"""
Raster MCDA heatmap — polygon için 0-100 skor GeoTIFF üretir.

Yöntem:
  1. GEE sampleRectangle → slope/aspect/LC raster (hedef UTM çözünürlüğünde)
  2. Solar API → 5 köşe/merkez noktasından IDW interpolasyon
  3. OSM merkez noktası → grid/access mesafe skoru (alan küçük → yeterince doğru)
  4. Vektörize MCDA → 2D skor array
  5. rasterio GeoTIFF (WGS84, Float32, nodata=-9999, deflate sıkıştırma)
"""

import io
import json
import numpy as np
import ee
from pathlib import Path
from shapely.geometry import shape, mapping
import rasterio
from rasterio.transform import from_bounds
from rasterio.crs import CRS
from rasterio.features import geometry_mask

from app.services import solar, grid as grid_svc, access as access_svc
from app.services.mcda import LC_SCORE, get_weights

_NODATA = -9999.0
_GLOBAL_HARD_LC = {70, 80, 90, 95}  # kar/buz, su, sulak alan, mangrov
_SOFT_BLOCK_SCORE = 25.0             # soft block alanlar için LC skoru

_RULES_PATH = Path(__file__).parents[2] / "config" / "country_rules.json"


def _country_rules(country_code: str) -> dict:
    try:
        rules = json.loads(_RULES_PATH.read_text())
        return rules.get(country_code, rules.get("DEFAULT", {}))
    except Exception:
        return {}


def _utm_epsg(lat: float, lon: float) -> int:
    zone = int((lon + 180) / 6) + 1
    return 32600 + zone if lat >= 0 else 32700 + zone


