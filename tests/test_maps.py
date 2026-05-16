"""
Maps router entegrasyon testleri.
heatmap.generate mock'lanır — GEE bağlantısı kurulmaz.
"""

import io
import sys
import types
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds
from rasterio.crs import CRS
from unittest.mock import patch
from fastapi.testclient import TestClient

# ee stub (test_api.py ile aynı pattern)
ee_stub = types.ModuleType("ee")
ee_stub.Initialize = lambda **kw: None
ee_stub.Number = lambda x: type("N", (), {"getInfo": lambda self: x})()
sys.modules.setdefault("ee", ee_stub)


def _make_tiff_bytes(score=70.0) -> bytes:
    """Geçerli bir in-memory GeoTIFF döndür."""
    data = np.full((8, 8), score, dtype="float32")
    buf = io.BytesIO()
    with rasterio.open(
        buf, "w",
        driver="GTiff", height=8, width=8,
        count=1, dtype="float32",
        crs=CRS.from_epsg(4326),
        transform=from_bounds(30.0, 36.0, 31.0, 37.0, 8, 8),
        nodata=-9999.0,
    ) as dst:
        dst.write(data, 1)
    buf.seek(0)
    return buf.read()


TIFF_BYTES = _make_tiff_bytes()
EMPTY_CONSTRAINTS = '{"type":"FeatureCollection","features":[]}'
GENERATE_RETURN = (TIFF_BYTES, EMPTY_CONSTRAINTS)

MAP_PAYLOAD = {
    "geom": {
        "type": "Polygon",
        "coordinates": [[[30.0, 36.0], [31.0, 36.0], [31.0, 37.0],
                          [30.0, 37.0], [30.0, 36.0]]],
    },
    "resolution_m": 500,
    "panel_tech": "mono",
    "tracking": "fixed",
    "country_code": "TR",
    "name": "Test Heatmap",
}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    maps_dir = str(tmp_path_factory.mktemp("maps"))
    with (
        patch("app.services.heatmap.generate", return_value=GENERATE_RETURN),
        patch("app.config.settings.maps_data_dir", maps_dir, create=True),
    ):
        from app.main import app
        from app.config import settings
        settings.maps_data_dir = maps_dir
        yield TestClient(app)


@pytest.fixture
def auth_headers():
    from app.auth import create_access_token
    from app.config import settings
    token = create_access_token(sub=settings.api_username)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def done_map_id(client, auth_headers, tmp_path_factory):
    """Tamamlanmış bir harita job'ı oluştur."""
    with patch("app.services.heatmap.generate", return_value=GENERATE_RETURN):
        r = client.post("/api/v1/maps", json=MAP_PAYLOAD, headers=auth_headers)
    assert r.status_code == 202
    return r.json()["id"]


# ─── POST /api/v1/maps ────────────────────────────────────────────────────────

class TestCreateMap:
    def test_returns_202(self, client, auth_headers):
        with patch("app.services.heatmap.generate", return_value=GENERATE_RETURN):
            r = client.post("/api/v1/maps", json=MAP_PAYLOAD, headers=auth_headers)
        assert r.status_code == 202

    def test_returns_job_id(self, client, auth_headers):
        with patch("app.services.heatmap.generate", return_value=GENERATE_RETURN):
            r = client.post("/api/v1/maps", json=MAP_PAYLOAD, headers=auth_headers)
        assert "id" in r.json()

    def test_requires_auth(self, client):
        r = client.post("/api/v1/maps", json=MAP_PAYLOAD)
        assert r.status_code == 401

    def test_invalid_geom_type_rejected(self, client, auth_headers):
        bad = {**MAP_PAYLOAD, "geom": {"type": "Point", "coordinates": [30.0, 36.0]}}
        r = client.post("/api/v1/maps", json=bad, headers=auth_headers)
        assert r.status_code == 422

    def test_resolution_out_of_range(self, client, auth_headers):
        bad = {**MAP_PAYLOAD, "resolution_m": 50}
        r = client.post("/api/v1/maps", json=bad, headers=auth_headers)
        assert r.status_code == 422

    def test_missing_geom_rejected(self, client, auth_headers):
        bad = {"resolution_m": 250, "panel_tech": "mono"}
        r = client.post("/api/v1/maps", json=bad, headers=auth_headers)
        assert r.status_code == 422


# ─── GET /api/v1/maps/{id} ────────────────────────────────────────────────────

class TestGetMap:
    def test_get_existing_job(self, client, auth_headers, done_map_id):
        r = client.get(f"/api/v1/maps/{done_map_id}", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["id"] == done_map_id

    def test_not_found(self, client, auth_headers):
        r = client.get("/api/v1/maps/nonexistent-id", headers=auth_headers)
        assert r.status_code == 404

    def test_done_job_has_stats(self, client, auth_headers, done_map_id):
        r = client.get(f"/api/v1/maps/{done_map_id}", headers=auth_headers)
        data = r.json()
        if data["status"] == "done":
            assert data["stats"] is not None
            assert "score_min" in data["stats"]

    def test_done_job_has_tile_url(self, client, auth_headers, done_map_id):
        r = client.get(f"/api/v1/maps/{done_map_id}", headers=auth_headers)
        data = r.json()
        if data["status"] == "done":
            assert data["tile_url_template"] is not None


# ─── GET /api/v1/boundaries/search ───────────────────────────────────────────

class TestBoundarySearch:
    def _mock_result(self):
        return [{
            "name": "Konya",
            "geojson": {"type": "Polygon", "coordinates": []},
            "bounds": [32.0, 37.0, 34.0, 39.0],
            "area_km2": 38257.0,
        }]

    def test_returns_results(self, client, auth_headers):
        with patch("app.services.boundaries.search", return_value=self._mock_result()):
            r = client.get("/api/v1/boundaries/search?q=Konya", headers=auth_headers)
        assert r.status_code == 200
        assert len(r.json()) == 1

    def test_result_schema(self, client, auth_headers):
        with patch("app.services.boundaries.search", return_value=self._mock_result()):
            r = client.get("/api/v1/boundaries/search?q=Konya", headers=auth_headers)
        item = r.json()[0]
        assert "name" in item
        assert "geojson" in item
        assert "bounds" in item
        assert "area_km2" in item

    def test_not_found_returns_404(self, client, auth_headers):
        with patch("app.services.boundaries.search", return_value=[]):
            r = client.get("/api/v1/boundaries/search?q=NoWhere12345", headers=auth_headers)
        assert r.status_code == 404

    def test_query_too_short_rejected(self, client, auth_headers):
        r = client.get("/api/v1/boundaries/search?q=K", headers=auth_headers)
        assert r.status_code == 422

    def test_requires_auth(self, client):
        r = client.get("/api/v1/boundaries/search?q=Konya")
        assert r.status_code == 401
