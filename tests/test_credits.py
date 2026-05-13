"""Sprint 9 M3: credit ledger service + balance/history endpoints.

The service is exercised directly against the SQLite session (the same
fixture pattern as test_register.py). Concurrency / FOR UPDATE coverage
is deferred to M4's Postgres-backed integration tests — SQLite doesn't
serialise row reads, so race tests here would be vacuous."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.models import user as _user_model  # noqa: F401
from app.models import credit_transaction as _ct_model  # noqa: F401
from app.models.credit_transaction import (
    REASON_ANALYSIS,
    REASON_PURCHASE,
    REASON_REFUND,
)
from app.models.user import SIGNUP_BONUS_CREDITS
from app.services.credits import (
    InsufficientCreditsError,
    add_credits,
    charge_credits,
)


# ─── DB + client fixtures (parallel to test_register.py) ────────────────────

@pytest.fixture(autouse=True, scope="module")
def stub_gee():
    with (
        patch("ee.Initialize"),
        patch("ee.Number", return_value=MagicMock(getInfo=lambda: 1)),
    ):
        yield


@pytest.fixture
def db_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(db_engine):
    return sessionmaker(bind=db_engine, autoflush=False, autocommit=False)


@pytest.fixture
def client(db_engine, session_factory):
    from app.main import app
    from app.routers.auth import session_or_none

    def _override():
        with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    app.dependency_overrides[session_or_none] = _override
    yield TestClient(app)
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(session_or_none, None)


def _register(client, email="user@example.com", password="supersecret"):
    r = client.post("/api/v1/auth/register",
                    json={"email": email, "password": password})
    assert r.status_code == 201
    return r.json()["access_token"]


# ─── charge_credits / add_credits service ────────────────────────────────────

class TestChargeCredits:
    def test_deducts_balance(self, client, session_factory):
        _register(client)
        from app.models.user import User

        with session_factory() as s:
            user = s.query(User).one()
            charge_credits(s, user_id=user.id, amount=2, reason=REASON_ANALYSIS)
            s.commit()
            s.refresh(user)
            assert user.credits == SIGNUP_BONUS_CREDITS - 2

    def test_records_transaction(self, client, session_factory):
        _register(client)
        from app.models.user import User
        from app.models.credit_transaction import CreditTransaction

        with session_factory() as s:
            user = s.query(User).one()
            charge_credits(s, user_id=user.id, amount=1,
                           reason=REASON_ANALYSIS, reference_id="job-123")
            s.commit()
            txs = (s.query(CreditTransaction)
                   .filter_by(reason=REASON_ANALYSIS).all())
            assert len(txs) == 1
            assert txs[0].amount == -1
            assert txs[0].balance_after == SIGNUP_BONUS_CREDITS - 1
            assert txs[0].reference_id == "job-123"

    def test_insufficient_raises_and_rolls_back(self, client, session_factory):
        _register(client)
        from app.models.user import User
        from app.models.credit_transaction import CreditTransaction

        with session_factory() as s:
            user = s.query(User).one()
            with pytest.raises(InsufficientCreditsError) as exc:
                charge_credits(s, user_id=user.id, amount=999,
                               reason=REASON_ANALYSIS)
            assert exc.value.required == 999
            assert exc.value.available == SIGNUP_BONUS_CREDITS
            s.rollback()

        with session_factory() as s:
            user = s.query(User).one()
            assert user.credits == SIGNUP_BONUS_CREDITS
            assert s.query(CreditTransaction).count() == 1  # only signup bonus

    def test_zero_or_negative_amount_rejected(self, client, session_factory):
        _register(client)
        from app.models.user import User
        with session_factory() as s:
            user = s.query(User).one()
            with pytest.raises(ValueError):
                charge_credits(s, user_id=user.id, amount=0,
                               reason=REASON_ANALYSIS)
            with pytest.raises(ValueError):
                charge_credits(s, user_id=user.id, amount=-3,
                               reason=REASON_ANALYSIS)


class TestAddCredits:
    def test_increments_balance(self, client, session_factory):
        _register(client)
        from app.models.user import User
        with session_factory() as s:
            user = s.query(User).one()
            add_credits(s, user_id=user.id, amount=50,
                        reason=REASON_PURCHASE, reference_id="stripe_cs_x")
            s.commit()
            s.refresh(user)
            assert user.credits == SIGNUP_BONUS_CREDITS + 50

    def test_records_positive_transaction(self, client, session_factory):
        _register(client)
        from app.models.user import User
        from app.models.credit_transaction import CreditTransaction
        with session_factory() as s:
            user = s.query(User).one()
            add_credits(s, user_id=user.id, amount=10, reason=REASON_REFUND)
            s.commit()
            tx = (s.query(CreditTransaction)
                  .filter_by(reason=REASON_REFUND).one())
            assert tx.amount == 10
            assert tx.balance_after == SIGNUP_BONUS_CREDITS + 10

    def test_zero_rejected(self, client, session_factory):
        _register(client)
        from app.models.user import User
        with session_factory() as s:
            user = s.query(User).one()
            with pytest.raises(ValueError):
                add_credits(s, user_id=user.id, amount=0,
                            reason=REASON_PURCHASE)


# ─── /credits/balance + /credits/history endpoints ──────────────────────────

class TestBalanceEndpoint:
    def test_returns_signup_balance(self, client):
        token = _register(client)
        r = client.get("/api/v1/credits/balance",
                       headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        body = r.json()
        assert body["credits"] == SIGNUP_BONUS_CREDITS
        assert body["user_id"]

    def test_requires_auth(self, client):
        r = client.get("/api/v1/credits/balance")
        assert r.status_code == 401

    def test_legacy_admin_rejected(self, client):
        from app.config import settings
        tok = client.post("/api/v1/auth/token",
                          data={"username": settings.api_username,
                                "password": settings.api_password}).json()["access_token"]
        r = client.get("/api/v1/credits/balance",
                       headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 401


class TestHistoryEndpoint:
    def test_returns_signup_bonus_row(self, client):
        token = _register(client)
        r = client.get("/api/v1/credits/history",
                       headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert len(body["items"]) == 1
        item = body["items"][0]
        assert item["amount"] == SIGNUP_BONUS_CREDITS
        assert item["balance_after"] == SIGNUP_BONUS_CREDITS
        assert item["reason"] == "signup_bonus"

    def test_includes_subsequent_charges(self, client, session_factory):
        token = _register(client)
        from app.models.user import User
        with session_factory() as s:
            user = s.query(User).one()
            charge_credits(s, user_id=user.id, amount=2,
                           reason=REASON_ANALYSIS, reference_id="job-a")
            add_credits(s, user_id=user.id, amount=10, reason=REASON_PURCHASE)
            s.commit()

        r = client.get("/api/v1/credits/history",
                       headers={"Authorization": f"Bearer {token}"})
        body = r.json()
        assert body["total"] == 3
        # newest first
        reasons = [it["reason"] for it in body["items"]]
        assert reasons[0] == REASON_PURCHASE
        assert reasons[1] == REASON_ANALYSIS

    def test_pagination(self, client, session_factory):
        token = _register(client)
        from app.models.user import User
        with session_factory() as s:
            user = s.query(User).one()
            for i in range(5):
                add_credits(s, user_id=user.id, amount=1,
                            reason=REASON_PURCHASE, reference_id=f"p{i}")
            s.commit()

        r = client.get("/api/v1/credits/history?limit=2&offset=0",
                       headers={"Authorization": f"Bearer {token}"})
        body = r.json()
        assert body["total"] == 6
        assert len(body["items"]) == 2

        r2 = client.get("/api/v1/credits/history?limit=2&offset=4",
                        headers={"Authorization": f"Bearer {token}"})
        assert len(r2.json()["items"]) == 2

    def test_requires_auth(self, client):
        r = client.get("/api/v1/credits/history")
        assert r.status_code == 401

    def test_history_isolated_per_user(self, client, session_factory):
        tok_a = _register(client, email="a@example.com")
        tok_b = _register(client, email="b@example.com")

        from app.models.user import User
        with session_factory() as s:
            user_a = s.query(User).filter_by(email="a@example.com").one()
            charge_credits(s, user_id=user_a.id, amount=2,
                           reason=REASON_ANALYSIS)
            s.commit()

        r_a = client.get("/api/v1/credits/history",
                         headers={"Authorization": f"Bearer {tok_a}"})
        r_b = client.get("/api/v1/credits/history",
                         headers={"Authorization": f"Bearer {tok_b}"})
        assert r_a.json()["total"] == 2  # signup + analysis
        assert r_b.json()["total"] == 1  # signup only
