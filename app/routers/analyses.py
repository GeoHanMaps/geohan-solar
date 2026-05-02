import uuid
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
import io

from app import store
from app.auth import get_current_user
from app.limiter import limiter
from app.config import settings
from app.schemas import (
    AnalysisRequest, AnalysisResult, JobResponse, JobListItem,
)

router = APIRouter(prefix="/api/v1/analyses", tags=["analyses"])


# ─── ENDPOINTS ────────────────────────────────────────────────────────────────

@router.post("", response_model=JobResponse, status_code=202,
             summary="Yeni analiz başlat")
@limiter.limit(settings.rate_limit_analyses)
def create_analysis(request: Request, req: AnalysisRequest,
                    _: str = Depends(get_current_user)):
    """
    Analizi Celery worker'a gönderir, hemen `job_id` döner.
    Sonucu `GET /api/v1/analyses/{id}` ile sorgula.
    """
    from app.tasks import analyse_task
    job_id = str(uuid.uuid4())
    store.create(job_id, {"name": req.name})
    analyse_task.delay(job_id, req.model_dump())
    return JobResponse(id=job_id, status="pending", name=req.name)


@router.get("", response_model=list[JobListItem], summary="Tüm işler")
def list_analyses(_: str = Depends(get_current_user)):
    return store.list_all()


@router.get("/{job_id}", response_model=JobResponse, summary="İş durumu ve sonuç")
def get_analysis(job_id: str, _: str = Depends(get_current_user)):
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job bulunamadı")

    result = None
    if job["status"] == "done" and job["result"]:
        result = AnalysisResult(**job["result"])

    return JobResponse(
        id=job_id,
        status=job["status"],
        name=job.get("name"),
        error=job.get("error"),
        result=result,
    )


@router.get("/{job_id}/score", summary="Sadece skor özeti")
def get_score(job_id: str, _: str = Depends(get_current_user)):
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job bulunamadı")
    if job["status"] != "done":
        return {"id": job_id, "status": job["status"]}
    r = job["result"]
    return {
        "id": job_id,
        "total_score": r["total_score"],
        "breakdown": {k: {"score": v["score"], "value": v["value"], "unit": v["unit"]}
                      for k, v in r["breakdown"].items()},
    }


@router.get("/{job_id}/report", summary="PDF rapor indir",
            response_class=StreamingResponse)
def get_report(job_id: str, _: str = Depends(get_current_user)):
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job bulunamadı")
    if job["status"] != "done":
        raise HTTPException(status_code=425, detail=f"Analiz henüz tamamlanmadı: {job['status']}")

    from app.services import report
    pdf_bytes = report.generate(job_id, job)

    filename = f"geohan_{job_id[:8]}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
