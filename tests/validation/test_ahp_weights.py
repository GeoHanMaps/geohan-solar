"""
Sprint 8 — AHP Kalibrasyon Regresyon Testleri.

config/ahp_comparisons.json dosyasındaki uzman karşılaştırmalarının
kalite ve tutarlılık garantilerini korur.

Çalıştır:
  pytest tests/validation/test_ahp_weights.py -v
"""

import json
from pathlib import Path

import pytest

from app.services.ahp import parse_comparisons, ahp_calibrate
from app.services.calibrate import CRITERIA

_COMP_FILE = Path(__file__).parent.parent.parent / "config" / "ahp_comparisons.json"
_data      = json.loads(_COMP_FILE.read_text(encoding="utf-8"))
_pairs     = parse_comparisons(_data["comparisons"])
_RESULT    = ahp_calibrate(_pairs, save=False)
_WEIGHTS   = _RESULT["weights"]


class TestAhpConsistency:
    def test_cr_below_threshold(self):
        """CR < 0.10 — Saaty tutarlılık kriteri."""
        assert _RESULT["cr"] < 0.10, (
            f"CR={_RESULT['cr']:.3f} ≥ 0.10 — ahp_comparisons.json yeniden ayarlanmalı."
        )

    def test_weights_sum_to_one(self):
        assert abs(sum(_WEIGHTS.values()) - 1.0) < 1e-3

    def test_all_weights_positive(self):
        for c, v in _WEIGHTS.items():
            assert v > 0, f"{c} ağırlığı sıfır veya negatif: {v}"

    def test_all_criteria_present(self):
        assert set(_WEIGHTS.keys()) == set(CRITERIA)

    def test_28_comparisons_provided(self):
        """Tüm 8×7/2 = 28 çift girilmiş olmalı."""
        expected = len(CRITERIA) * (len(CRITERIA) - 1) // 2
        assert len(_pairs) == expected, (
            f"{len(_pairs)}/{expected} karşılaştırma — eksik çiftler 1.0 varsayılır."
        )


class TestAhpDomainConstraints:
    def test_ghi_highest_weight(self):
        """GHI fiziksel olarak en belirleyici kriter olmalı."""
        assert _WEIGHTS["ghi"] == max(_WEIGHTS.values()), (
            f"GHI en yüksek ağırlığa sahip değil: {_WEIGHTS}"
        )

    def test_sebeke_second_highest(self):
        """Şebeke bağlantısı, GHI'dan sonra en kritik maliyet kalemi."""
        sorted_w = sorted(_WEIGHTS.values(), reverse=True)
        assert _WEIGHTS["sebeke"] >= sorted_w[1] - 0.02, (
            f"Şebeke ağırlığı ({_WEIGHTS['sebeke']:.4f}) ikinci sırada değil."
        )

    def test_ghi_above_egim(self):
        assert _WEIGHTS["ghi"] > _WEIGHTS["egim"]

    def test_ghi_above_yasal(self):
        assert _WEIGHTS["ghi"] > _WEIGHTS["yasal"]

    def test_sebeke_above_egim(self):
        assert _WEIGHTS["sebeke"] > _WEIGHTS["egim"]

    def test_yasal_above_minor_criteria(self):
        """Yasal kısıt, bakı/gölge/arazi/erişimden önemli olmalı."""
        for c in ("baki", "golge", "arazi", "erisim"):
            assert _WEIGHTS["yasal"] > _WEIGHTS[c], (
                f"yasal ({_WEIGHTS['yasal']:.4f}) ≤ {c} ({_WEIGHTS[c]:.4f})"
            )

    def test_egim_above_minor_criteria(self):
        """Eğim, bakı/gölge/arazi/erişimden önemli olmalı."""
        for c in ("baki", "golge", "arazi", "erisim"):
            assert _WEIGHTS["egim"] > _WEIGHTS[c], (
                f"egim ({_WEIGHTS['egim']:.4f}) ≤ {c} ({_WEIGHTS[c]:.4f})"
            )


class TestAhpVsSlsqpAlignment:
    """AHP ve SLSQP ağırlıkları aynı öncelik sıralamasını paylaşmalı."""

    _SLSQP = {
        "egim": 0.08, "ghi": 0.40, "baki": 0.03, "golge": 0.03,
        "arazi": 0.03, "sebeke": 0.28, "erisim": 0.03, "yasal": 0.12,
    }

    def test_top2_same_order(self):
        """Her iki yöntemde de ghi ve sebeke top-2."""
        ahp_top2  = set(sorted(_WEIGHTS, key=_WEIGHTS.get, reverse=True)[:2])
        slsqp_top2 = set(sorted(self._SLSQP, key=self._SLSQP.get, reverse=True)[:2])
        assert ahp_top2 == slsqp_top2, (
            f"AHP top-2: {ahp_top2}  SLSQP top-2: {slsqp_top2}"
        )

    def test_ghi_delta_within_10pp(self):
        """AHP ve SLSQP GHI ağırlıkları 10pp'den fazla ayrışmamalı."""
        delta = abs(_WEIGHTS["ghi"] - self._SLSQP["ghi"])
        assert delta <= 0.10, f"GHI delta {delta:.3f} > 0.10"

    def test_sebeke_delta_within_10pp(self):
        delta = abs(_WEIGHTS["sebeke"] - self._SLSQP["sebeke"])
        assert delta <= 0.10, f"Sebeke delta {delta:.3f} > 0.10"
