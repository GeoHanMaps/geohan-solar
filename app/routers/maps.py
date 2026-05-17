"""
Heatmap ve admin sınır endpointleri (Premium).

POST   /api/v1/maps                           — Harita analizi başlat
GET    /api/v1/maps/{id}                      — İş durumu + istatistik
GET    /api/v1/maps/{id}/tiles/{z}/{x}/{y}.png — XYZ tile (PNG)
GET    /api/v1/maps/{id}/geotiff              — Ham GeoTIFF indir
GET    /api/v1/boundaries/search?q=           — Admin sınır ara
"""

import uuid
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse, Response
from fastapi.security import OAuth2PasswordBearer
import io

from sqlalchemy.orm import Session

from app import store
from app.auth import decode_token, get_current_user
from app.db import get_session
from app.limiter import limiter
from app.config import settings
from app.models.credit_transaction import REASON_HEATMAP
from app.models.job_record import KIND_MAP
from app.schemas import MapRequest, MapJobResponse, MapStats, BoundaryResult, LayoutResponse, LayoutSummary
from app.services import jobs
from app.services.credits import require_credit

# Flat 5-credit charge for any heatmap. Area-tiered pricing (5 / 10 by
# polygon km²) is planned once area extraction is on a hot path.
HEATMAP_COST = 5

router_maps       = APIRouter(prefix="/api/v1/maps",       tags=["maps"])
router_boundaries = APIRouter(prefix="/api/v1/boundaries", tags=["boundaries"])

_oauth2 = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")


# ─── Harita endpointleri ──────────────────────────────────────────────────────

@router_maps.post("", response_model=MapJobResponse, status_code=202,
                  summary="Heatmap analizi başlat")
@limiter.limit(settings.rate_limit_analyses)
def create_map(request: Request, req: MapRequest,
               token: str = Depends(_oauth2),
               session: Session = Depends(get_session)):
    """DB user'lar için 5 kredi düşülür; legacy admin token bypass eder."""
    from app.tasks import map_task
    payload = decode_token(token)
    map_id = str(uuid.uuid4())
    require_credit(session, payload, cost=HEATMAP_COST,
                   reason=REASON_HEATMAP, reference_id=map_id)
    uid, _ = jobs.identify(payload)
    params = req.model_dump()
    jobs.record_create(session, job_id=map_id, kind=KIND_MAP,
                       name=req.name, params=params, user_id=uid)
    session.commit()
    store.create(map_id, {"name": req.name, "type": "map"})
    map_task.delay(map_id, params)
    return MapJobResponse(id=map_id, status="pending", name=req.name)


@router_maps.get("/{map_id}", response_model=MapJobResponse,
                 summary="Harita iş durumu")
def get_map(map_id: str, token: str = Depends(_oauth2),
            session: Session = Depends(get_session)):
    job = jobs.load_authorized(session, job_id=map_id,
                               token_payload=decode_token(token))
    if not job:
        raise HTTPException(status_code=404, detail="Map job bulunamadı")

    stats = None
    tile_url = None
    if job["status"] == "done" and job.get("result"):
        r = job["result"]
        stats = MapStats(
            score_min=r["score_min"],
            score_max=r["score_max"],
            score_mean=r["score_mean"],
            area_km2=r.get("area_km2", 0.0),
            pixel_count=r.get("pixel_count", 0),
        )
        tile_url = f"/api/v1/maps/{map_id}/tiles/{{z}}/{{x}}/{{y}}.png"

    return MapJobResponse(
        id=map_id,
        status=job["status"],
        name=job.get("name"),
        error=job.get("error"),
        stats=stats,
        tile_url_template=tile_url,
    )


@router_maps.get("/{map_id}/tiles/{z}/{x}/{y}.png",
                 summary="XYZ tile (RdYlGn PNG)")
