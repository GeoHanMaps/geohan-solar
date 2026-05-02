"""
GeoHan Solar GHI Servisi — Çok-kaynaklı, bölge bazlı routing.

Öncelik tablosu (yüksek → düşük çözünürlük):
  Avrupa / Afrika / Orta Doğu : CAMS (3km) → PVGIS (1-5km) → Open-Meteo (25km) → NASA POWER (50km)
  Amerika                     : NSRDB (4km) → Open-Meteo (25km) → NASA POWER (50km)
  Asya / Pasifik              : PVGIS (1-5km) → Open-Meteo (25km) → NASA POWER (50km)

CAMS ve NSRDB; key olmadan otomatik atlanır — fallback devreye girer.
"""

import requests
from app.config import settings
from app.services import cache

_MONTH_DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

# Günlük shortwave_radiation_sum birimi MJ/m² → kWh/m² için 3.6'ya böl
_MJ_TO_KWH = 3.6


# ─── BÖLGE ALGILAMA ────────────────────────────────────────────────────────────

def _region(lat: float, lon: float) -> str:
    if -170 <= lon <= -30:
        return "americas"
    if -30 < lon <= 60 and -35 <= lat <= 72:
        return "europe_africa_me"
    return "asia_pacific"


# ─── VERİ KAYNAKLARI ───────────────────────────────────────────────────────────

def _from_cams(lat: float, lon: float) -> float:
    """
    Copernicus CAMS Solar Radiation — 3km, Avrupa/Afrika/Orta Doğu.
    CDS API v2: sadece URL + key (UID ayrı değil).
    Gereksinim: settings.cams_key (UUID formatı)
    """
    if not settings.cams_key:
        raise RuntimeError("CAMS key eksik — .env dosyasına CAMS_KEY ekle")

    try:
        import cdsapi
    except ImportError:
        raise RuntimeError("cdsapi kurulu değil — pip install cdsapi")

    import tempfile
    import csv
    import os

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
            "date":           "2019-01-01/2019-12-31",
            "time_step":      "P1D",
            "time_reference": "universal_time_coordinated",
            "format":         "csv",
        }, tmp)

        annual_wh = 0.0
        with open(tmp, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f, skipinitialspace=True)
            for row in reader:
                # CAMS CSV sütun adları: 'GHI' (Wh/m²/gün)
                val = row.get("GHI") or row.get("Gb(h)") or row.get("ALLSKY_SFC_SW_DWN") or "0"
                try:
                    annual_wh += float(val)
                except ValueError:
                    pass
        return float(annual_wh / 1000)   # Wh/m² → kWh/m²/yıl
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _from_nsrdb(lat: float, lon: float) -> float:
    """
    NREL NSRDB Solar Resource — 4km, Amerika kıtaları.
    Gereksinim: settings.nsrdb_key + settings.nsrdb_email
    Kayıt: https://developer.nrel.gov
    """
    if not settings.nsrdb_key:
        raise RuntimeError("NSRDB key eksik — .env dosyasına NSRDB_KEY ekle")

    r = requests.get(
        settings.nsrdb_url,
        params={"api_key": settings.nsrdb_key, "lat": lat, "lon": lon},
        timeout=20,
    )
    r.raise_for_status()
    # avg_ghi.annual → kWh/m²/gün
    daily = r.json()["outputs"]["avg_ghi"]["annual"]
    return float(daily * 365)


def _from_pvgis(lat: float, lon: float) -> float:
    """
    PVGIS 5 (EU JRC) — 1-5km, ücretsiz.
    Avrupa/Afrika: CM SAF SARAH-3 (1km).
    Amerika: NSRDB tabanlı (4km).
    Asya/Pasifik: MERRA-2 (50km — NASA ile benzer).
    """
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
    """
    Open-Meteo ERA5 arşivi — ~25km, ücretsiz, API key yok.
    2019 temsil yılı kullanılır.
    """
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


# ─── ROUTING ───────────────────────────────────────────────────────────────────

_PIPELINE = {
    "europe_africa_me": [_from_cams, _from_pvgis,     _from_open_meteo, _from_nasa_power],
    "americas":         [_from_nsrdb, _from_open_meteo, _from_nasa_power],
    "asia_pacific":     [_from_pvgis, _from_open_meteo, _from_nasa_power],
}


def get_annual_ghi(lat: float, lon: float) -> float:
    """
    Bölge bazlı en yüksek çözünürlüklü GHI kaynağını kullanır.
    Key olmayan kaynaklar otomatik atlanır. 30 gün cache.
    """
    clat = round(lat, 3)
    clon = round(lon, 3)

    cached = cache.get("ghi", lat=clat, lon=clon)
    if cached is not None:
        return cached

    region   = _region(lat, lon)
    pipeline = _PIPELINE[region]
    last_exc = None

    for source_fn in pipeline:
        try:
            ghi = source_fn(lat, lon)
            cache.set("ghi", ghi, ttl_days=settings.cache_ttl_solar_days,
                      lat=clat, lon=clon)
            return ghi
        except Exception as exc:
            last_exc = exc
            continue

    raise RuntimeError(
        f"Tüm GHI kaynakları başarısız ({region}): {last_exc}"
    )


def source_info(lat: float, lon: float) -> dict:
    """Hangi bölgede, hangi kaynak öncelikli — debug/health için."""
    region = _region(lat, lon)
    return {
        "region": region,
        "pipeline": [fn.__name__.replace("_from_", "") for fn in _PIPELINE[region]],
        "cams_active":  bool(settings.cams_key),
        "nsrdb_active": bool(settings.nsrdb_key),
    }
