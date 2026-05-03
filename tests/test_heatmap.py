"""
Heatmap servisi testleri.
GEE ve dış servisler iç fonksiyon seviyesinde mock'lanır (_terrain_raster, _idw_ghi).
"""

import io
import numpy as np
import rasterio
from unittest.mock import patch

POLYGON = {
    "type": "Polygon",
    "coordinates": [[[30.0, 36.0], [31.0, 36.0], [31.0, 37.0],
                     [30.0, 37.0], [30.0, 36.0]]],
}
ROWS, COLS = 5, 5


def _slope(v=3.0):
    return np.full((ROWS, COLS), v, dtype=float)

def _aspect(v=180.0):
    return np.full((ROWS, COLS), v, dtype=float)

def _lc(v=60):
    return np.full((ROWS, COLS), v, dtype=int)

def _ghi(v=1950.0):
    return np.full((ROWS, COLS), v, dtype=float)


# ─── _utm_epsg ────────────────────────────────────────────────────────────────

class TestUtmEpsg:
    def test_northern(self):
        from app.services.heatmap import _utm_epsg
        epsg = _utm_epsg(37.87, 32.49)
        assert 32601 <= epsg <= 32660

    def test_southern(self):
        from app.services.heatmap import _utm_epsg
        epsg = _utm_epsg(-33.9, 25.0)
        assert 32701 <= epsg <= 32760

    def test_zone_value(self):
        from app.services.heatmap import _utm_epsg
        # lon=32.49 → zone = int((32.49+180)/6)+1 = 36
        assert _utm_epsg(37.87, 32.49) == 32636

    def test_equator(self):
        from app.services.heatmap import _utm_epsg
        assert _utm_epsg(0.0, 0.0) == 32631


# ─── Skor fonksiyonları ───────────────────────────────────────────────────────

class TestSlopeScore:
    def test_flat_full_score(self):
        from app.services.heatmap import _s_slope
        np.testing.assert_array_equal(_s_slope(np.array([0.0, 5.0])), [100.0, 100.0])

    def test_steep_zero(self):
        from app.services.heatmap import _s_slope
        assert _s_slope(np.array([20.0]))[0] == 0.0

    def test_linear_range(self):
        from app.services.heatmap import _s_slope
        v = _s_slope(np.array([10.0]))[0]
        assert 0 < v < 100

    def test_output_clipped(self):
        from app.services.heatmap import _s_slope
        result = _s_slope(np.array([-5.0, 50.0]))
        assert result.min() >= 0 and result.max() <= 100


class TestGhiScore:
    def test_high_full_score(self):
        from app.services.heatmap import _s_ghi
        assert _s_ghi(np.array([2100.0]))[0] == 100.0

    def test_below_min_zero(self):
        from app.services.heatmap import _s_ghi
        assert _s_ghi(np.array([1000.0]))[0] == 0.0

    def test_midpoint(self):
        from app.services.heatmap import _s_ghi
        v = _s_ghi(np.array([1600.0]))[0]
        assert 0 < v < 100


class TestDistScore:
    def test_near_full_score(self):
        from app.services.heatmap import _s_dist
        assert _s_dist(np.array([0.5]), 1.0, 30.0)[0] == 100.0

    def test_far_zero(self):
        from app.services.heatmap import _s_dist
        assert _s_dist(np.array([35.0]), 1.0, 30.0)[0] == 0.0

    def test_middle_range(self):
        from app.services.heatmap import _s_dist
        v = _s_dist(np.array([5.0]), 1.0, 30.0)[0]
        assert 0 < v < 100


# ─── generate ─────────────────────────────────────────────────────────────────

def _run_generate(**kwargs):
    with (
        patch("app.services.heatmap._terrain_raster",
              return_value=(_slope(), _aspect(), _lc())),
        patch("app.services.heatmap._idw_ghi", return_value=_ghi()),
        patch("app.services.heatmap.grid_svc.nearest_substation_km", return_value=0.9),
        patch("app.services.heatmap.access_svc.nearest_road_km", return_value=0.4),
    ):
        from app.services.heatmap import generate
        return generate(POLYGON, resolution_m=250, **kwargs)


class TestGenerate:
    def test_returns_bytes(self):
        result = _run_generate()
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_valid_geotiff(self):
        with rasterio.open(io.BytesIO(_run_generate())) as src:
            assert src.count == 1
            assert src.crs.to_epsg() == 4326
            assert src.dtypes[0] == "float32"

    def test_scores_in_range(self):
        with rasterio.open(io.BytesIO(_run_generate())) as src:
            data = src.read(1)
        valid = data[data != -9999.0]
        assert len(valid) > 0
        assert valid.min() >= 0
        assert valid.max() <= 100

    def test_hard_block_lc_zeroed(self):
        """Arazi kodu 80 (su) → tüm piksel skor=0."""
        with (
            patch("app.services.heatmap._terrain_raster",
                  return_value=(_slope(0.0), _aspect(), _lc(80))),
            patch("app.services.heatmap._idw_ghi", return_value=_ghi()),
            patch("app.services.heatmap.grid_svc.nearest_substation_km", return_value=0.9),
            patch("app.services.heatmap.access_svc.nearest_road_km", return_value=0.4),
        ):
            from app.services.heatmap import generate
            tiff = generate(POLYGON, resolution_m=250)

        with rasterio.open(io.BytesIO(tiff)) as src:
            data = src.read(1)
        valid = data[data != -9999.0]
        assert (valid == -1.0).all()

    def test_nodata_outside_polygon(self):
        """Polygon dışı → nodata=-9999."""
        result = _run_generate()
        with rasterio.open(io.BytesIO(result)) as src:
            assert src.nodata == -9999.0

    def test_grid_error_uses_fallback(self):
        """Grid servisi hata verse bile generate tamamlanmalı."""
        with (
            patch("app.services.heatmap._terrain_raster",
                  return_value=(_slope(), _aspect(), _lc())),
            patch("app.services.heatmap._idw_ghi", return_value=_ghi()),
            patch("app.services.heatmap.grid_svc.nearest_substation_km",
                  side_effect=RuntimeError("no grid")),
            patch("app.services.heatmap.access_svc.nearest_road_km", return_value=0.4),
        ):
            from app.services.heatmap import generate
            result = generate(POLYGON, resolution_m=250)
        assert isinstance(result, bytes)
