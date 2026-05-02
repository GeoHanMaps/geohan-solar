"""
MCDA ağırlık kalibrasyon modülü.

Pilot validasyon sonuçlarını kullanarak scipy.optimize ile MCDA ağırlıklarını
beklenen skor aralıklarına en iyi uyan değerlere kalibre eder.

Optimizasyon hedefi: MSE( X @ w, y_target ) → minimum
Kısıtlar:
  - sum(w) == 1.0
  - MIN_W <= w_i <= MAX_W (her ağırlık makul aralıkta kalır)
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

CRITERIA = ["egim", "ghi", "baki", "golge", "arazi", "sebeke", "erisim", "yasal"]
DEFAULT_WEIGHTS = [0.15, 0.25, 0.08, 0.07, 0.05, 0.20, 0.10, 0.10]
MIN_W = 0.03
MAX_W = 0.40

_WEIGHTS_FILE = Path(__file__).parent.parent.parent / "config" / "mcda_weights.json"


def load_current_weights() -> dict[str, float]:
    try:
        data = json.loads(_WEIGHTS_FILE.read_text(encoding="utf-8"))
        return data["weights"]
    except Exception:
        return dict(zip(CRITERIA, DEFAULT_WEIGHTS))


def save_weights(weights: dict[str, float], n_sites: int) -> None:
    data = {
        "v": 1,
        "weights": weights,
        "calibrated": True,
        "calibrated_at": datetime.now(timezone.utc).isoformat(),
        "n_sites": n_sites,
        "notes": f"Otomatik kalibrasyon — {n_sites} pilot lokasyon.",
    }
    _WEIGHTS_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def calibrate(pilot_results: list[dict], save: bool = True) -> dict[str, float]:
    """
    pilot_results öğesi:
      {
        "breakdown": {"egim": {"score": 80}, "ghi": {"score": 95}, ...},
        "expected_min": 60.0,
        "expected_max": 90.0,
      }

    Dönüş: {"egim": 0.14, "ghi": 0.27, ...}
    """
    rows, targets = [], []
    for r in pilot_results:
        bd = r.get("breakdown") or {}
        row = [float(bd.get(c, {}).get("score", 0)) for c in CRITERIA]
        rows.append(row)
        exp_mid = (r.get("expected_min", 0) + r.get("expected_max", 100)) / 2.0
        targets.append(exp_mid)

    if len(rows) < 2:
        raise ValueError(
            f"Kalibrasyon için en az 2 lokasyon gerekli, {len(rows)} sağlandı."
        )

    X = np.array(rows, dtype=float)
    y = np.array(targets, dtype=float)

    def objective(w: np.ndarray) -> float:
        return float(np.mean((X @ w - y) ** 2))

    def grad(w: np.ndarray) -> np.ndarray:
        return 2.0 * X.T @ (X @ w - y) / len(y)

    constraints = {"type": "eq", "fun": lambda w: np.sum(w) - 1.0,
                   "jac": lambda _: np.ones(len(CRITERIA))}
    bounds = [(MIN_W, MAX_W)] * len(CRITERIA)
    w0 = np.array(DEFAULT_WEIGHTS, dtype=float)

    result = minimize(
        objective, w0, jac=grad, method="SLSQP",
        bounds=bounds, constraints=constraints,
        options={"maxiter": 2000, "ftol": 1e-10},
    )

    if not result.success:
        raise RuntimeError(f"Optimizasyon yakınsayamadı: {result.message}")

    w_opt = np.clip(result.x, MIN_W, MAX_W)
    w_opt = w_opt / w_opt.sum()

    calibrated = {c: round(float(w), 4) for c, w in zip(CRITERIA, w_opt)}

    if save:
        save_weights(calibrated, n_sites=len(rows))

    return calibrated


def compare_weights(before: dict[str, float], after: dict[str, float]) -> None:
    print(f"\n{'Kriter':<10} {'Önceki':>10} {'Sonraki':>10} {'Δ':>10}")
    print("-" * 44)
    for k in CRITERIA:
        b, a = before.get(k, 0.0), after.get(k, 0.0)
        arrow = "↑" if a > b + 0.005 else ("↓" if a < b - 0.005 else " ")
        print(f"{k:<10} {b:>10.4f} {a:>10.4f} {a - b:>+10.4f} {arrow}")
    print()
