"""
AHP modülü testleri.
"""

import json
import pytest
import numpy as np
from unittest.mock import patch

from app.services import ahp as ahp_mod
from app.services.ahp import (
    parse_comparisons, build_matrix, weights_from_matrix,
    consistency_ratio, ahp_calibrate,
)
from app.services.calibrate import CRITERIA


# ─── Yardımcılar ──────────────────────────────────────────────────────────────

def _identity_comparisons() -> dict[tuple[str, str], float]:
    """Tüm çiftler eşit → w_i = 1/n, CR = 0."""
    return {(CRITERIA[i], CRITERIA[j]): 1.0
            for i in range(len(CRITERIA)) for j in range(i + 1, len(CRITERIA))}


def _perfect_comparisons(target_weights: dict[str, float]) -> dict[tuple[str, str], float]:
    """Verilen ağırlıklara tam tutarlı pairwise matris üret."""
    pairs = {}
    for i, ci in enumerate(CRITERIA):
        for j in range(i + 1, len(CRITERIA)):
            cj = CRITERIA[j]
            pairs[(ci, cj)] = target_weights[ci] / target_weights[cj]
    return pairs


# ─── parse_comparisons ────────────────────────────────────────────────────────

class TestParseComparisons:
    def test_valid_key(self):
        raw = {"ghi_vs_egim": 5}
        result = parse_comparisons(raw)
        assert result[("ghi", "egim")] == 5.0

    def test_unknown_criterion_raises(self):
        with pytest.raises(ValueError, match="Bilinmeyen"):
            parse_comparisons({"ghi_vs_invalid": 3})

    def test_bad_key_format_raises(self):
        with pytest.raises(ValueError, match="Geçersiz"):
            parse_comparisons({"ghi__egim": 3})

    def test_zero_value_raises(self):
        with pytest.raises(ValueError, match="sıfırdan büyük"):
            parse_comparisons({"ghi_vs_egim": 0})

    def test_fraction_value(self):
        raw = {"egim_vs_ghi": 0.2}
        result = parse_comparisons(raw)
        assert abs(result[("egim", "ghi")] - 0.2) < 1e-9


# ─── build_matrix ─────────────────────────────────────────────────────────────

class TestBuildMatrix:
    def test_diagonal_is_one(self):
        A = build_matrix(_identity_comparisons())
        assert np.allclose(np.diag(A), 1.0)

    def test_reciprocal_symmetry(self):
        pairs = {("ghi", "egim"): 5.0}
        A = build_matrix(pairs)
        i = CRITERIA.index("ghi")
        j = CRITERIA.index("egim")
        assert abs(A[i, j] - 5.0) < 1e-9
        assert abs(A[j, i] - 1 / 5.0) < 1e-9

    def test_shape(self):
        A = build_matrix({})
        assert A.shape == (len(CRITERIA), len(CRITERIA))

    def test_unspecified_pairs_default_one(self):
        A = build_matrix({})
        assert np.allclose(A, 1.0)


# ─── weights_from_matrix ──────────────────────────────────────────────────────

class TestWeightsFromMatrix:
    def test_identity_gives_equal_weights(self):
        A = build_matrix(_identity_comparisons())
        w = weights_from_matrix(A)
        expected = 1 / len(CRITERIA)
        assert np.allclose(w, expected, atol=1e-6)

    def test_weights_sum_to_one(self):
        A = build_matrix(_identity_comparisons())
        w = weights_from_matrix(A)
        assert abs(w.sum() - 1.0) < 1e-9

    def test_weights_all_positive(self):
        A = build_matrix(_identity_comparisons())
        w = weights_from_matrix(A)
        assert np.all(w > 0)

    def test_dominant_criterion_gets_highest_weight(self):
        pairs = {("ghi", c): 9.0 for c in CRITERIA if c != "ghi"}
        A = build_matrix(pairs)
        w = weights_from_matrix(A)
        ghi_idx = CRITERIA.index("ghi")
        assert w[ghi_idx] == w.max()


# ─── consistency_ratio ────────────────────────────────────────────────────────

class TestConsistencyRatio:
    def test_perfect_matrix_cr_near_zero(self):
        target = dict(zip(CRITERIA, [0.40, 0.28, 0.08, 0.07, 0.05, 0.03, 0.03, 0.06]))
        A = build_matrix(_perfect_comparisons(target))
        w = weights_from_matrix(A)
        _, cr = consistency_ratio(A, w)
        assert cr < 0.01

    def test_identity_matrix_cr_zero(self):
        A = build_matrix(_identity_comparisons())
        w = weights_from_matrix(A)
        _, cr = consistency_ratio(A, w)
        assert cr < 1e-6

    def test_returns_two_floats(self):
        A = build_matrix(_identity_comparisons())
        w = weights_from_matrix(A)
        result = consistency_ratio(A, w)
        assert len(result) == 2
        assert all(isinstance(v, float) for v in result)

    def test_inconsistent_matrix_cr_above_threshold(self):
        # Kasıtlı tutarsız karşılaştırma: A>B(9), B>C(9), ama C>A(9) → döngüsel
        pairs = {
            ("ghi", "egim"): 9.0,
            ("egim", "sebeke"): 9.0,
            ("sebeke", "ghi"): 9.0,
        }
        A = build_matrix(pairs)
        w = weights_from_matrix(A)
        _, cr = consistency_ratio(A, w)
        assert cr > 0.10


