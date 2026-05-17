"""
Celery task'ları — analiz mantığı burada çalışır.
API process sadece job oluşturup task'ı kuyruğa atar.
Worker process bu dosyayı çalıştırır.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.celery_app import celery_app
from app import store
from app.schemas import (
    AnalysisRequest, AnalysisResult, BatchRequest,
    CriterionScore, ScoreBreakdown, CapacityResult, FinancialResult,
    LegalDetail, MapRequest,
)
from app.services import (
    terrain, solar, grid, access, capacity,
    mcda, financial, legal, downscale, narrative, retention, cache,
)


def _terrain_and_horizon(lat: float, lon: float) -> tuple[dict, dict | None]:
    t = terrain.analyse(lat, lon)
    try:
        hp = terrain.horizon_profile(lat, lon)
    except Exception:
        hp = None
    return t, hp


# ─── Tekil analiz ─────────────────────────────────────────────────────────────

@celery_app.task(bind=True, max_retries=2, default_retry_delay=60,
                 name="geohan.analyse")
def analyse_task(self, job_id: str, req_data: dict) -> None:
    req = AnalysisRequest(**req_data)
    if req.country_code is None:
        req.country_code = "DEFAULT"
    store.set_running(job_id)
    try:
        # Faz 1 — paralel: tüm harici I/O çağrıları aynı anda başlar
        # geo_constraints (WDPA) lat/lon bazlı, terrain'den bağımsız → paralel eklendi
        with ThreadPoolExecutor(max_workers=5) as ex:
            ft = ex.submit(_terrain_and_horizon, req.lat, req.lon)
            fs = ex.submit(solar.get_solar_stats, req.lat, req.lon)
            fg = ex.submit(grid.nearest_substation_km, req.lat, req.lon, req.country_code)
            fa = ex.submit(access.nearest_road_km, req.lat, req.lon)
            fw = ex.submit(legal.geo_constraints, req.lat, req.lon, req.country_code)

            t, hp   = ft.result()
            sol     = fs.result()
            ghi_raw = sol["p50"]
            gkm     = fg.result()
            rkm     = fa.result()
            geo_res = fw.result()

        # Faz 2 — yerel (ağ yok): geo_result önceden hazır
        leg  = legal.check(req.lat, req.lon, t["lc_code"],
                           t["slope_mean_pct"], req.country_code,
                           geo_result=geo_res)
        corr = downscale.terrain_correction(
                   req.lat, req.lon, t["slope_mean_deg"], t["aspect_deg"],
                   horizon_profile=hp)
        ghi  = downscale.apply(ghi_raw, corr)
        cap  = capacity.calculate(t["slope_mean_pct"], ghi, req.area_ha,
                                   req.panel_tech, req.tracking, req.gcr)
        fin  = financial.calculate(cap["total_mw"], cap["annual_gwh"],
                                   grid_km=gkm, road_km=rkm,
                                   country_code=req.country_code)
        res  = mcda.score(t["slope_mean_pct"], ghi, t["aspect_score"],
                          t["shadow_score"], t["lc_code"], gkm, rkm,
                          yasal_score=leg["score"], hard_block=leg["hard_block"])

        s, w = res["scores"], res["weights"]
        utm  = int((req.lon + 180) / 6) + 1

        result = AnalysisResult(
            lat=req.lat, lon=req.lon, area_ha=req.area_ha, utm_zone=utm,
            total_score=res["total"],
            irr_estimate=fin["irr_estimate"],
            hard_block=res.get("hard_block", False),
            ghi_p50=ghi_raw,
            ghi_p90=sol["p90"],
            legal_detail=LegalDetail(**leg),
            breakdown=ScoreBreakdown(
                egim=CriterionScore(   value=round(t["slope_mean_pct"], 1), unit="%",          score=s["egim"],   weight=w["egim"]),
                ghi=CriterionScore(    value=round(ghi, 0),                 unit="kWh/m2/yil", score=s["ghi"],    weight=w["ghi"]),
                baki=CriterionScore(   value=round(t["aspect_deg"], 1),     unit="deg",        score=s["baki"],   weight=w["baki"]),
                golge=CriterionScore(  value=round(t["shadow_loss_pct"], 1),unit="%kayip",     score=s["golge"],  weight=w["golge"]),
                arazi=CriterionScore(  value=float(t["lc_code"]),           unit="ESA_kod",    score=s["arazi"],  weight=w["arazi"]),
                sebeke=CriterionScore( value=round(gkm, 2),                 unit="km",         score=s["sebeke"], weight=w["sebeke"]),
                erisim=CriterionScore( value=round(rkm, 2),                 unit="km",         score=s["erisim"], weight=w["erisim"]),
                yasal=CriterionScore(  value=float(t["lc_code"]),           unit="ESA_kod",    score=s["yasal"],  weight=w["yasal"]),
            ),
            capacity=CapacityResult(
                mw_per_ha=cap["mw_per_ha"],     total_mw=cap["total_mw"],
                annual_gwh=cap["annual_gwh"],   panel_tech=cap["panel_label"],
                tracking=cap["tracking_label"], gcr_effective=cap["gcr_effective"],
                buildable_fraction=cap["buildable_fraction"],
            ),
            financial=FinancialResult(**fin),
        )
        result_dict = result.model_dump()
        store.set_done(job_id, result_dict)

        narr = narrative.generate_analysis(result_dict, language=req.language)
        if narr:
            store.set_narrative(job_id, narr)

    except Exception as exc:
        store.set_failed(job_id, str(exc))


# ─── Batch analiz ─────────────────────────────────────────────────────────────

def _analyse_one(loc, req: BatchRequest) -> dict | None:
    """Tek lokasyon — hata varsa None döner."""
    try:
        with ThreadPoolExecutor(max_workers=5) as ex:
            ft = ex.submit(_terrain_and_horizon, loc.lat, loc.lon)
            fs = ex.submit(solar.get_solar_stats, loc.lat, loc.lon)
            fg = ex.submit(grid.nearest_substation_km, loc.lat, loc.lon, req.country_code)
            fa = ex.submit(access.nearest_road_km, loc.lat, loc.lon)
            fw = ex.submit(legal.geo_constraints, loc.lat, loc.lon, req.country_code)

            t, hp   = ft.result()
            sol     = fs.result()
            ghi_raw = sol["p50"]
            gkm     = fg.result()
            rkm     = fa.result()
            geo_res = fw.result()

        leg = legal.check(loc.lat, loc.lon, t["lc_code"],
                          t["slope_mean_pct"], req.country_code,
                          geo_result=geo_res)
        corr = downscale.terrain_correction(
                   loc.lat, loc.lon, t["slope_mean_deg"], t["aspect_deg"],
                   horizon_profile=hp)
        ghi = downscale.apply(ghi_raw, corr)
        cap = capacity.calculate(t["slope_mean_pct"], ghi, req.area_ha,
                                 req.panel_tech, req.tracking, req.gcr)
        fin = financial.calculate(cap["total_mw"], cap["annual_gwh"],
                                  grid_km=gkm, road_km=rkm,
                                  country_code=req.country_code)
        res = mcda.score(t["slope_mean_pct"], ghi, t["aspect_score"],
                         t["shadow_score"], t["lc_code"], gkm, rkm,
                         yasal_score=leg["score"], hard_block=leg["hard_block"])
        s, w = res["scores"], res["weights"]

        return {
            "lat": loc.lat, "lon": loc.lon, "name": loc.name,
            "total_score": res["total"],
            "irr_estimate": fin["irr_estimate"],
            "hard_block": res.get("hard_block", False),
            "legal_detail": leg,
            "breakdown": {
                "egim":   {"value": round(t["slope_mean_pct"], 1), "unit": "%",          "score": s["egim"],   "weight": w["egim"]},
                "ghi":    {"value": round(ghi, 0),                 "unit": "kWh/m2/yil", "score": s["ghi"],    "weight": w["ghi"]},
                "baki":   {"value": round(t["aspect_deg"], 1),     "unit": "deg",        "score": s["baki"],   "weight": w["baki"]},
                "golge":  {"value": round(t["shadow_loss_pct"], 1),"unit": "%kayip",     "score": s["golge"],  "weight": w["golge"]},
                "arazi":  {"value": float(t["lc_code"]),           "unit": "ESA_kod",    "score": s["arazi"],  "weight": w["arazi"]},
                "sebeke": {"value": round(gkm, 2),                 "unit": "km",         "score": s["sebeke"], "weight": w["sebeke"]},
                "erisim": {"value": round(rkm, 2),                 "unit": "km",         "score": s["erisim"], "weight": w["erisim"]},
                "yasal":  {"value": float(t["lc_code"]),           "unit": "ESA_kod",    "score": s["yasal"],  "weight": w["yasal"]},
            },
            "capacity": {
                "mw_per_ha": cap["mw_per_ha"], "total_mw": cap["total_mw"],
                "annual_gwh": cap["annual_gwh"], "panel_tech": cap["panel_label"],
                "tracking": cap["tracking_label"], "gcr_effective": cap["gcr_effective"],
            },
            "financial": fin,
        }
    except Exception:
        return None


@celery_app.task(bind=True, max_retries=1, default_retry_delay=120,
                 name="geohan.batch")
def batch_task(self, batch_id: str, req_data: dict) -> None:
    req = BatchRequest(**req_data)
    store.set_running(batch_id)
    results, completed = [], 0

    for loc in req.locations:
        r = _analyse_one(loc, req)
        if r:
            results.append(r)
        completed += 1
        store.batch_update_progress(batch_id, completed, results)

    results.sort(key=lambda x: x["total_score"], reverse=True)
    for i, r in enumerate(results, 1):
        r["rank"] = i

    store.set_done(batch_id, {
        "results":   results,
        "completed": completed,
    })

    narr = narrative.generate_batch(results, language=req.language)
    if narr:
        store.set_narrative(batch_id, narr)


# ─── Heatmap (premium) ────────────────────────────────────────────────────────

@celery_app.task(bind=True, max_retries=2, default_retry_delay=60,
                 name="geohan.map")
def map_task(self, map_id: str, req_data: dict) -> None:
    """Polygon için MCDA raster üret, COG olarak kaydet."""
    import math

    import rasterio
    from app.services import heatmap
    from app.config import settings

    req = MapRequest(**req_data)
    store.set_running(map_id)
    try:
        tiff_bytes, constraint_json = heatmap.generate(
            polygon_geojson=req.geom,
            resolution_m=req.resolution_m,
            panel_tech=req.panel_tech,
            tracking=req.tracking,
            country_code=req.country_code,
        )

        maps_dir = Path(settings.maps_data_dir)
        maps_dir.mkdir(parents=True, exist_ok=True)
        tiff_path        = str(maps_dir / f"{map_id}.tif")
        constraint_path  = str(maps_dir / f"{map_id}_constraints.geojson")

        with open(tiff_path, "wb") as f:
            f.write(tiff_bytes)
        with open(constraint_path, "w") as f:
            f.write(constraint_json)

        with rasterio.open(tiff_path) as src:
            data = src.read(1)
            px_w_deg = abs(src.transform.a)   # piksel genişliği (derece)
            px_h_deg = abs(src.transform.e)   # piksel yüksekliği (derece)
            lat_c = (src.bounds.bottom + src.bounds.top) / 2.0

        valid = data[(data > 0) & (data <= 100)]
        pixel_count = int(valid.size)
        # EPSG:4326 raster → metrik alan (boylamda cos(lat) düzeltmeli).
        # resolution_m, heatmap.generate içinde _auto_resolution ile değişebildiği
        # için raster'ın kendi geotransform'undan hesaplanır.
        m_per_deg_lat = 110_540.0
        m_per_deg_lon = 111_320.0 * math.cos(math.radians(lat_c))
        px_area_km2 = (px_w_deg * m_per_deg_lon) * (px_h_deg * m_per_deg_lat) / 1_000_000.0
        store.set_done(map_id, {
            "tiff_path":       tiff_path,
            "constraint_path": constraint_path,
            "score_min":   float(valid.min())  if pixel_count else 0.0,
            "score_max":   float(valid.max())  if pixel_count else 0.0,
            "score_mean":  float(valid.mean()) if pixel_count else 0.0,
            "area_km2":    round(pixel_count * px_area_km2, 2),
            "pixel_count": pixel_count,
        })

    except Exception as exc:
        store.set_failed(map_id, str(exc))


# ─── Artefakt retention ───────────────────────────────────────────────────────

@celery_app.task(name="geohan.cleanup_artifacts")
def cleanup_artifacts_task() -> int:
    """Periyodik (Celery beat) — iki sınırsız-büyüme kaynağını süpürür:
    (1) süresi dolmuş heatmap raster/constraint dosyaları (dayanıklı
    metadata job_records'ta kalır; tile/geotiff yoksa 404 döner),
    (2) süresi dolmuş upstream spatial-cache (OSM/GHI) JSON dosyaları —
    `cache.clear_expired()` başka hiçbir yerden çağrılmıyordu.
    Dönüş: toplam silinen dosya sayısı."""
    from app.config import settings

    deleted = retention.purge_expired_artifacts(
        settings.maps_data_dir, settings.maps_retention_days,
    )
    expired_cache = cache.clear_expired()
    return len(deleted) + expired_cache
