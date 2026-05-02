import hashlib
import json
import time
from pathlib import Path

_CACHE_DIR = Path(__file__).parent.parent.parent / "cache"
_CACHE_DIR.mkdir(exist_ok=True)

_MONTH_DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def _key(namespace: str, params: dict) -> str:
    raw = namespace + json.dumps(params, sort_keys=True)
    return hashlib.sha1(raw.encode(), usedforsecurity=False).hexdigest()


def get(namespace: str, **params):
    path = _CACHE_DIR / f"{_key(namespace, params)}.json"
    if not path.exists():
        return None
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
        if time.time() > entry["expires_at"]:
            path.unlink(missing_ok=True)
            return None
        return entry["value"]
    except Exception:
        return None


def set(namespace: str, value, ttl_days: float, **params) -> None:
    path = _CACHE_DIR / f"{_key(namespace, params)}.json"
    try:
        path.write_text(
            json.dumps({"value": value, "expires_at": time.time() + ttl_days * 86400}),
            encoding="utf-8",
        )
    except Exception:
        pass


def clear_expired() -> int:
    removed = 0
    for p in _CACHE_DIR.glob("*.json"):
        try:
            entry = json.loads(p.read_text(encoding="utf-8"))
            if time.time() > entry.get("expires_at", 0):
                p.unlink()
                removed += 1
        except Exception:
            p.unlink()
            removed += 1
    return removed
