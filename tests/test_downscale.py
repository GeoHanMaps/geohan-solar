import pytest
from unittest.mock import patch
from app.services import downscale, cache


@pytest.fixture(autouse=True)
def no_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "_CACHE_DIR", tmp_path)


class TestTerrainCorrection:
    def test_flat_terrain_near_one(self):
        factor = downscale.terrain_correction(37.87, 32.49, slope_deg=0.0, aspect_deg=180.0)
        assert 0.95 <= factor <= 1.05

    def test_optimal_tilt_above_one(self):
        # Kuzey yarıküre, güney bakı, orta eğim → düzlükten daha iyi
        factor = downscale.terrain_correction(37.87, 32.49, slope_deg=30.0, aspect_deg=180.0)
        assert factor > 1.0

    def test_north_facing_below_one(self):
        # Kuzey yarıküre, kuzey bakı → düzlükten kötü
        factor = downscale.terrain_correction(37.87, 32.49, slope_deg=20.0, aspect_deg=0.0)
        assert factor < 1.0

    def test_southern_hemisphere_south_facing_poor(self):
        # Güney yarıküre, güney bakı → kötü (optimum kuzey)
        factor = downscale.terrain_correction(-33.9, 25.0, slope_deg=20.0, aspect_deg=180.0)
        assert factor < 1.0

    def test_result_within_bounds(self):
        for slope in [0, 5, 15, 30, 45]:
            for aspect in [0, 90, 180, 270]:
                f = downscale.terrain_correction(37.0, 32.0, float(slope), float(aspect))
                assert 0.5 <= f <= 1.5

    def test_cache_hit_on_second_call(self):
        with patch("app.services.downscale._pvlib_factor", return_value=1.05) as mock_fn:
            downscale.terrain_correction(37.0, 32.0, 10.0, 180.0)
            downscale.terrain_correction(37.0, 32.0, 10.0, 180.0)
        assert mock_fn.call_count == 1

    def test_geometric_fallback_on_pvlib_error(self):
        with patch("app.services.downscale._pvlib_factor", side_effect=ImportError):
            factor = downscale.terrain_correction(37.0, 32.0, 10.0, 180.0)
        assert 0.5 <= factor <= 1.5


class TestGeometricFactor:
    def test_flat_returns_near_one(self):
        f = downscale._geometric_factor(lat=37.0, slope_deg=0.0, aspect_deg=180.0)
        assert 0.9 <= f <= 1.1

    def test_south_facing_better_than_north_nh(self):
        south = downscale._geometric_factor(37.0, 20.0, 180.0)
        north = downscale._geometric_factor(37.0, 20.0, 0.0)
        assert south > north

    def test_north_facing_better_than_south_sh(self):
        south = downscale._geometric_factor(-33.0, 20.0, 180.0)
        north = downscale._geometric_factor(-33.0, 20.0, 0.0)
        assert north > south


class TestSkyViewFactor:
    def test_flat_is_one(self):
        assert downscale.sky_view_factor(0.0) == pytest.approx(1.0)

    def test_vertical_is_half(self):
        assert downscale.sky_view_factor(90.0) == pytest.approx(0.5)

    def test_decreases_with_slope(self):
        svf_10 = downscale.sky_view_factor(10.0)
        svf_30 = downscale.sky_view_factor(30.0)
        assert svf_10 > svf_30


class TestHorizonShading:
    def test_no_profile_returns_one(self):
        assert downscale.horizon_shading_factor(None) == 1.0

    def test_flat_horizon_no_loss(self):
        profile = {az: 0.0 for az in range(0, 360, 10)}
        assert downscale.horizon_shading_factor(profile) == pytest.approx(1.0)

    def test_high_horizon_causes_loss(self):
        profile = {az: 20.0 for az in range(0, 360, 10)}
        f = downscale.horizon_shading_factor(profile)
        assert f < 1.0

    def test_max_loss_capped(self):
        profile = {az: 90.0 for az in range(0, 360, 10)}
        f = downscale.horizon_shading_factor(profile)
        assert f >= 0.70   # en fazla %30 kayıp


