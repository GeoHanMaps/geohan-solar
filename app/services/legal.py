"""
GeoHan Yasal Kısıt Servisi — ESA WorldCover + ülke kuralları + WDPA + askeri bölge.

Kontrol sırası (kısa-devre mantığı):
  1. ESA LC global hard-block (su/kar/sulak alan/mangrov)
  2. Ülke kural motoru — forbidden_lc, max_slope_pct (hard-block)
  3. Ülke kural motoru — soft_block_lc (soft-block)
  4. WDPA korunan alan + OSM askeri bölge (geo_constraints())
     ↳ military-hard > wdpa-hard > soft (wdpa veya military buffer) > temiz

Adım 1-3 ağ çağrısı gerektirmez (fast-path). Adım 4 OSM Overpass kullanır
(Protected Planet API fallback, token varsa). Ağ başarısız olursa graceful
degradation: wdpa_checked=False, skor adım 1-3 mantığından belirlenir.
"""

import json
import math
from pathlib import Path

import osmnx as ox
import requests
from pyproj import Transformer
from shapely.geometry import Point
from shapely.ops import transform as shp_transform

from app.config import settings
from app.services import cache

_RULES_PATH = Path(__file__).parent.parent.parent / "config" / "country_rules.json"

with open(_RULES_PATH, encoding="utf-8") as _f:
    _RULES: dict = json.load(_f)

# ESA WorldCover — ülkeden bağımsız global hard block
_GLOBAL_HARD_BLOCK = {70, 80, 90, 95}  # kar/buz, su, sulak alan, mangrov

_LC_LABELS: dict[int, str] = {
    10: "orman", 20: "makilik/çalılık", 30: "otlak/çayır",
    40: "tarım", 50: "yapılaşmış", 60: "çıplak arazi",
    100: "yosun/liken",
}

# WDPA IUCN kategorileri
_WDPA_HARD_IUCN = {"Ia", "Ib", "II", "III", "IV"}
_WDPA_SOFT_IUCN = {"V", "VI", "Not Reported", "Not Applicable"}

# OSM military tag → önem
_MIL_HARD = {"airfield", "base", "range", "danger_area", "training_area", "obstacle_course"}
_MIL_SOFT = {"barracks", "checkpoint", "trench"}

# OSM protect_class → IUCN kategori eşleme
_PROTECT_CLASS_TO_IUCN: dict[str, str] = {
    "1": "Ia", "1a": "Ia", "1b": "Ib",
    "2": "II", "3": "III", "4": "IV",
    "5": "V",  "6": "VI",
}


# ─── YARDIMCI ────────────────────────────────────────────────────────────────────

def _rules_for(country_code: str) -> dict:
    return _RULES.get(country_code.upper(), _RULES["DEFAULT"])


def _utm_transformer(lat: float, lon: float) -> tuple[Transformer, float, float]:
    zone = int((lon + 180) / 6) + 1
    epsg = 32600 + zone if lat >= 0 else 32700 + zone
    tr   = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    px, py = tr.transform(lon, lat)
    return tr, px, py


def _norm_iucn(val) -> str:
    """OSM protect_class / iucn_level değerini standart IUCN kategorisine çevirir."""
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "Not Reported"
    s     = str(val).strip()
    upper = s.upper()
    # Doğrudan IUCN adı
    aliases = {"IA": "Ia", "IB": "Ib", "II": "II", "III": "III",
               "IV": "IV", "V": "V", "VI": "VI"}
    if upper in aliases:
        return aliases[upper]
    # OSM protect_class numarası
    mapped = _PROTECT_CLASS_TO_IUCN.get(s.lower())
    return mapped if mapped else "Not Reported"


# ─── OSM SORGU + SINIFLANDIRMA ───────────────────────────────────────────────────

def _osm_protected_and_military(lat: float, lon: float):
    """OSM Overpass — tek sorguda WDPA poligonları ve askeri bölgeler."""
    return ox.features_from_point(
        (lat, lon),
        tags={"boundary": ["protected_area"], "military": True},
        dist=settings.wdpa_search_radius_m,
    )


