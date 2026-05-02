import math
import osmnx as ox
from pyproj import Transformer
from app.config import settings
from app.services import cache


def _utm_transformer(lat: float, lon: float) -> tuple[Transformer, float, float]:
    zone = int((lon + 180) / 6) + 1
    epsg = 32600 + zone if lat >= 0 else 32700 + zone
    tr = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    px, py = tr.transform(lon, lat)
    return tr, px, py


def nearest_substation_km(lat: float, lon: float, search_radius_m: int = 60000) -> float:
    """En yakın OSM substation/tower mesafesi (km). 7 gün cache."""
    clat = round(lat, 3)
    clon = round(lon, 3)

    cached = cache.get("grid", lat=clat, lon=clon)
    if cached is not None:
        return cached

    tr, px, py = _utm_transformer(lat, lon)
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
        result = min_d / 1000 if min_d < float("inf") else 99.0
    except Exception:
        result = 99.0

    cache.set("grid", result, ttl_days=settings.cache_ttl_osm_days, lat=clat, lon=clon)
    return result
