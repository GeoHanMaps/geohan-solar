"""
AHP (Analytic Hierarchy Process) — MCDA ağırlık kalibrasyonu.

Saaty ölçeği:
  1 = Eşit önem
  3 = Az üstün        (moderate)
  5 = Belirgin üstün  (strong)
  7 = Çok üstün       (very strong)
  9 = Mutlak üstün    (extreme)
  Ara değerler (2, 4, 6, 8) ince ayrım için kullanılır.
  Kesir (ör. 0.33 ≈ 1/3): ters önem — "A, B'ye göre 3x az önemli"

Tutarlılık Oranı (CR):
  CR < 0.10  → kabul edilir
  CR ≥ 0.10  → karşılaştırmalar yeniden değerlendirilmeli

Yöntem: Satır geometrik ortalaması (eigenvector ile eşdeğer, sayısal olarak kararlı).
"""

import json
import numpy as np
from datetime import datetime, timezone

from app.services.calibrate import CRITERIA, _WEIGHTS_FILE

# Saaty Random Consistency Index (n = 1..10)
_RI = [0.00, 0.00, 0.58, 0.90, 1.12, 1.24, 1.32, 1.41, 1.45, 1.49]

SAATY_SCALE = {
    1: "Eşit önem",
    2: "Az üstün (zayıf)",
    3: "Az üstün (ılımlı)",
    4: "Belirgin–ılımlı arası",
    5: "Belirgin üstün",
    6: "Belirgin–çok arası",
    7: "Çok üstün",
    8: "Çok–mutlak arası",
    9: "Mutlak üstün",
}


def parse_comparisons(raw: dict[str, float]) -> dict[tuple[str, str], float]:
    """
    JSON'daki "ghi_vs_egim": 5 → {("ghi", "egim"): 5.0}
    Kriter adları CRITERIA listesinden doğrulanır.
    """
    valid = set(CRITERIA)
    result: dict[tuple[str, str], float] = {}
    for key, val in raw.items():
        parts = key.split("_vs_")
        if len(parts) != 2:
            raise ValueError(f"Geçersiz karşılaştırma anahtarı: {key!r}  (beklenen: 'a_vs_b')")
        ci, cj = parts
        if ci not in valid:
            raise ValueError(f"Bilinmeyen kriter: {ci!r}")
        if cj not in valid:
            raise ValueError(f"Bilinmeyen kriter: {cj!r}")
        if val <= 0:
            raise ValueError(f"{key}: değer sıfırdan büyük olmalı, alınan {val}")
        result[(ci, cj)] = float(val)
    return result


def build_matrix(comparisons: dict[tuple[str, str], float]) -> np.ndarray:
    """
    Pairwise karşılaştırma matrisini oluştur.
    Belirtilmeyen çiftler eşit (1.0) kabul edilir.
    """
    n = len(CRITERIA)
    idx = {c: i for i, c in enumerate(CRITERIA)}
    A = np.ones((n, n), dtype=float)
    for (ci, cj), v in comparisons.items():
        i, j = idx[ci], idx[cj]
        A[i, j] = v
        A[j, i] = 1.0 / v
    return A


def weights_from_matrix(A: np.ndarray) -> np.ndarray:
    """Satır geometrik ortalaması → normalize ağırlıklar."""
    n = len(A)
    row_gm = np.array([np.prod(A[i]) ** (1.0 / n) for i in range(n)])
    return row_gm / row_gm.sum()


def consistency_ratio(A: np.ndarray, w: np.ndarray) -> tuple[float, float]:
    """
    (lambda_max, CR) döner.
    lambda_max: tutarlı matris için n'e eşit olur.
    CR < 0.10 → tutarlı.
    """
    n = len(A)
    lambda_max = float(np.dot(A @ w, 1.0 / (w + 1e-12)) / n)
    ci = (lambda_max - n) / max(n - 1, 1)
    ri = _RI[n - 1] if n <= len(_RI) else 1.49
    cr = ci / ri if ri > 0 else 0.0
    return round(lambda_max, 4), round(cr, 4)


def ahp_calibrate(
    comparisons: dict[tuple[str, str], float],
    save: bool = False,
    expert_name: str = "Anonim",
) -> dict:
    """
    AHP kalibrasyonunu çalıştır.

    Dönüş:
      {
        "weights":    {"ghi": 0.38, ...},
        "lambda_max": 8.42,
        "cr":         0.042,
        "consistent": True,
        "saved":      False,
      }
    """
    A = build_matrix(comparisons)
    w = weights_from_matrix(A)
    lambda_max, cr = consistency_ratio(A, w)
    consistent = cr < 0.10

    weights = {c: round(float(w[i]), 4) for i, c in enumerate(CRITERIA)}

    saved = False
    if save:
        if not consistent:
            raise ValueError(
                f"CR={cr:.3f} ≥ 0.10 — karşılaştırmalar tutarsız. "
                "Ağırlıklar kaydedilmedi; lütfen karşılaştırmaları gözden geçirin."
            )
        data = {
            "v": 1,
            "weights": weights,
            "calibrated": True,
            "method": "AHP",
            "expert": expert_name,
            "lambda_max": lambda_max,
            "cr": cr,
            "n_comparisons": len(comparisons),
            "calibrated_at": datetime.now(timezone.utc).isoformat(),
            "notes": (
                f"AHP pairwise comparison — {len(comparisons)} karşılaştırma, "
                f"CR={cr:.3f}, uzman={expert_name}"
            ),
        }
        _WEIGHTS_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        saved = True

    return {
        "weights":    weights,
        "lambda_max": lambda_max,
        "cr":         cr,
        "consistent": consistent,
        "saved":      saved,
    }