def _classify_osm(lat: float, lon: float, gdf) -> dict:
    """
    GeoDataFrame (EPSG:4326) → {wdpa, military, constraints, *_checked}.
    UTM'de containment ve mesafe hesabı yapılır (metre bazlı doğruluk).
    """
    tr, px, py = _utm_transformer(lat, lon)
    pt_utm     = Point(px, py)

    best_wdpa = None
    best_mil  = None
    constraints: list[dict] = []

    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        try:
            geom_utm = shp_transform(tr.transform, geom)
            dist_m   = geom_utm.distance(pt_utm)
        except Exception:
            continue

        dist_km      = dist_m / 1000.0
        inside       = (geom.geom_type in ("Polygon", "MultiPolygon")
                        and geom_utm.contains(pt_utm))
        within_buffer = dist_km <= settings.wdpa_buffer_km

        if not inside and not within_buffer:
            continue

        # ── Korunan alan ──────────────────────────────────────────────────
        boundary = row.get("boundary")
        if boundary == "protected_area":
            iucn_raw = row.get("protect_class") or row.get("iucn_level")
            iucn     = _norm_iucn(iucn_raw)
            severity = "hard" if (inside and iucn in _WDPA_HARD_IUCN) else "soft"
            name_val = row.get("name")
            name     = str(name_val) if (name_val is not None
                                         and not (isinstance(name_val, float)
                                                  and math.isnan(name_val))) else None
            entry: dict = {
                "type": "wdpa", "name": name, "iucn": iucn,
                "severity": severity, "distance_km": round(dist_km, 2),
                "inside": inside, "source": "osm",
            }
            constraints.append(entry)
            if (best_wdpa is None
                    or (severity == "hard" and best_wdpa["severity"] == "soft")
                    or (severity == best_wdpa["severity"]
                        and dist_km < best_wdpa["distance_km"])):
                best_wdpa = entry

        # ── Askeri bölge ──────────────────────────────────────────────────
        mil_raw = row.get("military")
        if isinstance(mil_raw, float) and math.isnan(mil_raw):
            mil_raw = None
        if mil_raw in _MIL_HARD or mil_raw in _MIL_SOFT:
            sev  = "hard" if mil_raw in _MIL_HARD else "soft"
            name_val = row.get("name")
            mentry: dict = {
                "type": "military",
                "name": (str(name_val) if (name_val is not None
                          and not (isinstance(name_val, float)
                                   and math.isnan(name_val))) else None),
                "military_type": mil_raw,
                "severity": sev,
                "distance_km": round(dist_km, 2),
                "inside": inside, "source": "osm",
            }
            constraints.append(mentry)
            if (best_mil is None
                    or (sev == "hard" and best_mil["severity"] == "soft")
                    or (sev == best_mil["severity"]
                        and dist_km < best_mil["distance_km"])):
                best_mil = mentry

    return {
        "wdpa": best_wdpa, "military": best_mil,
        "wdpa_checked": True, "military_checked": True,
        "constraints": constraints,
    }


# ─── PROTECTED PLANET API FALLBACK ───────────────────────────────────────────────