def get_tile(map_id: str, z: int, x: int, y: int,
             token: str = Depends(_oauth2),
             session: Session = Depends(get_session)):
    job = jobs.load_authorized(session, job_id=map_id,
                               token_payload=decode_token(token))
    if not job:
        raise HTTPException(status_code=404, detail="Map job bulunamadı")
    if job["status"] != "done":
        raise HTTPException(status_code=425, detail="Harita henüz hazır değil")

    tiff_path = job["result"]["tiff_path"]
    if not Path(tiff_path).exists():
        raise HTTPException(status_code=404, detail="GeoTIFF dosyası bulunamadı")

    from app.services import tiler
    png_bytes = tiler.get_tile(tiff_path, z, x, y)
    return Response(content=png_bytes, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=3600"})


@router_maps.get("/{map_id}/constraints", summary="Yasal kısıt noktaları (GeoJSON)")
def get_constraints(map_id: str, token: str = Depends(_oauth2),
                    session: Session = Depends(get_session)):
    job = jobs.load_authorized(session, job_id=map_id,
                               token_payload=decode_token(token))
    if not job:
        raise HTTPException(status_code=404, detail="Map job bulunamadı")
    if job["status"] != "done":
        raise HTTPException(status_code=425, detail="Harita henüz hazır değil")

    cp = job["result"].get("constraint_path")
    if not cp or not Path(cp).exists():
        return Response(
            content='{"type":"FeatureCollection","features":[]}',
            media_type="application/json",
        )
    return Response(content=Path(cp).read_text(), media_type="application/json",
                    headers={"Cache-Control": "public, max-age=3600"})


@router_maps.get("/{map_id}/geotiff", summary="Ham GeoTIFF indir")
def get_geotiff(map_id: str, token: str = Depends(_oauth2),
                session: Session = Depends(get_session)):
    job = jobs.load_authorized(session, job_id=map_id,
                               token_payload=decode_token(token))
    if not job:
        raise HTTPException(status_code=404, detail="Map job bulunamadı")
    if job["status"] != "done":
        raise HTTPException(status_code=425, detail="Harita henüz hazır değil")

    tiff_path = job["result"]["tiff_path"]
    p = Path(tiff_path)
    if not p.exists():
        raise HTTPException(status_code=404, detail="GeoTIFF dosyası bulunamadı")

    return StreamingResponse(
        io.BytesIO(p.read_bytes()),
        media_type="image/tiff",
        headers={"Content-Disposition": f'attachment; filename="geohan_map_{map_id[:8]}.tif"'},
    )


@router_maps.get("/{map_id}/layout", response_model=LayoutResponse,
                 summary="GES simülasyon katmanı (lazy, ilk GET'te hesaplanır)")
def get_layout(map_id: str, token: str = Depends(_oauth2),
               session: Session = Depends(get_session)):
    job = jobs.load_authorized(session, job_id=map_id,
                               token_payload=decode_token(token))
    if not job:
        raise HTTPException(status_code=404, detail="Map job bulunamadı")
    if job["status"] != "done":
        raise HTTPException(status_code=425, detail="Harita henüz hazır değil")

    tiff_path = (job.get("result") or {}).get("tiff_path")
    if not tiff_path or not Path(tiff_path).exists():
        raise HTTPException(status_code=404, detail="GeoTIFF dosyası bulunamadı")

    from app.models.job_record import JobRecord
    rec = session.get(JobRecord, map_id)
    params = (rec.params or {}) if rec else {}
    country_code = params.get("country_code", "DEFAULT")
    panel_tech   = params.get("panel_tech", "mono")
    tracking     = params.get("tracking", "fixed")

    from app.services import layout as layout_svc
    result = layout_svc.generate(
        tiff_path=tiff_path,
        map_id=map_id,
        data_dir=settings.maps_data_dir,
        country_code=country_code,
        panel_tech=panel_tech,
        tracking=tracking,
        panel_model=params.get("panel_model"),
        inverter_model=params.get("inverter_model"),
        cable_spec=params.get("cable_spec"),
    )
    elec = result["summary"].get("electrical")
    svg = None
    if elec:
        from app.services import electrical as elec_svc
        from app.services import single_line
        svg = single_line.build_svg(
            elec, elec_svc.default_mv_kv(),
            result["summary"].get("target_substation_kv"))
    return LayoutResponse(
        summary=LayoutSummary(**result["summary"]),
        geojson=result["geojson"],
        single_line_svg=svg,
    )


# ─── Sınır endpointleri ───────────────────────────────────────────────────────

@router_boundaries.get("/search", response_model=list[BoundaryResult],
                       summary="İl/ilçe/bölge sınırı ara")
@limiter.limit(settings.rate_limit_default)
def search_boundary(
    request: Request,
    q: str = Query(..., min_length=2, max_length=100, description="Yer adı"),
    _: str = Depends(get_current_user),
):
    from app.services import boundaries
    results = boundaries.search(q)
    if not results:
        raise HTTPException(status_code=404, detail=f"'{q}' için sınır bulunamadı")
    return [BoundaryResult(**r) for r in results]
