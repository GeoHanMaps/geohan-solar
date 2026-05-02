"""
Redis-tabanlı job store.
Celery worker ve API aynı Redis'i okur → sunucu restart'ta job kaybolmaz.
Test ortamında _client fakeredis ile replace edilir (tests/conftest.py).
"""

import json

import redis as redis_lib

from app.config import settings

# Module-level attribute — testlerde monkeypatch veya direkt atama ile değiştirilebilir
_client: redis_lib.Redis | None = None

_PREFIX = "geohan:job:"
_TTL    = 60 * 60 * 24 * 7   # 7 gün


def _r() -> redis_lib.Redis:
    global _client
    if _client is None:
        _client = redis_lib.Redis.from_url(settings.redis_url, decode_responses=True)
    return _client


def _key(job_id: str) -> str:
    return f"{_PREFIX}{job_id}"


def _load(job_id: str) -> dict | None:
    raw = _r().get(_key(job_id))
    return json.loads(raw) if raw else None


def _save(job_id: str, data: dict) -> None:
    _r().set(_key(job_id), json.dumps(data, default=str), ex=_TTL)


# ─── Public API ───────────────────────────────────────────────────────────────

def create(job_id: str, meta: dict) -> None:
    _save(job_id, {"status": "pending", "result": None, "error": None, **meta})


def set_running(job_id: str) -> None:
    data = _load(job_id)
    if data:
        data["status"] = "running"
        _save(job_id, data)


def set_done(job_id: str, result: dict) -> None:
    data = _load(job_id)
    if data:
        data["status"] = "done"
        data["result"] = result
        _save(job_id, data)


def set_failed(job_id: str, error: str) -> None:
    data = _load(job_id)
    if data:
        data["status"] = "failed"
        data["error"]  = error
        _save(job_id, data)


def get(job_id: str) -> dict | None:
    return _load(job_id)


def batch_update_progress(job_id: str, completed: int, results: list) -> None:
    data = _load(job_id)
    if data:
        data["completed"] = completed
        data["results"]   = results
        _save(job_id, data)


def list_all() -> list[dict]:
    keys = _r().keys(f"{_PREFIX}*")
    out  = []
    for k in keys:
        raw = _r().get(k)
        if raw:
            d = json.loads(raw)
            out.append({
                "id":     k.removeprefix(_PREFIX),
                "status": d["status"],
                "name":   d.get("name"),
            })
    return out
