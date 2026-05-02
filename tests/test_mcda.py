from app.services import mcda


class TestSlopeScore:
    def test_flat(self):
        assert mcda._slope_score(0) == 100

    def test_optimal_boundary(self):
        assert mcda._slope_score(5) == 100

    def test_mid_slope(self):
        s = mcda._slope_score(7)   # 100 - (7-5)*10 = 80
        assert 50 < s < 100

    def test_steep_boundary(self):
        assert mcda._slope_score(15) == 0   # 100 - (15-5)*10 = 0

    def test_too_steep(self):
        assert mcda._slope_score(20) == 0
        assert mcda._slope_score(100) == 0


class TestGhiScore:
    def test_excellent(self):
        assert mcda._ghi_score(2200) == 100

    def test_at_threshold(self):
        assert mcda._ghi_score(2000) == 100

    def test_mid(self):
        s = mcda._ghi_score(1600)
        assert 0 < s < 100

    def test_low(self):
        assert mcda._ghi_score(1000) == 0

    def test_at_low_threshold(self):
        assert mcda._ghi_score(1200) == 0


class TestDistanceScore:
    def test_very_close(self):
        assert mcda._distance_score(0.5, near=1, far=30) == 100

    def test_at_near(self):
        assert mcda._distance_score(1.0, near=1, far=30) == 100

    def test_at_far(self):
        assert mcda._distance_score(30.0, near=1, far=30) == 0

    def test_beyond_far(self):
        assert mcda._distance_score(50.0, near=1, far=30) == 0

    def test_mid(self):
        s = mcda._distance_score(5.0, near=1, far=30)
        assert 0 < s < 100


class TestScore:
    def test_excellent_site(self, flat_sunny_site):
        f = flat_sunny_site
        result = mcda.score(
            f["slope_pct"], f["ghi"], f["aspect_score"], f["shadow_score"],
            f["lc_code"], f["grid_km"], f["road_km"],
            yasal_score=f["yasal_score"], hard_block=f["hard_block"],
        )
        assert result["total"] >= 70
        assert set(result["scores"].keys()) == set(mcda.get_weights().keys())

    def test_hard_block_zeroes_all(self, steep_forested_site):
        f = steep_forested_site
        result = mcda.score(
            f["slope_pct"], f["ghi"], f["aspect_score"], f["shadow_score"],
            f["lc_code"], f["grid_km"], f["road_km"],
            yasal_score=f["yasal_score"], hard_block=f["hard_block"],
        )
        assert result["total"] == 0.0
        assert all(v == 0 for v in result["scores"].values())

    def test_weights_sum_to_one(self):
        total = sum(mcda.get_weights().values())
        assert abs(total - 1.0) < 1e-9

    def test_returns_all_keys(self, flat_sunny_site):
        f = flat_sunny_site
        result = mcda.score(
            f["slope_pct"], f["ghi"], f["aspect_score"], f["shadow_score"],
            f["lc_code"], f["grid_km"], f["road_km"],
        )
        assert "scores" in result and "weights" in result and "total" in result

    def test_lc_code_grassland(self):
        result = mcda.score(3.0, 1800, 90, 95, 30, 2.0, 1.0)
        assert result["scores"]["arazi"] == 100

    def test_lc_code_unknown_defaults(self):
        result = mcda.score(3.0, 1800, 90, 95, 999, 2.0, 1.0)
        assert result["scores"]["arazi"] == 50