# ─── ahp_calibrate ────────────────────────────────────────────────────────────

class TestAhpCalibrate:
    def test_returns_all_criteria(self):
        result = ahp_calibrate(_identity_comparisons(), save=False)
        assert set(result["weights"].keys()) == set(CRITERIA)

    def test_weights_sum_to_one(self):
        result = ahp_calibrate(_identity_comparisons(), save=False)
        assert abs(sum(result["weights"].values()) - 1.0) < 1e-4

    def test_consistent_flag_true_for_identity(self):
        result = ahp_calibrate(_identity_comparisons(), save=False)
        assert result["consistent"] is True

    def test_cr_in_result(self):
        result = ahp_calibrate(_identity_comparisons(), save=False)
        assert "cr" in result
        assert isinstance(result["cr"], float)

    def test_save_false_no_file(self, tmp_path):
        weights_file = tmp_path / "mcda_weights.json"
        with patch.object(ahp_mod, "_WEIGHTS_FILE", weights_file):
            ahp_calibrate(_identity_comparisons(), save=False)
        assert not weights_file.exists()

    def test_save_true_writes_file(self, tmp_path):
        weights_file = tmp_path / "mcda_weights.json"
        with patch.object(ahp_mod, "_WEIGHTS_FILE", weights_file):
            ahp_calibrate(_identity_comparisons(), save=True)
        assert weights_file.exists()
        data = json.loads(weights_file.read_text(encoding="utf-8"))
        assert data["method"] == "AHP"
        assert data["calibrated"] is True
        assert abs(sum(data["weights"].values()) - 1.0) < 1e-4

    def test_save_inconsistent_raises(self, tmp_path):
        weights_file = tmp_path / "mcda_weights.json"
        pairs = {
            ("ghi", "egim"): 9.0,
            ("egim", "sebeke"): 9.0,
            ("sebeke", "ghi"): 9.0,
        }
        with patch.object(ahp_mod, "_WEIGHTS_FILE", weights_file):
            with pytest.raises(ValueError, match="CR"):
                ahp_calibrate(pairs, save=True)
        assert not weights_file.exists()

    def test_saved_file_has_cr_and_lambda(self, tmp_path):
        weights_file = tmp_path / "mcda_weights.json"
        with patch.object(ahp_mod, "_WEIGHTS_FILE", weights_file):
            ahp_calibrate(_identity_comparisons(), save=True, expert_name="Test")
        data = json.loads(weights_file.read_text(encoding="utf-8"))
        assert "cr" in data
        assert "lambda_max" in data
        assert data["expert"] == "Test"


# ─── Entegrasyon: default ahp_comparisons.json ────────────────────────────────

class TestDefaultComparisons:
    """config/ahp_comparisons.json dosyasıyla entegrasyon."""

    def test_default_file_parses_and_runs(self):
        import json
        from pathlib import Path
        f = Path(__file__).parent.parent / "config" / "ahp_comparisons.json"
        data = json.loads(f.read_text(encoding="utf-8"))
        raw = data["comparisons"]
        pairs = parse_comparisons(raw)
        result = ahp_calibrate(pairs, save=False)
        assert set(result["weights"].keys()) == set(CRITERIA)
        assert abs(sum(result["weights"].values()) - 1.0) < 1e-3

    def test_default_file_cr_acceptable(self):
        import json
        from pathlib import Path
        f = Path(__file__).parent.parent / "config" / "ahp_comparisons.json"
        data = json.loads(f.read_text(encoding="utf-8"))
        pairs = parse_comparisons(data["comparisons"])
        result = ahp_calibrate(pairs, save=False)
        assert result["cr"] < 0.10, (
            f"Default ahp_comparisons.json CR={result['cr']:.3f} >= 0.10 — "
            "dosya yeniden ayarlanmalı."
        )

    def test_default_ghi_highest_weight(self):
        import json
        from pathlib import Path
        f = Path(__file__).parent.parent / "config" / "ahp_comparisons.json"
        data = json.loads(f.read_text(encoding="utf-8"))
        pairs = parse_comparisons(data["comparisons"])
        result = ahp_calibrate(pairs, save=False)
        weights = result["weights"]
        assert weights["ghi"] == max(weights.values())
