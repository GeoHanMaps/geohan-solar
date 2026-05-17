"""
GeoHan Solar GHI Servisi — Çok-kaynaklı, bölge bazlı routing + TMY/P50-P90.

Metodoloji:
  CAMS ve Open-Meteo: 2013-2022 (10 yıl) gerçek günlük veri → yıllık toplamlar →
    ≥5 yıl: P50 = medyan, P90 = %10'luk dilim (empirical)
    < 5 yıl: P90 = P50 × (1 − 1.282 × 0.07)  (CV=%7, cv_estimate)
  PVGIS (2005-2020 ortalaması), NSRDB, NASA POWER: CV tabanlı P90 kestirimi.

P50 → MCDA skorlaması (tipik yıl). P90 → banka finansmanı / yatırımcı belgesi.

Öncelik tablosu:
  Avrupa / Afrika / Orta Doğu : CAMS (3km) → PVGIS → Open-Meteo → NASA POWER
  Amerika                     : NSRDB (4km) → Open-Meteo → NASA POWER
  Asya / Pasifik              : PVGIS → Open-Meteo → NASA POWER

CAMS CSV parser bug fix: ';' ayraç (DictReader varsayılan ',' değil), GHI sütunu
(Gb(h) ≠ GHI — beam ≠ global), negatif missing-data sentinel temizlendi.
"""

import csv
import os
import statistics
import tempfile
from typing import TypedDict

import requests

from app.config import settings
from app.services import cache

_MONTH_DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
_MJ_TO_KWH  = 3.6
_TMY_START   = "2013-01-01"
_TMY_END     = "2022-12-31"


class SolarStats(TypedDict):
    p50:        float   # Medyan yıllık GHI (kWh/m²/yıl) — MCDA skoru için
    p90:        float   # P90 exceedance: yılların %90'ı bu değerin üzerinde üretir
    p90_method: str     # "empirical" (≥5 yıl veri) | "cv_estimate" (CV=%7)
    source:     str     # Başarılı veri kaynağı
    years_used: int     # Veri setindeki yıl sayısı


# ─── İSTATİSTİK YARDIMCILARI ────────────────────────────────────────────────────

def _p50_p90(values: list[float]) -> tuple[float, float]:
    """
    P50 = medyan yıllık GHI.
    P90 = %10'luk dilim (P90 exceedance: yılların %90'ı bu değeri aşar).
    """
    if len(values) == 1:
        return values[0], _cv_p90(values[0])
    sorted_v = sorted(values)
    n        = len(sorted_v)
    p50      = statistics.median(sorted_v)
    idx      = max(0, round(0.10 * n) - 1)
    p90      = sorted_v[idx]
    return p50, p90


def _cv_p90(mean: float, cv: float = 0.07) -> float:
    """
    CV tabanlı P90: tek yıllık ortalama bilindiğinde kullanılır.
    P90 ≈ mean × (1 − 1.282 × CV); CV=%7 tipik yıllararası GHI değişkenliği.
    """
    return mean * (1.0 - 1.282 * cv)


# ─── CAMS CSV PARSER (DÜZELTME) ─────────────────────────────────────────────────

def _parse_cams_csv(filepath: str) -> dict[int, float]:
    """
    CAMS Solar Radiation CSV → {yıl: yıllık GHI (kWh/m²)}.

    CAMS format: ';' ayraç (virgül DEĞİL), yorum satırları '#' ile başlar,
    GHI sütunu Wh/m²/gün, 'Observation period' = 'YYYY-MM-DD/YYYY-MM-DD'.
    Negatif değerler (-1, -999): missing data sentinel — atlanır.
    """
    annual: dict[int, float] = {}
    with open(filepath, newline="", encoding="utf-8") as f:
        lines = [line for line in f if not line.startswith("#")]

    if not lines:
        return annual

    reader = csv.DictReader(lines, delimiter=";")
    for row in reader:
        row = {k.strip(): v.strip() for k, v in row.items() if k}
        period   = row.get("Observation period", "")
        year_str = period[:4]
        try:
            year = int(year_str)
        except ValueError:
            continue
        ghi_str = row.get("GHI", "")
        try:
            ghi_val = float(ghi_str)
        except ValueError:
            continue
        if ghi_val < 0:
            continue
        annual[year] = annual.get(year, 0.0) + ghi_val

    return {yr: wh / 1000 for yr, wh in annual.items()}  # Wh/m² → kWh/m²


# ─── BÖLGE ALGILAMA ─────────────────────────────────────────────────────────────

def _region(lat: float, lon: float) -> str:
    if -170 <= lon <= -30:
        return "americas"
    if -30 < lon <= 60 and -35 <= lat <= 72:
        return "europe_africa_me"
    return "asia_pacific"


