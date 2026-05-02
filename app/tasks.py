"""
Celery task'ları — analiz mantığı burada çalışır.
API process sadece job oluşturup task'ı kuyruğa atar.
Worker process bu dosyayı çalıştırır.
"""

from pathlib import Path

from app.celery_app import celery_app
from app import store
from app.schemas import (
    AnalysisRequest, AnalysisResult, BatchRequest,
    CriterionScore, ScoreBreakdown, CapacityResult, FinancialResult,
    MapRequest,
)
from app.services import (
    terrain, solar, grid, access, capacity,
    mcda, financial, legal, downscale,
)


# ─── Tekil analiz ─────────────────────────────────────────────────────────────

@celery_app.task(bind=True, max_retries=2, default_retry_delay=60,
                 name="geohan.analyse")
def analyse_task(self, job_id: str, req_data: dict) -> None:
    req = AnalysisRequest(**req_data)
    if req.country_code is None:
        req.country_code = "DEFAULT"
    store.set_running(job_id)
    try:
        t = terrain.analyse(req.lat, req.lon)
        try:
            hp = terrain.horizon_profile(req.lat, req.lon)
        except Exception:
            hp = None

        ghi  = solar.get_annual_ghi(req.lat, req.lon)
        corr = downscale.terrain_correction(
                   req.lat, req.lon, t["slope_mean_deg"], t["aspect_deg"],
                   horizon_profile=hp)
        ghi  = downscale.apply(ghi, corr)
        gkm  = grid.nearest_substation_km(req.lat, req.lon, country_code=req.country_code)
        rkm  = access.nearest_road_km(req.lat, req.lon)
        leg  = legal.check(req.lat, req.lon, t["lc_code"],
                           t["slope_mean_pct"], req.country_code)
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
            ),
            financial=FinancialResult(**fin),
        )
        store.set_done(job_id, result.model_dump())

    except Exception as exc:
        store.set_failed(job_id, str(exc))


# ─── Batch analiz ─────────────────────────────────────────────────────────────

def _analyse_one(loc, req: BatchRequest) -> dict | None:
    """Tek lokasyon — hata varsa None döner."""
    try:
        t  = terrain.analyse(loc.lat, loc.lon)
        try:
            hp = terrain.horizon_profile(loc.lat, loc.lon)
        except Exception:
            hp = None
        ghi = solar.get_annual_ghi(loc.lat, loc.lon)
        corr = downscale.terrain_correction(
                   loc.lat, loc.lon, t["slope_mean_deg"], t["aspect_deg"],
                   horizon_profile=hp)
        ghi = downscale.apply(ghi, corr)
        gkm = grid.nearest_substation_km(loc.lat, loc.lon, country_code=req.country_code)
        rkm = access.nearest_road_km(loc.lat, loc.lon)
        leg = legal.check(loc.lat, loc.lon, t["lc_code"],
                          t["slope_mean_pct"], req.country_code)
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


# ─── Heatmap (premium) ────────────────────────────────────────────────────────

@celery_app.task(bind=True, max_retries=2, default_retry_delay=60,
                 name="geohan.map")
def map_task(self, map_id: str, req_data: dict) -> None:
    """Polygon için MCDA raster üret, COG olarak kaydet."""
    import rasterio
    from app.services import heatmap
    from app.config import settings

    req = MapRequest(**req_data)
    store.set_running(map_id)
    try:
        tiff_bytes = heatmap.generate(
            polygon_geojson=req.geom,
            resolution_m=req.resolution_m,
            panel_tech=req.panel_tech,
            tracking=req.tracking,
            country_code=req.country_code,
        )

        maps_dir = Path(settings.maps_data_dir)
        maps_dir.mkdir(parents=True, exist_ok=True)
        tiff_path = str(maps_dir / f"{map_id}.tif")

        with open(tiff_path, "wb") as f:
            f.write(tiff_bytes)

        with rasterio.open(tiff_path) as src:
            data = src.read(1)

        valid = data[(data > 0) & (data <= 100)]
        store.set_done(map_id, {
            "tiff_path":  tiff_path,
            "score_min":  float(valid.min())  if len(valid) else 0.0,
            "score_max":  float(valid.max())  if len(valid) else 0.0,
            "score_mean": float(valid.mean()) if len(valid) else 0.0,
        })

    except Exception as exc:
        store.set_failed(map_id, str(exc))