class TestPvlibFactorWithHorizon:
    """_pvlib_factor ile ufuk profili maskeleme testleri."""

    def test_zero_horizon_matches_no_horizon(self):
        # 0° ufuk → pvlib zaten gece saatlerinde DNI=0, maskeleme değişmez
        profile_zero = {az: 0.0 for az in range(0, 360, 10)}
        f_none = downscale._pvlib_factor(37.0, 32.0, 10.0, 180.0, None)
        f_zero = downscale._pvlib_factor(37.0, 32.0, 10.0, 180.0, profile_zero)
        assert f_zero == pytest.approx(f_none, rel=0.01)

    def test_high_horizon_reduces_factor(self):
        # Yüksek ufuk → daha fazla gölge → daha düşük çarpan
        profile_flat = {az: 0.0  for az in range(0, 360, 10)}
        profile_high = {az: 25.0 for az in range(0, 360, 10)}
        f_flat = downscale._pvlib_factor(37.0, 32.0, 10.0, 180.0, profile_flat)
        f_high = downscale._pvlib_factor(37.0, 32.0, 10.0, 180.0, profile_high)
        assert f_high < f_flat

    def test_partial_horizon_between_extremes(self):
        # Kısmi ufuk (sadece kuzey yönleri yüksek) → tam-flat ile tam-high arasında
        profile_mixed = {}
        for az in range(0, 360, 10):
            profile_mixed[az] = 20.0 if az < 180 else 0.0
        profile_flat = {az: 0.0 for az in range(0, 360, 10)}
        profile_high = {az: 20.0 for az in range(0, 360, 10)}
        f_mixed = downscale._pvlib_factor(37.0, 32.0, 10.0, 180.0, profile_mixed)
        f_flat  = downscale._pvlib_factor(37.0, 32.0, 10.0, 180.0, profile_flat)
        f_high  = downscale._pvlib_factor(37.0, 32.0, 10.0, 180.0, profile_high)
        assert f_high <= f_mixed <= f_flat

    def test_result_still_within_physical_bounds(self):
        profile_high = {az: 30.0 for az in range(0, 360, 10)}
        f = downscale._pvlib_factor(37.0, 32.0, 10.0, 180.0, profile_high)
        assert 0.0 < f < 2.0

    def test_terrain_correction_passes_horizon(self):
        # terrain_correction; horizon verilince _pvlib_factor'ı hor=1 cache ile çağırır
        profile = {az: 5.0 for az in range(0, 360, 10)}
        with patch("app.services.downscale._pvlib_factor", return_value=0.95) as mock_fn:
            downscale.terrain_correction(37.0, 32.0, 10.0, 180.0, horizon_profile=profile)
        args, kwargs = mock_fn.call_args
        assert kwargs.get("horizon_profile") == profile or args[4] == profile

    def test_cache_separates_horizon_and_no_horizon(self):
        # hor=0 ve hor=1 cache key'leri farklı → iki ayrı sonuç saklanabilir
        profile = {az: 15.0 for az in range(0, 360, 10)}
        with patch("app.services.downscale._pvlib_factor", return_value=1.05) as mock_no_h:
            downscale.terrain_correction(38.0, 33.0, 5.0, 180.0, horizon_profile=None)
        with patch("app.services.downscale._pvlib_factor", return_value=0.92) as mock_h:
            downscale.terrain_correction(38.0, 33.0, 5.0, 180.0, horizon_profile=profile)
        # Her ikisi de hesaplandı (cache'den gelmiyor — farklı key)
        assert mock_no_h.call_count == 1
        assert mock_h.call_count == 1


class TestApply:
    def test_no_correction(self):
        assert downscale.apply(1800.0, 1.0) == pytest.approx(1800.0)

    def test_positive_correction(self):
        result = downscale.apply(1800.0, 1.1)
        assert result == pytest.approx(1980.0)

    def test_upper_bound(self):
        result = downscale.apply(1800.0, 2.0)
        assert result == pytest.approx(1800.0 * 1.5)

    def test_never_negative(self):
        assert downscale.apply(1800.0, 0.0) == 0.0