# ─── CAMS ORTAK FETCH (kod tekrarını önler) ─────────────────────────────────────

def _fetch_cams_annual(lat: float, lon: float) -> dict[int, float]:
    """CAMS API → 10 yıllık {yıl: kWh/m²}. Key yoksa RuntimeError."""
    if not settings.cams_key:
        raise RuntimeError("CAMS key eksik — .env dosyasına CAMS_KEY ekle")
    try:
        import cdsapi
    except ImportError:
        raise RuntimeError("cdsapi kurulu değil — pip install cdsapi")

    c = cdsapi.Client(
        url=settings.cams_ads_url,
        key=settings.cams_key,
        quiet=True,
        verify=True,
    )
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        tmp = f.name
    try:
        c.retrieve("cams-solar-radiation-timeseries", {
            "sky_type":       "observed_cloud",
            "location":       {"latitude": lat, "longitude": lon},
            "altitude":       "-999.",
            "date":           f"{_TMY_START}/{_TMY_END}",
            "time_step":      "P1D",
            "time_reference": "universal_time_coordinated",
            "format":         "csv",
        }, tmp)
        annual = _parse_cams_csv(tmp)
        if not annual:
            raise RuntimeError("CAMS: GHI verisi ayrıştırılamadı (boş sonuç)")
        return annual
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ─── MEVCUT KAYNAK FONKSİYONLARI (test uyumluluğu için korunur) ──────────────

def _from_cams(lat: float, lon: float) -> float:
    """CAMS P50 (medyan yıllık GHI). 10 yıl (2013-2022), ';' parser düzeltildi."""
    annual = _fetch_cams_annual(lat, lon)
    return statistics.median(list(annual.values()))


def _from_nsrdb(lat: float, lon: float) -> float:
    if not settings.nsrdb_key:
        raise RuntimeError("NSRDB key eksik — .env dosyasına NSRDB_KEY ekle")
    r = requests.get(
        settings.nsrdb_url,
        params={"api_key": settings.nsrdb_key, "lat": lat, "lon": lon},
        timeout=20,
    )
    r.raise_for_status()
    daily = r.json()["outputs"]["avg_ghi"]["annual"]
    return float(daily * 365)


def _from_pvgis(lat: float, lon: float) -> float:
    r = requests.get(
        settings.pvgis_url,
        params={
            "lat": lat, "lon": lon,
            "outputformat": "json",
            "startyear": 2005, "endyear": 2020,
            "global": 1, "localtime": 1,
        },
        timeout=30,
    )
    r.raise_for_status()
    months = r.json()["outputs"]["monthly"]["fixed"]
    return float(sum(
        m["H(h)_m"] * _MONTH_DAYS[i] / 1000
        for i, m in enumerate(months)
    ))


def _from_open_meteo(lat: float, lon: float) -> float:
    """Tek yıl (2019) — mevcut test backward compat için korunur."""
    r = requests.get(
        settings.open_meteo_archive_url,
        params={
            "latitude": lat, "longitude": lon,
            "start_date": "2019-01-01", "end_date": "2019-12-31",
            "daily": "shortwave_radiation_sum",
            "timezone": "UTC",
        },
        timeout=30,
    )
    r.raise_for_status()
    daily_mj = r.json()["daily"]["shortwave_radiation_sum"]
    return float(sum(v for v in daily_mj if v is not None) / _MJ_TO_KWH)


def _from_nasa_power(lat: float, lon: float) -> float:
    """NASA POWER — 50km, global, ücretsiz. Son çare fallback."""
    url = (
        f"{settings.nasa_power_url}"
        f"?parameters=ALLSKY_SFC_SW_DWN&community=RE"
        f"&longitude={lon}&latitude={lat}&format=JSON"
    )
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    ann = r.json()["properties"]["parameter"]["ALLSKY_SFC_SW_DWN"]["ANN"]
    return float(ann * 365)


# ─── STATS VARIANTS (TMY/P50-P90 pipeline için) ──────────────────────────────────

def _stats_from_cams(lat: float, lon: float) -> SolarStats:
    annual = _fetch_cams_annual(lat, lon)
    values = list(annual.values())
    if len(values) >= 5:
        p50, p90 = _p50_p90(values)
        method   = "empirical"
    else:
        p50    = statistics.mean(values)
        p90    = _cv_p90(p50)
        method = "cv_estimate"
    return SolarStats(p50=p50, p90=p90, p90_method=method,
                      source="cams", years_used=len(values))


