"""Sprint 9 #5c — real Postgres proof of the credit FOR UPDATE invariant.

The SQLite race test in test_cost_middleware can only assert the weak
ledger invariant: StaticPool ignores `SELECT ... FOR UPDATE`, so balances
overshoot below zero there. This test runs against a real Postgres
(CI `services: postgres`) and asserts the *strong* guarantee production
relies on: row-level locking serialises concurrent charges so the balance
never goes negative and exactly the starting credits succeed.

Gated on a Postgres DATABASE_URL — skipped on the local SQLite suite, so
the fast default run is unaffected.
"""
import concurrent.futures
import os
import threading
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

_PG_URL = os.getenv("DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not _PG_URL.startswith("postgresql"),
    reason="Postgres DATABASE_URL not set — FOR UPDATE proof runs in CI only",
)


def test_for_update_serialises_concurrent_charges():
    import app.models  # noqa: F401 — register all tables on Base.metadata
    from app.db import Base
    from app.models.credit_transaction import REASON_ANALYSIS, CreditTransaction
    from app.models.user import User
    from app.services.credits import InsufficientCreditsError, charge_credits

    engine = create_engine(_PG_URL, future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as s:
        user = User(email=f"pgrace+{uuid.uuid4().hex[:8]}@example.com",
                    password_hash="x", credits=3)
        s.add(user)
        s.commit()
        uid = user.id

    outcomes = {"ok": 0, "insufficient": 0, "errored": 0}
    lock = threading.Lock()

    def _charge():
        try:
            with SessionLocal() as s:
                charge_credits(s, user_id=uid, amount=1,
                               reason=REASON_ANALYSIS, reference_id="pg-race")
                s.commit()
            key = "ok"
        except InsufficientCreditsError:
            key = "insufficient"
        except Exception:
            key = "errored"
        with lock:
            outcomes[key] += 1

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
            for f in [ex.submit(_charge) for _ in range(10)]:
                f.result()

        with SessionLocal() as s:
            final = s.get(User, uid)
            rows = (s.query(CreditTransaction)
                    .filter_by(user_id=uid, reason=REASON_ANALYSIS).all())

        # Strong invariant — only real row locking yields this:
        assert outcomes["errored"] == 0
        assert outcomes["ok"] == 3            # exactly the starting balance
        assert outcomes["insufficient"] == 7
        assert final.credits == 0            # never overshoots below zero
        assert len(rows) == 3
        assert all(r.balance_after >= 0 for r in rows)
    finally:
        with SessionLocal() as s:
            s.query(CreditTransaction).filter_by(user_id=uid).delete()
            s.query(User).filter_by(id=uid).delete()
            s.commit()
        engine.dispose()
