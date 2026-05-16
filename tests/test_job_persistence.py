"""Sprint 9 #1 — durable, user-scoped job persistence.

Guards the money-adjacent invariants the architecture review surfaced:

  * a job is owned by its creator (no cross-user read → no data leak),
  * `GET /api/v1/analyses` is scoped to the caller (eski global KEYS
    taraması herkesin job'unu döndürüyordu),
  * a completed job survives Redis TTL expiry (served from Postgres),
  * legacy/admin token still sees the old global store behaviour.

Eager Celery runs the real pipeline which fails without GEE creds in the
test env, so jobs land in the terminal `failed` state — that is enough to
exercise ownership, scoping and the durable-fallback path.
"""
from unittest.mock import MagicMock, patch

import app.store as store
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.models.credit_transaction import (
    REASON_ANALYSIS,
    REASON_REFUND,
    CreditTransaction,
)
from app.models.job_record import JobRecord
from app.models.user import SIGNUP_BONUS_CREDITS, User


# Same external stubs as test_cost_middleware — keep the eager Celery
# pipeline off the network so these persistence tests stay fast and
# deterministic (without stubs the real OSM Overpass call took ~6 min).
_TERRAIN = {
    "slope_mean_pct": 3.0, "slope_mean_deg": 1.7, "slope_p90_pct": 5.0,
    "aspect_deg": 175.0, "aspect_score": 98.0,
    "shadow_loss_pct": 0.6, "shadow_score": 97.0, "lc_code": 60,
}


