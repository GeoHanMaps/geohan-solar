import math
import ee
from app.services import cache


def analyse(lat: float, lon: float, radius_m: int = 3000) -> dict:
    """GEE SRTM'den slope, aspect, land cover döndür."""
    delta_lat = radius_m / 111320
    delta_lon = radius_m / (111320 * math.cos(math.radians(lat)))
    roi = ee.Geometry.Rectangle([lon - delta_lon, lat - delta_lat,
                                  lon + delta_lon, lat + delta_lat])

    copdem     = ee.ImageCollection("COPERNICUS/DEM/GLO30").mosaic()
    slope_img  = ee.Terrain.slope(copdem)
    aspect_img = ee.Terrain.aspect(copdem)
    lc_img     = ee.ImageCollection("ESA/WorldCover/v200").first()

    slope_stats = slope_img.reduceRegion(
        reducer=ee.Reducer.mean().combine(
            ee.Reducer.percentile([10, 50, 90]), "", True),
        geometry=roi, scale=30, maxPixels=1e6,
    ).getInfo()

    aspect_val = aspect_img.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=roi, scale=30, maxPixels=1e6,
    ).getInfo().get("aspect_mean", 180)

    lc_val = int(round(lc_img.reduceRegion(
        reducer=ee.Reducer.mode(),
        geometry=roi, scale=10, maxPixels=1e7,
    ).getInfo().get("Map", 30)))

    slope_mean_deg = slope_stats.get("slope_mean", 0)
    slope_p90_deg  = slope_stats.get("slope_p90", 0)

    slope_mean_pct = math.tan(math.radians(slope_mean_deg)) * 100
    slope_p90_pct  = math.tan(math.radians(slope_p90_deg))  * 100

    # Bakı skoru: kosinüs tabanlı (kuzey yarıküre → güney optimal)
    optimal = 180 if lat >= 0 else 0
    aspect_score = (math.cos(math.radians(aspect_val - optimal)) + 1) / 2 * 100

    # Ufuk gölge tahmini (düz arazide minimal)
    shadow_loss_pct = min(slope_mean_pct * 0.2, 5.0)
    shadow_score    = max(0.0, 100 - shadow_loss_pct * 5)

    return {
        "slope_mean_pct":  slope_mean_pct,
        "slope_mean_deg":  slope_mean_deg,
        "slope_p90_pct":   slope_p90_pct,
        "aspect_deg":      aspect_val,
        "aspect_score":    aspect_score,
        "shadow_loss_pct": shadow_loss_pct,
        "shadow_score":    shadow_score,
        "lc_code":         lc_val,
    }


def horizon_profile(lat: float, lon: float) -> dict[int, float]:
    """
    GEE SRTM 90m'den 36 azimut ufuk yükseklik açısı (derece).

    16km × 16km bölge, 90m çözünürlük → ~178 × 178 piksel grid.
    Her 10°'lik azimut için merkez pikselden dışa doğru tarama yapılır;
    maksimum atan2(yükseklik_farkı, yatay_mesafe) açısı ufuk açısıdır.

    Returns: {0: 2.3, 10: 1.1, ..., 350: 3.5}  (GEE yoksa {} döner)
    Cache: 365 gün — arazi değişmiyor.
    """
    clat = round(lat, 3)
    clon = round(lon, 3)

    cached = cache.get("horizon", lat=clat, lon=clon)
    if cached is not None:
        return {int(k): float(v) for k, v in cached.items()}

    import numpy as np

    R_m = 8000  # 8 km her yöne → 16 km kare
    delta_lat = R_m / 111320
    delta_lon = R_m / (111320 * math.cos(math.radians(lat)))

    roi = ee.Geometry.Rectangle([
        lon - delta_lon, lat - delta_lat,
        lon + delta_lon, lat + delta_lat,
    ])

    copdem = ee.ImageCollection("COPERNICUS/DEM/GLO30").mosaic()
    elev_data = (
        copdem
        .sampleRectangle(region=roi, defaultValue=0)
        .get("DEM")
        .getInfo()
    )

    elev = np.array(elev_data, dtype=float)
    rows, cols = elev.shape
    cy, cx = rows // 2, cols // 2
    center_elev = elev[cy, cx]

    # Piksel boyutu: toplam mesafe / piksel sayısı (yaklaşık — kare bölge)
    pixel_m = (R_m * 2) / max(rows, cols)

    profile: dict[int, float] = {}
    max_steps = int(R_m / pixel_m)

    for az_deg in range(0, 360, 10):
        az_rad = math.radians(az_deg)
        # Coğrafi konvansiyon: 0=Kuzey, saat yönü
        # Sütun (col): +doğu → sin(az)
        # Satır (row): kuzey = satır 0 → -cos(az) yönünde
        dcol = math.sin(az_rad)
        drow = -math.cos(az_rad)

        max_angle = 0.0
        for step in range(1, max_steps + 1):
            c = cx + round(dcol * step)
            r = cy + round(drow * step)

            if not (0 <= r < rows and 0 <= c < cols):
                break

            h_diff = elev[int(r), int(c)] - center_elev
            dist_m = step * pixel_m
            angle  = math.degrees(math.atan2(h_diff, dist_m))
            if angle > max_angle:
                max_angle = angle

        profile[az_deg] = round(max_angle, 2)

    cache.set("horizon", profile, ttl_days=365.0, lat=clat, lon=clon)
    return profile
