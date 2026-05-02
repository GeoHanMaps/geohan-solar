"""
GeoHan Terrain Downscaling Servisi

Coarse GHI (1-50km) → site-specific GHI (~30m) dönüşümü.

Yöntem:
  1. pvlib clear-sky simülasyonu ile yıllık tilt correction faktörü hesapla
  2. Horizon profile ile her saat güneşin ufuk profiline göre maskelenmesi
  3. Sky view factor ile diffuse ışınımı düzelt

Çözünürlük: coarse veri (PVGIS 1-5km, NASA 50km) → 30m DEM hassasiyeti
"""

from __future__ import annotations

import math
from app.config import settings
from app.services import cache


def terrain_correction(
    lat: float,
    lon: float,
    slope_deg: float,
    aspect_deg: float,
    horizon_profile: dict[int, float] | None = None,
) -> float:
    """
    Yatay yüzey GHI → eğimli yüzey GHI çarpanı.

    pvlib clear-sky simülasyonu (8760 saat/yıl) ile hesaplanır.
    Horizon profile varsa DNI saatlik olarak maskelenir.
    Aynı parametreler için 180 gün cache'lenir.

    Returns: correction_factor  (tipik aralık: 0.80 – 1.20)
    """
    clat    = round(lat, 2)
    clon    = round(lon, 2)
    cslope  = round(slope_deg, 1)
    caspect = round(aspect_deg, 0)
    hor     = 1 if horizon_profile else 0   # cache versiyonunu ayırt eder

    cached = cache.get("downscale", lat=clat, lon=clon,
                       slope=cslope, aspect=caspect, hor=hor)
    if cached is not None:
        return float(cached)

    try:
        factor = _pvlib_factor(lat, lon, slope_deg, aspect_deg, horizon_profile)
    except Exception:
        factor = _geometric_factor(lat, slope_deg, aspect_deg)

    # [0.5, 1.5] sınırına zorla — fizik dışı değerlere karşı güvence
    factor = max(0.5, min(1.5, factor))

    cache.set("downscale", factor, ttl_days=settings.cache_ttl_downscale_days,
              lat=clat, lon=clon, slope=cslope, aspect=caspect, hor=hor)
    return factor


def _pvlib_factor(
    lat: float,
    lon: float,
    slope_deg: float,
    aspect_deg: float,
    horizon_profile: dict[int, float] | None = None,
) -> float:
    """
    pvlib Haydavies modeli ile yıllık POA/GHI oranı.

    horizon_profile varsa güneşin ufuk profilinin altında kaldığı saatlerde
    DNI sıfırlanır (difüz ışınım etkilenmez) → gerçek kazanç hesaplanır.
    """
    import numpy as np
    import pandas as pd
    import pvlib

    times    = pd.date_range("2019-01-01", "2019-12-31 23:00", freq="h", tz="UTC")
    location = pvlib.location.Location(latitude=lat, longitude=lon)
    sol      = location.get_solarposition(times)
    cs       = location.get_clearsky(times, model="simplified_solis")

    dni_extra = pvlib.irradiance.get_extra_radiation(times)
    dni       = cs["dni"].copy()

    if horizon_profile:
        # Azimutları ve karşılık gelen ufuk açılarını numpy dizisine al
        azimuths = sorted(horizon_profile.keys())
        hz_vals  = [horizon_profile[az] for az in azimuths]

        az_arr = np.array(azimuths, dtype=float)
        hz_arr = np.array(hz_vals,  dtype=float)

        # 360° → 0° geçişi için dairesel interpolasyon: son değeri 360°'e tekrarla
        az_ext = np.concatenate([az_arr, [360.0]])
        hz_ext = np.concatenate([hz_arr, [hz_arr[0]]])

        sun_az = sol["azimuth"].values % 360.0
        sun_el = sol["elevation"].values

        horizon_at_sun = np.interp(sun_az, az_ext, hz_ext)

        # Güneş ufuk profilinin altında → DNI sıfır (difüz hâlâ gelir)
        masked = sun_el < horizon_at_sun
        dni = dni.copy()
        dni[masked] = 0.0

    poa = pvlib.irradiance.get_total_irradiance(
        surface_tilt=slope_deg,
        surface_azimuth=aspect_deg,
        solar_zenith=sol["apparent_zenith"],
        solar_azimuth=sol["azimuth"],
        dni=dni,
        ghi=cs["ghi"],
        dhi=cs["dhi"],
        dni_extra=dni_extra,
        model="haydavies",
    )

    annual_poa = float(poa["poa_global"].clip(lower=0).sum())
    annual_ghi = float(cs["ghi"].clip(lower=0).sum())

    if annual_ghi < 1.0:
        return 1.0
    return annual_poa / annual_ghi


def _geometric_factor(lat: float, slope_deg: float, aspect_deg: float) -> float:
    """
    pvlib yoksa ya da başarısız olursa basit geometrik yaklaşım.
    Yaklaşık doğruluk: ±10%.
    """
    slope_rad = math.radians(slope_deg)

    svf = (1 + math.cos(slope_rad)) / 2

    optimal = 180.0 if lat >= 0 else 0.0
    aspect_eff = (math.cos(math.radians(aspect_deg - optimal)) + 1) / 2
    lat_factor = max(0, 1 - abs(lat) / 90)
    direct_boost = slope_rad * aspect_eff * lat_factor * 0.35

    return float(svf * 0.35 + (1 + direct_boost) * 0.65)


def sky_view_factor(slope_deg: float) -> float:
    """
    Diffuse ışınım için gökyüzü görüş faktörü.
    Düz arazi → 1.0, dikey duvar → 0.5.
    """
    return (1 + math.cos(math.radians(slope_deg))) / 2


def horizon_shading_factor(horizon_profile: dict[int, float] | None) -> float:
    """
    Yıllık ortalama ufuk gölgesi kayıp faktörü (yardımcı — basit tahmın).
    Gerçek ufuk maskeleme _pvlib_factor içinde yapılır.
    """
    if not horizon_profile:
        return 1.0
    avg_horizon = sum(horizon_profile.values()) / len(horizon_profile)
    loss = min(avg_horizon / 45.0, 0.30)
    return 1.0 - loss


def apply(coarse_ghi: float, correction_factor: float) -> float:
    """
    Coarse GHI'ya terrain correction uygular.
    Güvenlik sınırı: [0, coarse_ghi × 1.5]
    """
    return float(max(0.0, min(coarse_ghi * correction_factor, coarse_ghi * 1.5)))