def _terrain_raster(
    minx: float, miny: float, maxx: float, maxy: float,
    resolution_m: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    GEE'den slope (°), aspect (°), lc (ESA kod) rasterı al.
    Row 0 = kuzey (maxy) — rasterio from_bounds ile uyumlu.
    """
    lat_c = (miny + maxy) / 2
    lon_c = (minx + maxx) / 2
    utm_epsg = _utm_epsg(lat_c, lon_c)

    roi  = ee.Geometry.Rectangle([minx, miny, maxx, maxy])
    proj = ee.Projection(f"EPSG:{utm_epsg}").atScale(resolution_m)

    srtm = ee.Image("USGS/SRTMGL1_003")
    lc   = ee.ImageCollection("ESA/WorldCover/v200").first()

    combined = (
        ee.Terrain.slope(srtm).rename("slope")
        .addBands(ee.Terrain.aspect(srtm).rename("aspect"))
        .addBands(lc.rename("lc"))
        .reproject(proj)
    )

    props = combined.sampleRectangle(region=roi, defaultValue=0).getInfo()["properties"]

    slope  = np.array(props["slope"],  dtype=float)
    aspect = np.array(props["aspect"], dtype=float)
    lc_arr = np.array(props["lc"],     dtype=float)

    target = slope.shape
    if aspect.shape != target:
        aspect = np.resize(aspect, target)
    if lc_arr.shape != target:
        lc_arr = np.resize(lc_arr, target)

    return slope, aspect, lc_arr.astype(int)


def _idw_ghi(
    rows: int, cols: int,
    minx: float, miny: float, maxx: float, maxy: float,
) -> np.ndarray:
    """5 nokta IDW → GHI grid (row 0 = kuzey)."""
    pts = [
        (miny, minx), (miny, maxx),
        (maxy, minx), (maxy, maxx),
        ((miny + maxy) / 2, (minx + maxx) / 2),
    ]
    vals = []
    for slat, slon in pts:
        try:
            vals.append(solar.get_annual_ghi(slat, slon))
        except Exception:
            vals.append(1600.0)

    lats = np.linspace(maxy, miny, rows)
    lons = np.linspace(minx, maxx, cols)
    lon_g, lat_g = np.meshgrid(lons, lats)

    num = np.zeros((rows, cols), dtype=float)
    den = np.zeros((rows, cols), dtype=float)
    for (slat, slon), v in zip(pts, vals):
        d = np.maximum(np.hypot(lon_g - slon, lat_g - slat), 1e-10)
        w = 1.0 / d
        num += w * v
        den += w
    return num / den


# ─── Vectorized skor fonksiyonları ────────────────────────────────────────────

def _s_slope(pct: np.ndarray) -> np.ndarray:
    return np.where(pct <= 5, 100.0,
           np.where(pct <= 15, 100.0 - (pct - 5) * 10, 0.0)).clip(0, 100)


def _s_ghi(g: np.ndarray) -> np.ndarray:
    return np.where(g >= 2000, 100.0,
           np.where(g >= 1200, (g - 1200) / 800 * 100, 0.0)).clip(0, 100)


def _s_dist(km: np.ndarray, near: float, far: float) -> np.ndarray:
    safe = np.maximum(km, near)
    return np.where(km <= near, 100.0,
           np.where(km >= far, 0.0,
           (100 - np.log(safe / near) / np.log(far / near) * 100).clip(0, 100)))


# ─── Ana fonksiyon ────────────────────────────────────────────────────────────

def generate(
    polygon_geojson: dict,
    resolution_m: int = 250,
    panel_tech: str = "mono",
    tracking: str = "fixed",
    country_code: str = "DEFAULT",
) -> bytes:
    """
    Polygon için MCDA heatmap GeoTIFF üret.
    Returns: GeoTIFF bytes (Float32, WGS84, nodata=-9999, deflate).
    """
    geom = shape(polygon_geojson)
    minx, miny, maxx, maxy = geom.bounds
    lat_c = (miny + maxy) / 2
    lon_c = (minx + maxx) / 2

    # 1. Terrain (GEE)
    slope_deg, aspect_deg, lc = _terrain_raster(minx, miny, maxx, maxy, resolution_m)
    rows, cols = slope_deg.shape
    slope_pct = np.tan(np.radians(slope_deg)) * 100

    # 2. Aspect skoru
    optimal = 180.0 if lat_c >= 0 else 0.0
    aspect_sc = ((np.cos(np.radians(aspect_deg - optimal)) + 1) / 2 * 100)

    # 3. Gölge skoru (hızlı terrain tahmini)
    shadow_sc = np.clip(100.0 - slope_pct * 1.0, 0.0, 100.0)

    # 4. GHI raster (IDW)
    ghi = _idw_ghi(rows, cols, minx, miny, maxx, maxy)

    # 5. Mesafe skoru — alan merkezi için tek değer
    try:
        grid_km = grid_svc.nearest_substation_km(lat_c, lon_c)
    except Exception:
        grid_km = 5.0
    try:
        road_km = access_svc.nearest_road_km(lat_c, lon_c)
    except Exception:
        road_km = 1.0

    grid_sc = _s_dist(np.full((rows, cols), grid_km), 1.0, 30.0)
    road_sc = _s_dist(np.full((rows, cols), road_km), 0.5, 10.0)

    # 6. Arazi örtüsü ve yasal skoru (ülke kuralları dahil)
    rules = _country_rules(country_code)
    country_forbidden = set(rules.get("forbidden_lc", []))
    soft_block_lc     = set(rules.get("soft_block_lc", []))
    all_hard_lc       = _GLOBAL_HARD_LC | country_forbidden

    hard_blk = np.vectorize(lambda c: int(c) in all_hard_lc)(lc)
    soft_blk = np.vectorize(lambda c: int(c) in soft_block_lc)(lc)

    lc_sc = np.vectorize(
        lambda c: 0.0 if int(c) in all_hard_lc
                  else (_SOFT_BLOCK_SCORE if int(c) in soft_block_lc
                        else LC_SCORE.get(int(c), 50))
    )(lc).astype(float)
    yasal_sc = np.where(hard_blk, 0.0, np.where(soft_blk, 40.0, 100.0))

    # 7. MCDA toplam
    w = get_weights()
    score = (
        _s_slope(slope_pct)            * w["egim"]   +
        _s_ghi(ghi)                    * w["ghi"]    +
        aspect_sc                      * w["baki"]   +
        shadow_sc                      * w["golge"]  +
        lc_sc                          * w["arazi"]  +
        grid_sc                        * w["sebeke"] +
        road_sc                        * w["erisim"] +
        yasal_sc                       * w["yasal"]
    ).clip(0, 100).astype("float32")

    # Hard block → özel değer (-1) → tiler'da kırmızı overlay
    score[hard_blk] = -1.0

    # 8. Polygon dışını maskle
    tf = from_bounds(minx, miny, maxx, maxy, cols, rows)
    outside = geometry_mask([mapping(geom)], out_shape=(rows, cols),
                             transform=tf, invert=False)
    score[outside] = _NODATA

    # 9. GeoTIFF yaz
    buf = io.BytesIO()
    with rasterio.open(
        buf, "w",
        driver="GTiff",
        height=rows, width=cols,
        count=1, dtype="float32",
        crs=CRS.from_epsg(4326),
        transform=tf,
        nodata=_NODATA,
        compress="deflate",
    ) as dst:
        dst.write(score, 1)

    buf.seek(0)
    return buf.read()
