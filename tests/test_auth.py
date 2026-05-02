"""
JWT auth endpoint ve korumalı route testleri.
GEE gerektiren servisler stub'lanır — auth katmanı izole edilir.
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.config import settings


# ─── GEE + servis stub'ları (test başında uygulanır) ────────────────────────

@pytest.fixture(autouse=True, scope="module")
def stub_gee_and_services():
    """GEE, terrain ve solar stub'larını modül boyunca uygula."""
    fake_terrain = {
        "slope_mean_pct": 3.0, "slope_mean_deg": 1.7, "slope_p90_pct": 5.0,
        "aspect_deg": 175.0, "aspect_score": 98.0,
        "shadow_loss_pct": 0.6, "shadow_score": 97.0, "lc_code": 60,
    }
    with (
        patch("ee.Initialize"),
        patch("ee.Number", return_value=MagicMock(getInfo=lambda: 1)),
        patch("app.services.terrain.analyse", return_value=fake_terrain),
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


@pytest.fixture(scope="module")
def client():
    from app.main import app
    return TestClient(app)


@pytest.fixture(scope="module")
def valid_token(client):
    r = client.post("/api/v1/auth/token",
                    data={"username": settings.api_username,
                          "password": settings.api_password})
    return r.json()["access_token"]


# ─── /auth/token ─────────────────────────────────────────────────────────────

class TestLogin:
    def test_success_returns_token(self, client):
        r = client.post("/api/v1/auth/token",
                        data={"username": settings.api_username,
                              "password": settings.api_password})
        assert r.status_code == 200
        assert "access_token" in r.json()
        assert r.json()["token_type"] == "bearer"

    def test_token_is_valid_jwt(self, client):
        r = client.post("/api/v1/auth/token",
                        data={"username": settings.api_username,
                              "password": settings.api_password})
        parts = r.json()["access_token"].split(".")
        assert len(parts) == 3   # header.payload.signature

    def test_wrong_password_returns_401(self, client):
        r = client.post("/api/v1/auth/token",
                        data={"username": settings.api_username,
                              "password": "yanlis_sifre"})
        assert r.status_code == 401

    def test_wrong_username_returns_401(self, client):
        r = client.post("/api/v1/auth/token",
                        data={"username": "hacker", "password": settings.api_password})
        assert r.status_code == 401

    def test_missing_credentials_returns_422(self, client):
        r = client.post("/api/v1/auth/token")
        assert r.status_code == 422


# ─── Korumalı route'lar ───────────────────────────────────────────────────────

class TestProtectedRoutes:
    def test_analyses_without_token_returns_401(self, client):
        r = client.post("/api/v1/analyses",
                        json={"lat": 37.0, "lon": 32.0, "area_ha": 10})
        assert r.status_code == 401

    def test_analyses_with_valid_token_returns_202(self, client, valid_token):
        r = client.post("/api/v1/analyses",
                        json={"lat": 37.0, "lon": 32.0, "area_ha": 10},
                        headers={"Authorization": f"Bearer {valid_token}"})
        assert r.status_code == 202
        assert "id" in r.json()

    def test_analyses_with_invalid_token_returns_401(self, client):
        r = client.post("/api/v1/analyses",
                        json={"lat": 37.0, "lon": 32.0, "area_ha": 10},
                        headers={"Authorization": "Bearer invalid.jwt.token"})
        assert r.status_code == 401

    def test_list_analyses_requires_auth(self, client):
        r = client.get("/api/v1/analyses")
        assert r.status_code == 401

    def test_list_analyses_with_token(self, client, valid_token):
        r = client.get("/api/v1/analyses",
                       headers={"Authorization": f"Bearer {valid_token}"})
        assert r.status_code == 200

    def test_batch_without_token_returns_401(self, client):
        r = client.post("/api/v1/batch",
                        json={"locations": [{"lat": 37.0, "lon": 32.0}],
                              "area_ha": 10})
        assert r.status_code == 401

    def test_batch_with_valid_token_returns_202(self, client, valid_token):
        r = client.post("/api/v1/batch",
                        json={"locations": [{"lat": 37.0, "lon": 32.0}],
                              "area_ha": 10},
                        headers={"Authorization": f"Bearer {valid_token}"})
        assert r.status_code == 202


# ─── Health endpoint herkese açık ─────────────────────────────────────────────

class TestPublicEndpoints:
    def test_health_is_public(self, client):
        r = client.get("/api/v1/health")
        assert r.status_code != 401

    def test_health_returns_ok(self, client):
        r = client.get("/api/v1/health")
        assert r.json()["status"] == "ok"


# ─── Token son kullanma tarihi ─────────────────────────────────────────────────

class TestTokenExpiry:
    def test_expired_token_returns_401(self, client):
        from app.auth import ALGORITHM
        from jose import jwt

        expired = jwt.encode(
            {"sub": settings.api_username,
             "exp": datetime.now(timezone.utc) - timedelta(hours=1)},
            settings.secret_key,
            algorithm=ALGORITHM,
        )
        r = client.post("/api/v1/analyses",
                        json={"lat": 37.0, "lon": 32.0, "area_ha": 10},
                        headers={"Authorization": f"Bearer {expired}"})
        assert r.status_code == 401

    def test_token_without_sub_returns_401(self, client):
        from app.auth import ALGORITHM
        from jose import jwt
        from datetime import datetime, timezone, timedelta

        bad = jwt.encode(
            {"exp": datetime.now(timezone.utc) + timedelta(hours=1)},
            settings.secret_key,
            algorithm=ALGORITHM,
        )
        r = client.post("/api/v1/analyses",
                        json={"lat": 37.0, "lon": 32.0, "area_ha": 10},
                        headers={"Authorization": f"Bearer {bad}"})
        assert r.status_code == 401