@pytest.fixture(autouse=True, scope="module")
def stub_externals():
    with (
        patch("ee.Initialize"),
        patch("ee.Number", return_value=MagicMock(getInfo=lambda: 1)),
        patch("app.services.terrain.analyse", return_value=_TERRAIN),
        patch("app.services.terrain.horizon_profile", return_value={}),
        patch("app.services.solar.get_annual_ghi", return_value=1850.0),
        patch("app.services.downscale.terrain_correction", return_value=1.05),
        patch("app.services.grid.nearest_substation_km", return_value=2.5),
        patch("app.services.access.nearest_road_km", return_value=0.8),
        patch("app.services.legal.check",
              return_value={"score": 90, "hard_block": False,
                            "reason": None, "country_code": "DEFAULT",
                            "wdpa_checked": False}),
    ):
        yield


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def _register(client, email, password="supersecret"):
    r = client.post("/api/v1/auth/register",
                    json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


def _admin_headers():
    from app.auth import create_access_token
    return {"Authorization": f"Bearer {create_access_token(sub=settings.api_username)}"}


_BODY = {"lat": 37.0, "lon": 32.0, "area_ha": 10}


def _post_analysis(client, token):
    r = client.post("/api/v1/analyses", json=_BODY,
                     headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 202, r.text
    return r.json()["id"]


# ─── ownership / data-leak ────────────────────────────────────────────────────

def test_record_created_scoped_to_user(client, db_session_factory):
    token = _register(client, "owner@example.com")
    job_id = _post_analysis(client, token)

    with db_session_factory() as s:
        rec = s.get(JobRecord, job_id)
        assert rec is not None
        assert rec.kind == "analysis"
        assert rec.user_id is not None
        assert rec.params["lat"] == 37.0


def test_other_user_cannot_read_job(client):
    a_token = _register(client, "alice@example.com")
    b_token = _register(client, "bob@example.com")
    job_id = _post_analysis(client, a_token)

    # Bob must not see Alice's job — same 404 as a missing id (no leak).
    r = client.get(f"/api/v1/analyses/{job_id}",
                    headers={"Authorization": f"Bearer {b_token}"})
    assert r.status_code == 404

    r_own = client.get(f"/api/v1/analyses/{job_id}",
                        headers={"Authorization": f"Bearer {a_token}"})
    assert r_own.status_code == 200


def test_list_is_user_scoped(client):
    a_token = _register(client, "alice2@example.com")
    b_token = _register(client, "bob2@example.com")
    a_job = _post_analysis(client, a_token)
    b_job = _post_analysis(client, b_token)

    r = client.get("/api/v1/analyses",
                    headers={"Authorization": f"Bearer {a_token}"})
    assert r.status_code == 200
    ids = {item["id"] for item in r.json()}
    assert a_job in ids
    assert b_job not in ids


# ─── durability beyond Redis TTL ──────────────────────────────────────────────

def test_completed_job_survives_redis_expiry(client):
    token = _register(client, "durable@example.com")
    job_id = _post_analysis(client, token)
    headers = {"Authorization": f"Bearer {token}"}

    # First read promotes the terminal Redis state into Postgres.
    r1 = client.get(f"/api/v1/analyses/{job_id}", headers=headers)
    assert r1.status_code == 200
    assert r1.json()["status"] in ("failed", "done")

    # Simulate the 7-day Redis TTL elapsing.
    store._r().delete(store._key(job_id))
    assert store.get(job_id) is None

    # Still served — now from the durable row.
    r2 = client.get(f"/api/v1/analyses/{job_id}", headers=headers)
    assert r2.status_code == 200
    assert r2.json()["status"] == r1.json()["status"]


# ─── legacy/admin unchanged ───────────────────────────────────────────────────

# ─── fail → refund saga ───────────────────────────────────────────────────────

def _post_failing_analysis(client, token):
    """Force the eager pipeline to fail (override one stub to raise) so the
    job lands in `failed` after the credit was already debited."""
    with patch("app.services.solar.get_annual_ghi",
               side_effect=RuntimeError("boom")):
        return _post_analysis(client, token)


def test_failed_job_refunds_credit(client, db_session_factory):
    token = _register(client, "refund@example.com")
    job_id = _post_failing_analysis(client, token)
    headers = {"Authorization": f"Bearer {token}"}

    # Charged at request time.
    with db_session_factory() as s:
        assert s.query(User).one().credits == SIGNUP_BONUS_CREDITS - 1

    # Reading the failed job triggers the refund.
    r = client.get(f"/api/v1/analyses/{job_id}", headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "failed"

    with db_session_factory() as s:
        assert s.query(User).one().credits == SIGNUP_BONUS_CREDITS
        refunds = (s.query(CreditTransaction)
                   .filter_by(reason=REASON_REFUND, reference_id=job_id).all())
        assert len(refunds) == 1
        assert refunds[0].amount == 1


def test_refund_is_idempotent(client, db_session_factory):
    token = _register(client, "idem@example.com")
    job_id = _post_failing_analysis(client, token)
    headers = {"Authorization": f"Bearer {token}"}

    for _ in range(3):
        assert client.get(f"/api/v1/analyses/{job_id}",
                           headers=headers).status_code == 200

    with db_session_factory() as s:
        assert s.query(User).one().credits == SIGNUP_BONUS_CREDITS
        assert (s.query(CreditTransaction)
                .filter_by(reason=REASON_REFUND, reference_id=job_id)
                .count()) == 1


def test_done_promotion_does_not_refund(db_session_factory):
    """Unit-level: a job that completes successfully keeps its charge.
    Driven through _promote directly — the stubbed HTTP pipeline always
    ends in `failed`, so a `done` outcome can't be produced end-to-end."""
    from app.services import jobs
    from app.services.credits import charge_credits

    with db_session_factory() as s:
        u = User(email="happy@example.com", password_hash="x", credits=5)
        s.add(u)
        s.flush()
        uid = u.id
        charge_credits(s, user_id=uid, amount=1, reason=REASON_ANALYSIS,
                       reference_id="job-done-1")
        s.add(JobRecord(id="job-done-1", user_id=uid, kind="analysis",
                        status="pending"))
        s.commit()

    with db_session_factory() as s:
        rec = s.get(JobRecord, "job-done-1")
        jobs._promote(s, rec=rec, live={"status": "done", "result": {"ok": 1}})
        s.commit()

    with db_session_factory() as s:
        assert s.query(User).filter_by(id=uid).one().credits == 4
        assert (s.query(CreditTransaction)
                .filter_by(reason=REASON_REFUND, reference_id="job-done-1")
                .count()) == 0


def test_admin_token_uses_legacy_store_path(client):
    """Admin (no uid) keeps the global store view and can read any job even
    with no ownership row required."""
    admin = _admin_headers()
    r = client.post("/api/v1/analyses", json=_BODY, headers=admin)
    assert r.status_code == 202
    job_id = r.json()["id"]

    r_get = client.get(f"/api/v1/analyses/{job_id}", headers=admin)
    assert r_get.status_code == 200

    r_list = client.get("/api/v1/analyses", headers=admin)
    assert r_list.status_code == 200
