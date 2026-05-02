"""
Batch analiz endpoint entegrasyon testleri.
Tüm dış servisler mock'lanır. Store FakeRedis, Celery senkron çalışır (conftest.py).
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

TERRAIN = {
    "slope_mean_pct": 3.0, "slope_mean_deg": 1.7, "slope_p90_pct": 5.0,
    "aspect_deg": 175.0, "aspect_score": 98.0,
    "shadow_loss_pct": 0.6, "shadow_score": 97.0, "lc_code": 60,
}
LEGAL  = {"score": 100, "hard_block": False, "reason": "ok",
           "country_code": "TR", "wdpa_checked": False}
GHI    = 1950.0
GRID   = 0.9
ROAD   = 0.4


@pytest.fixture(scope="module")
def client():
    with (
        patch("app.services.terrain.analyse",            return_value=TERRAIN),
        patch("app.services.terrain.horizon_profile",    return_value={}),
        patch("app.services.solar.get_annual_ghi",       return_value=GHI),
        patch("app.services.downscale.terrain_correction", return_value=1.02),
        patch("app.services.grid.nearest_substation_km", return_value=GRID),
        patch("app.services.access.nearest_road_km",     return_value=ROAD),
        patch("app.services.legal.check",                return_value=LEGAL),
        patch("app.services.financial.get_usd_tl",       return_value=38.0),
    ):
        from app.main import app
        yield TestClient(app)


@pytest.fixture
def token(client):
    from app.auth import create_access_token
    return create_access_token(sub="test")


@pytest.fixture
def headers(token):
    return {"Authorization": f"Bearer {token}"}


BATCH_PAYLOAD = {
    "locations": [
        {"lat": 37.73, "lon": 33.55, "name": "Karapinar"},
        {"lat": 36.87, "lon": 39.03, "name": "Harran"},
        {"lat": 38.37, "lon": 33.97, "name": "Aksaray"},
    ],
    "area_ha": 100,
    "panel_tech": "mono",
    "tracking": "fixed",
    "country_code": "TR",
}


class TestCreateBatch:
    def test_returns_202_and_id(self, client, headers):
        r = client.post("/api/v1/batch", json=BATCH_PAYLOAD, headers=headers)
        assert r.status_code == 202
        data = r.json()
        assert "id" in data
        assert data["total_locations"] == 3

    def test_without_auth_returns_401(self, client):
        r = client.post("/api/v1/batch", json=BATCH_PAYLOAD)
        assert r.status_code == 401

    def test_empty_locations_rejected(self, client, headers):
        r = client.post("/api/v1/batch",
                        json={**BATCH_PAYLOAD, "locations": []},
                        headers=headers)
        assert r.status_code == 422

    def test_too_many_locations_rejected(self, client, headers):
        locs = [{"lat": 37.0, "lon": 32.0} for _ in range(51)]
        r = client.post("/api/v1/batch",
                        json={**BATCH_PAYLOAD, "locations": locs},
                        headers=headers)
        assert r.status_code == 422

    def test_invalid_lat_rejected(self, client, headers):
        r = client.post("/api/v1/batch",
                        json={**BATCH_PAYLOAD, "locations": [{"lat": 200.0, "lon": 32.0}]},
                        headers=headers)
        assert r.status_code == 422


class TestGetBatch:
    @pytest.fixture
    def batch_id(self, client, headers):
        r = client.post("/api/v1/batch", json=BATCH_PAYLOAD, headers=headers)
        return r.json()["id"]

    def test_get_existing(self, client, headers, batch_id):
        r = client.get(f"/api/v1/batch/{batch_id}", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == batch_id
        assert data["total_locations"] == 3

    def test_get_nonexistent(self, client, headers):
        r = client.get("/api/v1/batch/does-not-exist", headers=headers)
        assert r.status_code == 404

    def test_get_without_auth_returns_401(self, client, batch_id):
        r = client.get(f"/api/v1/batch/{batch_id}")
        assert r.status_code == 401

    def test_done_results_sorted_by_score(self, client, headers, batch_id):
        r = client.get(f"/api/v1/batch/{batch_id}", headers=headers)
        data = r.json()
        if data["status"] == "done" and data["results"]:
            scores = [res["total_score"] for res in data["results"]]
            assert scores == sorted(scores, reverse=True)

    def test_results_have_rank(self, client, headers, batch_id):
        r = client.get(f"/api/v1/batch/{batch_id}", headers=headers)
        data = r.json()
        if data["status"] == "done":
            ranks = [res["rank"] for res in data["results"]]
            assert ranks == list(range(1, len(ranks) + 1))

    def test_scores_in_valid_range(self, client, headers, batch_id):
        r = client.get(f"/api/v1/batch/{batch_id}", headers=headers)
        data = r.json()
        if data["status"] == "done":
            for res in data["results"]:
                assert 0 <= res["total_score"] <= 100


class TestHardBlockInBatch:
    def test_hard_block_location_scores_zero(self, client, headers):
        hard_block_legal = {
            "score": 0, "hard_block": True,
            "reason": "Orman — yasak", "country_code": "TR", "wdpa_checked": False,
        }
        with patch("app.services.legal.check", return_value=hard_block_legal):
            r = client.post("/api/v1/batch", json={
                **BATCH_PAYLOAD,
                "locations": [{"lat": 37.73, "lon": 33.55, "name": "Bloke"}],
            }, headers=headers)
            batch_id = r.json()["id"]

        r2 = client.get(f"/api/v1/batch/{batch_id}", headers=headers)
        data = r2.json()
        if data["status"] == "done" and data["results"]:
            assert data["results"][0]["total_score"] == 0