def _stats_from_pvgis(lat: float, lon: float) -> SolarStats:
    """PVGIS 2005-2020 çok-yıllık ortalaması → CV tabanlı P90."""
    mean = _from_pvgis(lat, lon)
    return SolarStats(p50=mean, p90=_cv_p90(mean),
                      p90_method="cv_estimate", source="pvgis", years_used=16)


def _stats_from_open_meteo(lat: float, lon: float) -> SolarStats:
    """10 yıllık (2013-2022) ERA5 → günlük veri yıl bazında gruplanır → gerçek P50/P90."""
    r = requests.get(
        settings.open_meteo_archive_url,
        params={
            "latitude": lat, "longitude": lon,
            "start_date": _TMY_START, "end_date": _TMY_END,
            "daily": "shortwave_radiation_sum",
            "timezone": "UTC",
        },
        timeout=60,
    )
    r.raise_for_status()
    data   = r.json()["daily"]
    dates  = data["time"]
    values = data["shortwave_radiation_sum"]

    annual: dict[int, float] = {}
    for d, v in zip(dates, values):
        if v is None:
            continue
        year = int(d[:4])
        annual[year] = annual.get(year, 0.0) + v / _MJ_TO_KWH

    if not annual:
        raise RuntimeError("Open-Meteo: geçerli günlük veri döndürülmedi")

    annual_list = list(annual.values())
    if len(annual_list) >= 5:
        p50, p90 = _p50_p90(annual_list)
        method   = "empirical"
    else:
        mean   = statistics.mean(annual_list)
        p50, p90 = mean, _cv_p90(mean)
        method = "cv_estimate"

    return SolarStats(p50=p50, p90=p90, p90_method=method,
                      source="open_meteo", years_used=len(annual_list))


def _stats_from_nsrdb(lat: float, lon: float) -> SolarStats:
    mean = _from_nsrdb(lat, lon)
    return SolarStats(p50=mean, p90=_cv_p90(mean),
                      p90_method="cv_estimate", source="nsrdb", years_used=1)


def _stats_from_nasa_power(lat: float, lon: float) -> SolarStats:
    mean = _from_nasa_power(lat, lon)
    return SolarStats(p50=mean, p90=_cv_p90(mean),
                      p90_method="cv_estimate", source="nasa_power", years_used=22)


# ─── ROUTING ────────────────────────────────────────────────────────────────────

_PIPELINE = {
    "europe_africa_me": [_from_cams, _from_pvgis, _from_open_meteo, _from_nasa_power],
    "americas":         [_from_nsrdb, _from_open_meteo, _from_nasa_power],
    "asia_pacific":     [_from_pvgis, _from_open_meteo, _from_nasa_power],
}

_STATS_PIPELINE = {
    "europe_africa_me": [_stats_from_cams, _stats_from_pvgis,
                         _stats_from_open_meteo, _stats_from_nasa_power],
    "americas":         [_stats_from_nsrdb, _stats_from_open_meteo, _stats_from_nasa_power],
    "asia_pacific":     [_stats_from_pvgis, _stats_from_open_meteo, _stats_from_nasa_power],
}


def get_solar_stats(lat: float, lon: float) -> SolarStats:
    """
    Bölge bazlı TMY/P50-P90 istatistikleri (30 gün cache).
    P50 → MCDA skorlama. P90 → bankability belgesi.
    """
    clat = round(lat, 3)
    clon = round(lon, 3)

    cached = cache.get("ghi_stats", lat=clat, lon=clon)
    if cached is not None:
        return SolarStats(**cached)

    region   = _region(lat, lon)
    pipeline = _STATS_PIPELINE[region]
    last_exc = None

    for source_fn in pipeline:
        try:
            stats = source_fn(lat, lon)
            cache.set("ghi_stats", dict(stats), ttl_days=settings.cache_ttl_solar_days,
                      lat=clat, lon=clon)
            return stats
        except Exception as exc:
            last_exc = exc
            continue

    raise RuntimeError(
        f"Tüm GHI kaynakları başarısız ({region}): {last_exc}"
    )


def get_annual_ghi(lat: float, lon: float) -> float:
    """P50 yıllık GHI (kWh/m²/yıl). Geriye dönük uyumluluk + MCDA için."""
    return get_solar_stats(lat, lon)["p50"]


def source_info(lat: float, lon: float) -> dict:
    """Hangi bölgede, hangi kaynak öncelikli — debug/health için."""
    region = _region(lat, lon)
    return {
        "region": region,
        "pipeline": [
            fn.__name__.replace("_stats_from_", "").replace("_from_", "")
            for fn in _STATS_PIPELINE[region]
        ],
        "cams_active":  bool(settings.cams_key),
        "nsrdb_active": bool(settings.nsrdb_key),
    }
