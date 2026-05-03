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
import io

from app import store
from app.auth import get_current_user
from app.limiter import limiter
from app.config import settings
from app.schemas import MapRequest, MapJobResponse, MapStats, BoundaryResult

router_maps       = APIRouter(prefix="/api/v1/maps",       tags=["maps"])
router_boundaries = APIRouter(prefix="/api/v1/boundaries", tags=["boundaries"])


# ─── Harita endpointleri ──────────────────────────────────────────────────────

@router_maps.post("", response_model=MapJobResponse, status_code=202,
                  summary="Heatmap analizi başlat")
@limiter.limit(settings.rate_limit_analyses)
def create_map(request: Request, req: MapRequest,
               _: str = Depends(get_current_user)):
    from app.tasks import map_task
    map_id = str(uuid.uuid4())
    store.create(map_id, {"name": req.name, "type": "map"})
    map_task.delay(map_id, req.model_dump())
    return MapJobResponse(id=map_id, status="pending", name=req.name)


@router_maps.get("/{map_id}", response_model=MapJobResponse,
                 summary="Harita iş durumu")
def get_map(map_id: str, _: str = Depends(get_current_user)):
    job = store.get(map_id)
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
             _: str = Depends(get_current_user)):
    job = store.get(map_id)
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
def get_constraints(map_id: str, _: str = Depends(get_current_user)):
    job = store.get(map_id)
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
def get_geotiff(map_id: str, _: str = Depends(get_current_user)):
    job = store.get(map_id)
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
