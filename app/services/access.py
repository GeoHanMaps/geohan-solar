import math
import osmnx as ox
from pyproj import Transformer
from app.config import settings
from app.services import cache

_HEAVY_VEHICLE_FILTER = '["highway"~"motorway|trunk|primary|secondary|tertiary"]'


def _utm_transformer(lat: float, lon: float) -> tuple[Transformer, float, float]:
    zone = int((lon + 180) / 6) + 1
    epsg = 32600 + zone if lat >= 0 else 32700 + zone
    tr = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    px, py = tr.transform(lon, lat)
    return tr, px, py


def nearest_road_km(lat: float, lon: float, search_radius_m: int = 15000) -> float:
    """İş makinesi geçebilir en yakın yol mesafesi (km). 7 gün cache."""
    clat = round(lat, 3)
    clon = round(lon, 3)

    cached = cache.get("access", lat=clat, lon=clon)
    if cached is not None:
        return cached

    tr, px, py = _utm_transformer(lat, lon)
    try:
        G = ox.graph_from_point(
            (lat, lon),
            dist=search_radius_m,
            network_type="drive",
            custom_filter=_HEAVY_VEHICLE_FILTER,
        )
        min_d = float("inf")
        for _, data in G.nodes(data=True):
            ex, ey = tr.transform(data["x"], data["y"])
            min_d = min(min_d, math.hypot(px - ex, py - ey))
        result = min_d / 1000 if min_d < float("inf") else 99.0
    except Exception:
        result = 99.0

    cache.set("access", result, ttl_days=settings.cache_ttl_osm_days, lat=clat, lon=clon)
    return result
