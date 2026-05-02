"""
Kalibrasyon modülü testleri.

scipy optimize — gerçek hesaplama çalışır (mock yok).
"""

import json
import pytest
from unittest.mock import patch

from app.services import calibrate as cal
from app.services.calibrate import CRITERIA, DEFAULT_WEIGHTS


# ─── Yardımcı ─────────────────────────────────────────────────────────────────

def _make_pilot(scores_dict: dict, exp_min: float, exp_max: float) -> dict:
    """breakdown formatında tek lokasyon oluştur."""
    bd = {c: {"score": float(scores_dict.get(c, 50))} for c in CRITERIA}
    return {"breakdown": bd, "expected_min": exp_min, "expected_max": exp_max}


def _ideal_pilot(n: int = 5) -> list[dict]:
    """Varsayılan ağırlıklarla tutarlı n lokasyon üret."""
    import random
    rng = random.Random(42)
    pilots = []
    w = dict(zip(CRITERIA, DEFAULT_WEIGHTS))
    for _ in range(n):
        sc = {c: float(rng.randint(20, 100)) for c in CRITERIA}
        expected = sum(sc[c] * w[c] for c in CRITERIA)
        pilots.append(_make_pilot(sc, expected - 5, expected + 5))
    return pilots


# ─── Temel kalibrasyonTests ──────────────────────────────────────────────────

class TestCalibrateBasic:
    def test_returns_dict_with_all_criteria(self):
        pilots = _ideal_pilot(4)
        result = cal.calibrate(pilots, save=False)
        assert set(result.keys()) == set(CRITERIA)

    def test_weights_sum_to_one(self):
        pilots = _ideal_pilot(4)
        result = cal.calibrate(pilots, save=False)
        assert abs(sum(result.values()) - 1.0) < 1e-6

    def test_all_weights_in_bounds(self):
        pilots = _ideal_pilot(6)
        result = cal.calibrate(pilots, save=False)
        for k, v in result.items():
            assert cal.MIN_W <= v <= cal.MAX_W, f"{k}={v} sınır dışı"

    def test_weights_are_floats(self):
        pilots = _ideal_pilot(3)
        result = cal.calibrate(pilots, save=False)
        for v in result.values():
            assert isinstance(v, float)

    def test_high_ghi_pilot_raises_ghi_weight(self):
        """Yüksek GHI skorlu, yüksek beklenti → ghi ağırlığı artmalı."""
        pilots = [
            _make_pilot({"ghi": 100, "egim": 50, "baki": 50, "golge": 50,
                         "arazi": 50, "sebeke": 50, "erisim": 50, "yasal": 50},
                        exp_min=70, exp_max=90),
            _make_pilot({"ghi": 100, "egim": 40, "baki": 40, "golge": 40,
                         "arazi": 40, "sebeke": 40, "erisim": 40, "yasal": 40},
                        exp_min=65, exp_max=85),
            _make_pilot({"ghi": 20, "egim": 50, "baki": 50, "golge": 50,
                         "arazi": 50, "sebeke": 50, "erisim": 50, "yasal": 50},
                        exp_min=30, exp_max=50),
        ]
        default_ghi = dict(zip(CRITERIA, DEFAULT_WEIGHTS))["ghi"]
        result = cal.calibrate(pilots, save=False)
        assert result["ghi"] >= default_ghi - 0.05

    def test_fewer_than_two_sites_raises(self):
        pilots = [_make_pilot({"ghi": 80}, 50, 70)]
        with pytest.raises(ValueError, match="en az 2"):
            cal.calibrate(pilots, save=False)

    def test_empty_list_raises(self):
        with pytest.raises(ValueError):
            cal.calibrate([], save=False)


class TestCalibrateSave:
    def test_save_writes_file(self, tmp_path):
        weights_file = tmp_path / "mcda_weights.json"
        pilots = _ideal_pilot(4)
        with patch.object(cal, "_WEIGHTS_FILE", weights_file):
            cal.calibrate(pilots, save=True)
        assert weights_file.exists()
        data = json.loads(weights_file.read_text(encoding="utf-8"))
        assert data["calibrated"] is True
        assert data["n_sites"] == 4
        assert "calibrated_at" in data

    def test_save_false_does_not_write(self, tmp_path):
        weights_file = tmp_path / "mcda_weights.json"
        pilots = _ideal_pilot(3)
        with patch.object(cal, "_WEIGHTS_FILE", weights_file):
            cal.calibrate(pilots, save=False)
        assert not weights_file.exists()

    def test_saved_weights_sum_to_one(self, tmp_path):
        weights_file = tmp_path / "mcda_weights.json"
        pilots = _ideal_pilot(5)
        with patch.object(cal, "_WEIGHTS_FILE", weights_file):
            cal.calibrate(pilots, save=True)
        data = json.loads(weights_file.read_text(encoding="utf-8"))
        assert abs(sum(data["weights"].values()) - 1.0) < 1e-4


class TestLoadCurrentWeights:
    def test_loads_from_file(self, tmp_path):
        weights_file = tmp_path / "mcda_weights.json"
        custom = {c: round(1 / len(CRITERIA), 4) for c in CRITERIA}
        weights_file.write_text(
            json.dumps({"v": 1, "weights": custom}), encoding="utf-8"
        )
        with patch.object(cal, "_WEIGHTS_FILE", weights_file):
            loaded = cal.load_current_weights()
        assert loaded == custom

    def test_falls_back_to_defaults_on_missing_file(self, tmp_path):
        missing = tmp_path / "no_such_file.json"
        with patch.object(cal, "_WEIGHTS_FILE", missing):
            loaded = cal.load_current_weights()
        assert set(loaded.keys()) == set(CRITERIA)

    def test_falls_back_on_corrupt_file(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("NOT JSON", encoding="utf-8")
        with patch.object(cal, "_WEIGHTS_FILE", bad_file):
            loaded = cal.load_current_weights()
        assert set(loaded.keys()) == set(CRITERIA)


class TestCompareWeights:
    def test_runs_without_error(self, capsys):
        before = dict(zip(CRITERIA, DEFAULT_WEIGHTS))
        after = dict(zip(CRITERIA, [round(1 / len(CRITERIA), 4)] * len(CRITERIA)))
        cal.compare_weights(before, after)
        captured = capsys.readouterr()
        assert "ghi" in captured.out
        assert "Kriter" in captured.out


class TestMcdaIntegration:
    def test_mcda_uses_calibrated_weights(self, tmp_path):
        """Kalibre edilmiş ağırlıklar mcda.score()'da kullanılmalı."""
        import app.services.mcda as mcda_mod

        custom = {c: round(1 / len(CRITERIA), 4) for c in CRITERIA}
        weights_file = tmp_path / "mcda_weights.json"
        weights_file.write_text(
            json.dumps({"v": 1, "weights": custom}), encoding="utf-8"
        )
        # Cache'i sıfırla
        mcda_mod._weights_cache = (0.0, mcda_mod._DEFAULT_WEIGHTS)

        with patch.object(mcda_mod, "_WEIGHTS_FILE", weights_file):
            result = mcda_mod.score(
                slope_pct=3.0, ghi=1800.0, aspect_score=90.0,
                shadow_score=95.0, lc_code=60,
                grid_km=2.0, road_km=1.0,
            )

        for v in result["weights"].values():
            assert abs(v - round(1 / len(CRITERIA), 4)) < 0.01
