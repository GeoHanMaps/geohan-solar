"""
XYZ tile server — GeoTIFF'ten PNG tile üretir.
Renk skalası: beyaz (0) → sarı (50) → yeşil (100).
Hard block (score=-1): saf kırmızı, siyah kenarlık.
"""

import io
import math
import numpy as np
import rasterio
import rasterio.windows
from rasterio.enums import Resampling
from PIL import Image

_TILE_SIZE = 256
_NODATA = -9999.0


def _tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """XYZ → WGS84 bbox (west, south, east, north)."""
    n = 2 ** z
    west  = x       / n * 360 - 180
    east  = (x + 1) / n * 360 - 180
    lat_n = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 *  y      / n))))
    lat_s = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return west, lat_s, east, lat_n


def _score_rgba(norm: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """0-1 → RGBA (uint8). Beyaz=0, Sarı=0.5, Yeşil=1."""
    n = np.clip(norm, 0.0, 1.0)
    # Beyaz→Sarı: B 255→0, RG sabit 255
    # Sarı→Yeşil: R 255→0, G 255→200, B 0
    r = np.where(n < 0.5, 255,
                 np.clip(255 * (1.0 - (n - 0.5) * 2.0), 0, 255)).astype(np.uint8)
    g = np.where(n < 0.5, 255,
                 np.clip(200 + 55 * (1.0 - (n - 0.5) * 2.0), 0, 255)).astype(np.uint8)
    b = np.where(n < 0.5, np.clip(255 * (1.0 - n * 2.0), 0, 255), 0).astype(np.uint8)
    a = np.where(valid, 210, 0).astype(np.uint8)
    return np.stack([r, g, b, a], axis=-1)


def _empty_tile() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (_TILE_SIZE, _TILE_SIZE), (0, 0, 0, 0)).save(buf, "PNG")
    buf.seek(0)
    return buf.read()


def get_tile(cog_path: str, z: int, x: int, y: int) -> bytes:
    """
    COG GeoTIFF'ten XYZ tile oku, PNG döndür.
    Kapsam dışıysa şeffaf tile döner.
    """
    west, south, east, north = _tile_bounds(z, x, y)

    with rasterio.open(cog_path) as src:
        sb = src.bounds
        if east < sb.left or west > sb.right or north < sb.bottom or south > sb.top:
            return _empty_tile()

        window = rasterio.windows.from_bounds(west, south, east, north, src.transform)
        data = src.read(
            1,
            window=window,
            out_shape=(_TILE_SIZE, _TILE_SIZE),
            resampling=Resampling.bilinear,
            boundless=True,
            fill_value=_NODATA,
        )

    blocked = (data == -1.0)
    valid   = (data >= 0.0) & (data <= 100.0)
    norm    = np.where(valid, data / 100.0, 0.0)
    rgba    = _score_rgba(norm, valid)

    # Hard block: saf kırmızı iç, siyah kenar (1px komşu kontrolü)
    if blocked.any():
        p = np.pad(blocked, 1, constant_values=False)
        all4 = p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:]
        interior = blocked & all4
        edge     = blocked & ~all4
        rgba[interior] = [255,  0,  0, 235]   # saf kırmızı
        rgba[edge]     = [  0,  0,  0, 255]   # siyah kenarlık

    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG")
    buf.seek(0)
    return buf.read()
