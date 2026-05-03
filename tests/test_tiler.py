"""
XYZ tile servisi — saf hesaplama, dış bağımlılık yok.
"""

import io
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds
from rasterio.crs import CRS

from app.services.tiler import (
    _tile_bounds, _score_rgba, _empty_tile, get_tile, _TILE_SIZE,
)


def _make_tiff(path: str, minx=30.0, miny=36.0, maxx=31.0, maxy=37.0,
               score=75.0, nodata=-9999.0):
    data = np.full((10, 10), score, dtype="float32")
    with rasterio.open(
        path, "w",
        driver="GTiff", height=10, width=10,
        count=1, dtype="float32",
        crs=CRS.from_epsg(4326),
        transform=from_bounds(minx, miny, maxx, maxy, 10, 10),
        nodata=nodata,
    ) as dst:
        dst.write(data, 1)


# ─── _tile_bounds ─────────────────────────────────────────────────────────────

class TestTileBounds:
    def test_zoom0_covers_world(self):
        w, s, e, n = _tile_bounds(0, 0, 0)
        assert w == pytest.approx(-180, abs=0.1)
        assert e == pytest.approx(180, abs=0.1)
        assert s < -80
        assert n > 80

    def test_zoom1_left_half(self):
        w, s, e, n = _tile_bounds(1, 0, 0)
        assert w == pytest.approx(-180, abs=0.1)
        assert e == pytest.approx(0, abs=0.1)
        assert n > 0

    def test_north_greater_than_south(self):
        _, s, _, n = _tile_bounds(8, 140, 90)
        assert n > s

    def test_east_greater_than_west(self):
        w, _, e, _ = _tile_bounds(8, 140, 90)
        assert e > w


# ─── _score_rgba ──────────────────────────────────────────────────────────────

class TestScoreRgba:
    def test_zero_is_white(self):
        norm = np.array([[0.0]])
        valid = np.array([[True]])
        rgba = _score_rgba(norm, valid)
        assert rgba[0, 0, 0] == 255   # R = 255
        assert rgba[0, 0, 1] == 255   # G = 255
        assert rgba[0, 0, 2] == 255   # B = 255
        assert rgba[0, 0, 3] > 0      # görünür

    def test_half_is_yellow(self):
        norm = np.array([[0.5]])
        valid = np.array([[True]])
        rgba = _score_rgba(norm, valid)
        assert rgba[0, 0, 0] == 255   # R = 255
        assert rgba[0, 0, 1] == 255   # G = 255
        assert rgba[0, 0, 2] == 0     # B = 0

    def test_one_is_green(self):
        norm = np.array([[1.0]])
        valid = np.array([[True]])
        rgba = _score_rgba(norm, valid)
        assert rgba[0, 0, 0] == 0     # R = 0
        assert rgba[0, 0, 1] == 200   # G = 200
        assert rgba[0, 0, 2] == 0     # B = 0

    def test_invalid_pixel_transparent(self):
        norm = np.array([[0.5]])
        valid = np.array([[False]])
        rgba = _score_rgba(norm, valid)
        assert rgba[0, 0, 3] == 0

    def test_output_shape(self):
        norm = np.zeros((4, 4))
        valid = np.ones((4, 4), dtype=bool)
        rgba = _score_rgba(norm, valid)
        assert rgba.shape == (4, 4, 4)


# ─── _empty_tile ──────────────────────────────────────────────────────────────

class TestEmptyTile:
    def test_returns_png_bytes(self):
        data = _empty_tile()
        assert isinstance(data, bytes)
        assert data[:4] == b'\x89PNG'

    def test_correct_size(self):
        from PIL import Image
        img = Image.open(io.BytesIO(_empty_tile()))
        assert img.size == (_TILE_SIZE, _TILE_SIZE)

    def test_fully_transparent(self):
        from PIL import Image
        img = Image.open(io.BytesIO(_empty_tile())).convert("RGBA")
        assert np.array(img)[:, :, 3].max() == 0


# ─── get_tile ─────────────────────────────────────────────────────────────────

class TestGetTile:
    def test_out_of_bounds_returns_transparent(self, tmp_path):
        tiff = str(tmp_path / "test.tif")
        _make_tiff(tiff, minx=30.0, miny=36.0, maxx=31.0, maxy=37.0, score=75.0)
        # z=1 x=0 y=0 → -180..0 lon — GeoTIFF kapsam dışı
        result = get_tile(tiff, 1, 0, 0)
        from PIL import Image
        img = Image.open(io.BytesIO(result)).convert("RGBA")
        assert np.array(img)[:, :, 3].max() == 0

    def test_in_bounds_returns_png(self, tmp_path):
        tiff = str(tmp_path / "test.tif")
        _make_tiff(tiff, minx=30.0, miny=36.0, maxx=31.0, maxy=37.0, score=75.0)
        # z=8 x=149 y=99 → west≈29.5, east≈30.9, south≈36.6, north≈37.6 (örtüşür)
        result = get_tile(tiff, 8, 149, 99)
        assert isinstance(result, bytes)
        assert result[:4] == b'\x89PNG'

    def test_in_bounds_has_colored_pixels(self, tmp_path):
        tiff = str(tmp_path / "test.tif")
        _make_tiff(tiff, minx=30.0, miny=36.0, maxx=31.0, maxy=37.0, score=75.0)
        from PIL import Image
        # z=8 x=149 y=99 → GeoTIFF bölgesiyle örtüşür
        result = get_tile(tiff, 8, 149, 99)
        img = Image.open(io.BytesIO(result)).convert("RGBA")
        arr = np.array(img)
        assert arr[:, :, 3].max() > 0

    def test_nodata_pixels_transparent(self, tmp_path):
        tiff = str(tmp_path / "nodata.tif")
        _make_tiff(tiff, minx=30.0, miny=36.0, maxx=31.0, maxy=37.0, score=-9999.0)
        result = get_tile(tiff, 8, 149, 99)
        from PIL import Image
        img = Image.open(io.BytesIO(result)).convert("RGBA")
        arr = np.array(img)
        assert arr[:, :, 3].max() == 0
