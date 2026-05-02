import json
import math
from pathlib import Path

import osmnx as ox
from pyproj import Transformer

from app.config import settings
from app.services import cache

_COSTS_PATH = Path(__file__).parent.parent.parent / "config" / "country_costs.json"
_COUNTRY_COSTS: dict = {}


def _load_costs() -> dict:
    global _COUNTRY_COSTS
    if not _COUNTRY_COSTS:
        _COUNTRY_COSTS = json.loads(_COSTS_PATH.read_text(encoding="utf-8"))
    return _COUNTRY_COSTS


def _reliability_to_km(reliability: float) -> float:
    """Grid reliability (0-1) → estimated nearest substation distance (km).

    Calibrated against real infrastructure benchmarks:
      DE=0.998 → ~3km, TR=0.88 → ~19km, NG=0.55 → ~53km,
      ML=0.45 → ~62km, NE=0.2 → ~82km
    """
    r = max(0.0, min(1.0, reliability))
    return round(3.0 + 95.0 * (1.0 - r) ** 0.7, 1)


def _utm_transformer(lat: float, lon: float) -> tuple[Transformer, float, float]:
    zone = int((lon + 180) / 6) + 1
    epsg = 32600 + zone if lat >= 0 else 32700 + zone
    tr = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    px, py = tr.transform(lon, lat)
    return tr, px, py


def nearest_substation_km(
    lat: float,
    lon: float,
    country_code: str = "DEFAULT",
    search_radius_m: int = 100_000,
) -> float:
    """Nearest grid substation distance (km). 7-day cache.

    Tries OSM Overpass at search_radius_m (default 100 km). If OSM returns
    no results or fails, falls back to a country-specific estimate derived
    from the country's grid_reliability value in country_costs.json.
    """
    clat = round(lat, 3)
    clon = round(lon, 3)

    cached = cache.get("grid", lat=clat, lon=clon)
    if cached is not None:
        return cached

    tr, px, py = _utm_transformer(lat, lon)
    osm_result: float | None = None

    try:
        gdf = ox.features_from_point(
            (lat, lon),
            tags={"power": ["substation", "tower"]},
            dist=search_radius_m,
        )
        min_d = float("inf")
        for _, row in gdf.iterrows():
            g = row.geometry
            c = g if g.geom_type == "Point" else g.centroid
            ex, ey = tr.transform(c.x, c.y)
            min_d = min(min_d, math.hypot(px - ex, py - ey))

        if min_d < float("inf"):
            osm_result = min_d / 1000
    except Exception:
        pass

    if osm_result is not None:
        result = osm_result
    else:
        costs = _load_costs()
        cc = (country_code or "DEFAULT").upper()
        cfg = costs.get(cc) or costs.get("DEFAULT", {})
        reliability = float(cfg.get("grid_reliability", 0.75))
        result = _reliability_to_km(reliability)

    cache.set("grid", result, ttl_days=settings.cache_ttl_osm_days, lat=clat, lon=clon)
    return result
