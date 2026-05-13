import uuid
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.security import OAuth2PasswordBearer
import io

from sqlalchemy.orm import Session

from app import store
from app.auth import decode_token, get_current_user
from app.db import get_session
from app.limiter import limiter
from app.config import settings
from app.models.credit_transaction import REASON_ANALYSIS
from app.schemas import (
    AnalysisRequest, AnalysisResult, JobResponse, JobListItem,
)
from app.services.credits import require_credit

ANALYSIS_COST = 1

router = APIRouter(prefix="/api/v1/analyses", tags=["analyses"])

_oauth2 = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")


# ─── ENDPOINTS ────────────────────────────────────────────────────────────────

@router.post("", response_model=JobResponse, status_code=202,
             summary="Yeni analiz başlat")
@limiter.limit(settings.rate_limit_analyses)
def create_analysis(request: Request, req: AnalysisRequest,
                    token: str = Depends(_oauth2),
                    session: Session = Depends(get_session)):
    """
    Analizi Celery worker'a gönderir, hemen `job_id` döner.
    DB user'lar için 1 kredi düşülür; legacy admin token bypass eder
    (`credit_transactions`'a `admin_bypass` audit row yazılır).
    Sonucu `GET /api/v1/analyses/{id}` ile sorgula.
    """
    from app.tasks import analyse_task
    payload = decode_token(token)
    job_id = str(uuid.uuid4())
    require_credit(session, payload, cost=ANALYSIS_COST,
                   reason=REASON_ANALYSIS, reference_id=job_id)
    session.commit()
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
    pdf_bytes = report.generate(job_id, job, narrative=job.get("narrative"))

    filename = f"geohan_{job_id[:8]}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