def _protected_planet_lookup(lat: float, lon: float) -> dict:
    """Protected Planet API v3 — token gerekli, OSM başarısız olduğunda denenir."""
    r = requests.get(
        "https://api.protectedplanet.net/v3/protected_areas/search",
        params={
            "token":         settings.protected_planet_token,
            "with_geometry": "true",
            "lat":  lat, "lon": lon,
            "radius": max(1, settings.wdpa_search_radius_m // 1000),
        },
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _classify_api(lat: float, lon: float, api_resp: dict) -> dict:
    """Protected Planet API yanıtını sınıflandırır."""
    from shapely.geometry import shape as shp_shape

    tr, px, py = _utm_transformer(lat, lon)
    pt_utm     = Point(px, py)

    best_wdpa  = None
    constraints: list[dict] = []

    for area in api_resp.get("protected_areas", []):
        iucn    = _norm_iucn(area.get("iucn_category"))
        name    = area.get("name", "")
        geom_js = area.get("geojson") or area.get("geometry")
        inside  = False
        dist_km = float("inf")

        if geom_js:
            try:
                geom     = shp_shape(geom_js)
                geom_utm = shp_transform(tr.transform, geom)
                inside   = (geom.geom_type in ("Polygon", "MultiPolygon")
                            and geom_utm.contains(pt_utm))
                dist_km  = geom_utm.distance(pt_utm) / 1000.0
            except Exception:
                pass

        if not inside and dist_km > settings.wdpa_buffer_km:
            continue

        severity = "hard" if (inside and iucn in _WDPA_HARD_IUCN) else "soft"
        entry: dict = {
            "type": "wdpa", "name": name, "iucn": iucn,
            "severity": severity, "distance_km": round(dist_km, 2),
            "inside": inside, "source": "protected_planet",
        }
        constraints.append(entry)
        if (best_wdpa is None
                or (severity == "hard" and best_wdpa["severity"] == "soft")):
            best_wdpa = entry

    return {
        "wdpa": best_wdpa, "military": None,
        "wdpa_checked": True, "military_checked": False,
        "constraints": constraints,
    }


# ─── ANA GEO KONSTREYİNT FONKSİYONU ─────────────────────────────────────────────

def geo_constraints(lat: float, lon: float, country_code: str = "DEFAULT") -> dict:
    """
    Nokta için WDPA korunan alan + askeri bölge tespiti (30 gün cache).
    OSM Overpass birincil; Protected Planet API token varsa fallback.
    Ağ başarısız olursa: wdpa_checked=False, skor cezası yok.
    """
    clat = round(lat, 3)
    clon = round(lon, 3)

    cached = cache.get("wdpa", lat=clat, lon=clon, r=settings.wdpa_search_radius_m)
    if cached is not None:
        return cached

    result: dict = {
        "wdpa": None, "military": None,
        "wdpa_checked": False, "military_checked": False,
        "constraints": [],
    }

    try:
        gdf       = _osm_protected_and_military(lat, lon)
        classified = _classify_osm(lat, lon, gdf)
        result.update(classified)
    except Exception:
        if settings.protected_planet_token:
            try:
                api_resp  = _protected_planet_lookup(lat, lon)
                classified = _classify_api(lat, lon, api_resp)
                result.update(classified)
            except Exception:
                pass

    cache.set("wdpa", result, ttl_days=settings.cache_ttl_wdpa_days,
              lat=clat, lon=clon, r=settings.wdpa_search_radius_m)
    return result


# ─── ANA KONTROL FONKSİYONU ──────────────────────────────────────────────────────

def check(
    lat: float,
    lon: float,
    lc_code: int,
    slope_pct: float,
    country_code: str = "DEFAULT",
    geo_result: dict | None = None,
) -> dict:
    """
    Yasal uygunluk skoru (0–100).

    Hard block → score=0, hard_block=True  (MCDA toplam skoru sıfırlanır)
    Soft block → score=40, hard_block=False (izin/ÇED süreciyle çözülebilir)
    Temiz      → score=100

    Fast-path: LC/eğim hard-block ise WDPA sorgusu yapılmaz.
    geo_result: önceden hesaplanmış geo_constraints() sonucu (tasks.py paralel fazdan).
    """
    rules = _rules_for(country_code)

    # ── Fast-path: ağsız LC/eğim kontrolleri ──────────────────────────────
    if lc_code in _GLOBAL_HARD_BLOCK:
        return _result(0, True,
                       f"ESA LC {lc_code} — global hard block (su/kar/sulak alan/mangrov)",
                       country_code, {})

    if lc_code in rules.get("forbidden_lc", []):
        return _result(0, True,
                       f"ESA LC {lc_code} — {country_code} ülke kuralında yasak",
                       country_code, {})

    max_slope = rules.get("max_slope_pct", 20)
    if slope_pct > max_slope:
        return _result(0, True,
                       f"Eğim %{slope_pct:.1f} > {country_code} sınırı %{max_slope}",
                       country_code, {})

    if lc_code in rules.get("soft_block_lc", []):
        label = _LC_LABELS.get(lc_code, f"LC{lc_code}")
        notes = rules.get("notes", "")
        reason = f"ESA LC {lc_code} ({label}) — izin gerekebilir ({country_code})"
        if notes:
            reason += f". {notes}"
        return _result(40, False, reason, country_code, {})

    # ── WDPA + askeri kontrol ────────────────────────────────────────────
    geo  = geo_result if geo_result is not None else geo_constraints(lat, lon, country_code)
    mil  = geo.get("military")
    wdpa = geo.get("wdpa")

    if mil and mil["severity"] == "hard":
        mil_type = mil.get("military_type") or mil.get("type", "")
        return _result(0, True,
                       f"Askeri bölge ({mil_type}) — hard block",
                       country_code, geo)

    if wdpa and wdpa["severity"] == "hard":
        name = wdpa.get("name") or "bilinmiyor"
        iucn = wdpa.get("iucn") or "?"
        return _result(0, True,
                       f"WDPA korunan alan '{name}' (IUCN {iucn}) — hard block",
                       country_code, geo)

    if (wdpa and wdpa["severity"] == "soft") or (mil and mil["severity"] == "soft"):
        parts = []
        if wdpa and wdpa["severity"] == "soft":
            wname = wdpa.get("name") or "korunan alan"
            iucn  = wdpa.get("iucn") or "?"
            parts.append(f"WDPA korunan alan yakınında ('{wname}', IUCN {iucn})")
        if mil and mil["severity"] == "soft":
            mt = mil.get("military_type", "askeri")
            parts.append(f"Askeri bölge yakınında ({mt})")
        reason = " · ".join(parts) + " — ÇED Yönetmeliği kapsamında çevresel etki değerlendirmesi gerekli"
        return _result(40, False, reason, country_code, geo)

    reason = "Bilinen yasal kısıt yok"
    if not geo.get("wdpa_checked"):
        reason += " (WDPA doğrulanamadı — manuel kontrol önerilir)"
    return _result(100, False, reason, country_code, geo)


def available_countries() -> list[str]:
    return [k for k in _RULES if k != "DEFAULT"]


def _result(score: int, hard_block: bool, reason: str,
            country_code: str, geo: dict) -> dict:
    return {
        "score":            score,
        "hard_block":       hard_block,
        "reason":           reason,
        "country_code":     country_code,
        "wdpa_checked":     geo.get("wdpa_checked", False),
        "military_checked": geo.get("military_checked", False),
        "wdpa_name":        (geo.get("wdpa") or {}).get("name"),
        "wdpa_iucn":        (geo.get("wdpa") or {}).get("iucn"),
        "constraints":      geo.get("constraints", []),
    }
