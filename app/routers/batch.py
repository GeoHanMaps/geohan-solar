import uuid
from fastapi import APIRouter, Depends, HTTPException, Request

from app import store
from app.auth import get_current_user
from app.limiter import limiter
from app.config import settings
from app.schemas import (
    BatchRequest, BatchJobResponse, BatchLocationResult,
    CriterionScore, ScoreBreakdown, CapacityResult, FinancialResult,
)

router = APIRouter(prefix="/api/v1/batch", tags=["batch"])


@router.post("", response_model=BatchJobResponse, status_code=202,
             summary="Çoklu lokasyon batch analizi başlat")
@limiter.limit(settings.rate_limit_analyses)
def create_batch(request: Request, req: BatchRequest,
                 _: str = Depends(get_current_user)):
    from app.tasks import batch_task
    batch_id = str(uuid.uuid4())
    store.create(batch_id, {
        "type": "batch",
        "total_locations": len(req.locations),
        "completed": 0,
        "results": [],
    })
    batch_task.delay(batch_id, req.model_dump())
    return BatchJobResponse(
        id=batch_id, status="pending",
        total_locations=len(req.locations), completed=0,
    )


@router.get("/{batch_id}", response_model=BatchJobResponse,
            summary="Batch iş durumu ve sıralı sonuçlar")
def get_batch(batch_id: str, _: str = Depends(get_current_user)):
    job = store.get(batch_id)
    if not job:
        raise HTTPException(status_code=404, detail="Batch job bulunamadı")

    results = []
    if job["status"] == "done" and job.get("result"):
        raw = job["result"].get("results", [])
        for r in raw:
            bd = r["breakdown"]
            results.append(BatchLocationResult(
                rank=r["rank"], lat=r["lat"], lon=r["lon"], name=r.get("name"),
                total_score=r["total_score"],
                breakdown=ScoreBreakdown(**{k: CriterionScore(**v) for k, v in bd.items()}),
                capacity=CapacityResult(**r["capacity"]),
                financial=FinancialResult(**r["financial"]),
            ))

    return BatchJobResponse(
        id=batch_id,
        status=job["status"],
        total_locations=job.get("total_locations", 0),
        completed=job.get("completed", 0),
        results=results,
        error=job.get("error"),
    )
