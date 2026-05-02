"""
Admin sınır servisi — il / ilçe / bölge GeoJSON polygon arama.
Kaynak: OSM Nominatim (osmnx.geocode_to_gdf).
"""

import math
from shapely.geometry import mapping
import osmnx as ox

from app.services.cache import get as cache_get, set as cache_set

_CACHE_TTL = 7.0  # gün


def _area_km2(geom) -> float:
    minx, miny, maxx, maxy = geom.bounds
    lat_c     = (miny + maxy) / 2
    width_km  = (maxx - minx) * 111.32 * math.cos(math.radians(lat_c))
    height_km = (maxy - miny) * 111.32
    return width_km * height_km


def search(query: str) -> list[dict]:
    """
    Nominatim ile idari sınır ara (il/ilçe/bölge adı).
    Returns: [{"name", "geojson", "bounds":[W,S,E,N], "area_km2"}, ...]
    """
    q = query.strip()
    if not q:
        return []

    cached = cache_get("boundary", query=q.lower())
    if cached is not None:
        return cached

    try:
        gdf = ox.geocode_to_gdf(q, which_result=None)
    except Exception:
        return []

    if gdf is None or gdf.empty:
        return []

    results = []
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        results.append({
            "name":     str(row.get("display_name", q)),
            "geojson":  mapping(geom),
            "bounds":   list(geom.bounds),   # [minx, miny, maxx, maxy]
            "area_km2": round(_area_km2(geom), 1),
        })
        if len(results) >= 5:
            break

    cache_set("boundary", results, ttl_days=_CACHE_TTL, query=q.lower())
    return results
