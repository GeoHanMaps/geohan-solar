"""Sprint 9 M2: multi-user auth — register, login (DB user + legacy admin), /me.

DB layer is exercised against in-memory SQLite via a get_session override;
the User/CreditTransaction models use SQLAlchemy 2.0's portable Uuid so the
same models that run on Postgres in prod also create cleanly on SQLite."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_session
# import models so their tables register on Base.metadata
from app.models import user as _user_model  # noqa: F401
from app.models import credit_transaction as _ct_model  # noqa: F401


# ─── In-memory DB fixture ────────────────────────────────────────────────────

@pytest.fixture(autouse=True, scope="module")
def stub_gee():
    """Lifespan calls ee.Initialize; stub it so the TestClient can start."""
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
def db_session(db_engine):
    TestSession = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    with TestSession() as session:
        yield session


@pytest.fixture
def client(db_engine):
    """TestClient with both get_session and session_or_none pointing at the
    in-memory DB so /register, /token, /me all share the same store."""
    from app.main import app
    from app.routers.auth import session_or_none

    TestSession = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)

    def _override_session():
        with TestSession() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[session_or_none] = _override_session
    yield TestClient(app)
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(session_or_none, None)


# ─── /auth/register ──────────────────────────────────────────────────────────

class TestRegister:
    def test_success_returns_201_and_token(self, client):
        r = client.post("/api/v1/auth/register",
                        json={"email": "alice@example.com", "password": "supersecret"})
        assert r.status_code == 201
        body = r.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"

    def test_persists_user_with_signup_bonus(self, client, db_engine):
        r = client.post("/api/v1/auth/register",
                        json={"email": "bob@example.com", "password": "supersecret"})
        assert r.status_code == 201

        from app.models.user import User, SIGNUP_BONUS_CREDITS

        TestSession = sessionmaker(bind=db_engine)
        with TestSession() as s:
            user = s.query(User).filter_by(email="bob@example.com").one()
            assert user.credits == SIGNUP_BONUS_CREDITS
            assert user.password_hash != "supersecret"
            assert user.password_hash.startswith("$2")

    def test_records_signup_bonus_transaction(self, client, db_engine):
        client.post("/api/v1/auth/register",
                    json={"email": "tx@example.com", "password": "supersecret"})

        from app.models.credit_transaction import CreditTransaction, REASON_SIGNUP_BONUS

        TestSession = sessionmaker(bind=db_engine)
        with TestSession() as s:
            tx = s.query(CreditTransaction).filter_by(reason=REASON_SIGNUP_BONUS).one()
            assert tx.amount == 5
            assert tx.balance_after == 5

    def test_duplicate_email_returns_409(self, client):
        payload = {"email": "dup@example.com", "password": "supersecret"}
        client.post("/api/v1/auth/register", json=payload)
        r = client.post("/api/v1/auth/register", json=payload)
        assert r.status_code == 409

    def test_short_password_returns_422(self, client):
        r = client.post("/api/v1/auth/register",
                        json={"email": "x@example.com", "password": "short"})
        assert r.status_code == 422

    def test_email_normalised_to_lowercase(self, client, db_engine):
        client.post("/api/v1/auth/register",
                    json={"email": "Mixed@Example.com", "password": "supersecret"})
        from app.models.user import User
        TestSession = sessionmaker(bind=db_engine)
        with TestSession() as s:
            assert s.query(User).filter_by(email="mixed@example.com").one_or_none()

    def test_invalid_email_returns_422(self, client):
        r = client.post("/api/v1/auth/register",
                        json={"email": "no-at-sign", "password": "supersecret"})
        assert r.status_code == 422


# ─── /auth/token (DB user + legacy admin) ────────────────────────────────────

class TestLogin:
    def test_db_user_login(self, client):
        client.post("/api/v1/auth/register",
                    json={"email": "login@example.com", "password": "supersecret"})
        r = client.post("/api/v1/auth/token",
                        data={"username": "login@example.com", "password": "supersecret"})
        assert r.status_code == 200
        assert "access_token" in r.json()

    def test_db_user_wrong_password(self, client):
        client.post("/api/v1/auth/register",
                    json={"email": "pw@example.com", "password": "supersecret"})
        r = client.post("/api/v1/auth/token",
                        data={"username": "pw@example.com", "password": "wrongpass"})
        assert r.status_code == 401

    def test_legacy_admin_still_works(self, client):
        r = client.post("/api/v1/auth/token",
                        data={"username": settings.api_username,
                              "password": settings.api_password})
        assert r.status_code == 200
        assert "access_token" in r.json()

    def test_unknown_user_returns_401(self, client):
        r = client.post("/api/v1/auth/token",
                        data={"username": "ghost@example.com", "password": "supersecret"})
        assert r.status_code == 401


# ─── /auth/me ────────────────────────────────────────────────────────────────

class TestMe:
    def test_returns_user_info(self, client):
        reg = client.post("/api/v1/auth/register",
                          json={"email": "me@example.com", "password": "supersecret"})
        token = reg.json()["access_token"]
        r = client.get("/api/v1/auth/me",
                       headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        body = r.json()
        assert body["email"] == "me@example.com"
        assert body["credits"] == 5
        assert "id" in body

    def test_legacy_admin_token_rejected_on_me(self, client):
        """Admin (env-based) has no DB row → /me must 401 even with a
        valid legacy token. DB-bound endpoints require real user identity."""
        tok = client.post("/api/v1/auth/token",
                          data={"username": settings.api_username,
                                "password": settings.api_password}).json()["access_token"]
        r = client.get("/api/v1/auth/me",
                       headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 401

    def test_no_token_returns_401(self, client):
        r = client.get("/api/v1/auth/me")
        assert r.status_code == 401
