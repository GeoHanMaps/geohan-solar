import json
import math
import os
from pathlib import Path

LC_SCORE = {
    10: 0,   # orman
    20: 80,  # çalılık
    30: 100, # otlak
    40: 50,  # tarım
    50: 0,   # yapılaşmış
    60: 100, # çıplak arazi
    70: 0,   # kar/buz
    80: 0,   # su
    90: 0,   # sulak alan
    95: 0,   # mangrov
    100: 70, # yosun/liken
}

_DEFAULT_WEIGHTS = {
    "egim": 0.08, "ghi": 0.40, "baki": 0.03, "golge": 0.03,
    "arazi": 0.03, "sebeke": 0.28, "erisim": 0.03, "yasal": 0.12,
}

_WEIGHTS_FILE = Path(__file__).parent.parent.parent / "config" / "mcda_weights.json"

# Ağırlık cache: (mtime, weights_dict)
_weights_cache: tuple[float, dict] = (0.0, _DEFAULT_WEIGHTS)


def get_weights() -> dict[str, float]:
    """mcda_weights.json'dan oku; dosya değişmişse yeniden yükle."""
    global _weights_cache
    try:
        mtime = os.path.getmtime(_WEIGHTS_FILE)
        if mtime != _weights_cache[0]:
            data = json.loads(_WEIGHTS_FILE.read_text(encoding="utf-8"))
            _weights_cache = (mtime, data["weights"])
    except Exception:
        pass
    return _weights_cache[1]


def _slope_score(pct: float) -> int:
    if pct <= 5:    return 100
    if pct <= 15:   return int(100 - (pct - 5) * 10)
    return 0


def _ghi_score(ghi: float) -> int:
    if ghi >= 2000: return 100
    if ghi >= 1200: return int((ghi - 1200) / 800 * 100)
    return 0


def _distance_score(km: float, near: float, far: float) -> int:
    if km <= near:  return 100
    if km >= far:   return 0
    return int(100 - (math.log(km / near) / math.log(far / near)) * 100)


def score(
    slope_pct: float,
    ghi: float,
    aspect_score: float,
    shadow_score: float,
    lc_code: int,
    grid_km: float,
    road_km: float,
    yasal_score: int = 100,
    hard_block: bool = False,
) -> dict:
    weights = get_weights()
    scores = {
        "egim":   _slope_score(slope_pct),
        "ghi":    _ghi_score(ghi),
        "baki":   int(aspect_score),
        "golge":  int(shadow_score),
        "arazi":  LC_SCORE.get(lc_code, 50),
        "sebeke": _distance_score(grid_km, near=1, far=30),
        "erisim": _distance_score(road_km, near=0.5, far=10),
        "yasal":  yasal_score,
    }

    if hard_block:
        return {"scores": scores, "weights": weights, "total": 0.0, "hard_block": True}

    total = sum(scores[k] * weights[k] for k in scores)
    return {"scores": scores, "weights": weights, "total": round(total, 1)}
