"""
FastAPI entegrasyon testleri — tüm dış servisler mock'lanır.
GEE, NASA POWER, OSM çağrısı gerçekleşmez.
Store: FakeRedis (conftest.py). Celery: senkron/eager mod.
"""

import sys
import types
import pytest

ee_stub = types.ModuleType("ee")
ee_stub.Initialize = lambda **kw: None
ee_stub.Number = lambda x: type("N", (), {"getInfo": lambda self: x})()
sys.modules.setdefault("ee", ee_stub)

from unittest.mock import patch
from fastapi.testclient import TestClient


TERRAIN_RESULT = {
    "slope_mean_pct":  3.0,
    "slope_mean_deg":  1.7,
    "slope_p90_pct":   5.0,
    "aspect_deg":      175.0,
    "aspect_score":    98.0,
    "shadow_loss_pct": 0.6,
    "shadow_score":    97.0,
    "lc_code":         60,
}
LEGAL_RESULT = {"score": 100, "hard_block": False,
                "reason": "ok", "country_code": "TR", "wdpa_checked": False}
GHI     = 1950.0
GRID_KM = 0.9
ROAD_KM = 0.4


@pytest.fixture(scope="module")
def client():
    with (
        patch("app.services.terrain.analyse",              return_value=TERRAIN_RESULT),
        patch("app.services.terrain.horizon_profile",      return_value={}),
        patch("app.services.solar.get_annual_ghi",         return_value=GHI),
        patch("app.services.downscale.terrain_correction", return_value=1.02),
        patch("app.services.grid.nearest_substation_km",   return_value=GRID_KM),
        patch("app.services.access.nearest_road_km",       return_value=ROAD_KM),
        patch("app.services.legal.check",                  return_value=LEGAL_RESULT),
        patch("app.services.financial.get_usd_tl",         return_value=38.0),
    ):
        from app.main import app
        yield TestClient(app)


@pytest.fixture
def token():
    """Admin-sub'lu token: M4 cost middleware bunu bypass yoluna sokar,
    test invariant'ları (job_id dönüşü, vb.) bozulmadan kalır."""
    from app.auth import create_access_token
    from app.config import settings
    return create_access_token(sub=settings.api_username)


@pytest.fixture
def headers(token):
    return {"Authorization": f"Bearer {token}"}


PAYLOAD = {"lat": 37.87, "lon": 32.49, "area_ha": 50.0,
           "country_code": "TR", "name": "Test Parsel"}


class TestHealth:
    def test_health_ok(self, client):
        r = client.get("/api/v1/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


class TestCreateAnalysis:
    def test_returns_202_and_job_id(self, client, headers):
        r = client.post("/api/v1/analyses", json=PAYLOAD, headers=headers)
        assert r.status_code == 202
        data = r.json()
        assert "id" in data
        assert data["status"] in ("pending", "running", "done")

    def test_requires_auth(self, client):
        r = client.post("/api/v1/analyses", json=PAYLOAD)
        assert r.status_code == 401

    def test_invalid_lat_rejected(self, client, headers):
        r = client.post("/api/v1/analyses",
                        json={"lat": 200.0, "lon": 32.0, "area_ha": 10.0},
                        headers=headers)
        assert r.status_code == 422

    def test_missing_area_rejected(self, client, headers):
        r = client.post("/api/v1/analyses",
                        json={"lat": 37.0, "lon": 32.0},
                        headers=headers)
        assert r.status_code == 422


class TestGetAnalysis:
    @pytest.fixture
    def job_id(self, client, headers):
        r = client.post("/api/v1/analyses", json=PAYLOAD, headers=headers)
        return r.json()["id"]

    def test_get_existing_job(self, client, headers, job_id):
        r = client.get(f"/api/v1/analyses/{job_id}", headers=headers)
        assert r.status_code == 200
        assert r.json()["id"] == job_id

    def test_get_nonexistent_returns_404(self, client, headers):
        r = client.get("/api/v1/analyses/does-not-exist", headers=headers)
        assert r.status_code == 404

    def test_get_requires_auth(self, client, job_id):
        r = client.get(f"/api/v1/analyses/{job_id}")
        assert r.status_code == 401

    def test_list_returns_array(self, client, headers):
        r = client.get("/api/v1/analyses", headers=headers)
        assert r.status_code == 200
        assert isinstance(r.json(), list)


class TestScoreEndpoint:
    @pytest.fixture
    def job_id(self, client, headers):
        r = client.post("/api/v1/analyses", json=PAYLOAD, headers=headers)
        return r.json()["id"]

    def test_score_endpoint_responds(self, client, headers, job_id):
        r = client.get(f"/api/v1/analyses/{job_id}/score", headers=headers)
        assert r.status_code == 200

    def test_score_contains_total_if_done(self, client, headers, job_id):
        r = client.get(f"/api/v1/analyses/{job_id}/score", headers=headers)
        body = r.json()
        if body.get("status") == "done":
            assert "total_score" in body
            assert 0 <= body["total_score"] <= 100


class TestHardBlock:
    def test_hard_block_zero_score(self, client, headers):
        hard_block = {"score": 0, "hard_block": True,
                      "reason": "Orman", "country_code": "TR", "wdpa_checked": False}
        with patch("app.services.legal.check", return_value=hard_block):
            r = client.post("/api/v1/analyses", json=PAYLOAD, headers=headers)
            job_id = r.json()["id"]

        r2 = client.get(f"/api/v1/analyses/{job_id}/score", headers=headers)
        body = r2.json()
        if body.get("status") == "done":
            assert body["total_score"] == 0
