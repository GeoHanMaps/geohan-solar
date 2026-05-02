import pytest
from app.services import legal


class TestGlobalHardBlocks:
    @pytest.mark.parametrize("lc_code", [70, 80, 90, 95])
    def test_global_hard_block_codes(self, lc_code):
        result = legal.check(0.0, 0.0, lc_code, slope_pct=2.0)
        assert result["hard_block"] is True
        assert result["score"] == 0

    def test_forest_hard_block_tr(self):
        result = legal.check(39.0, 35.0, lc_code=10, slope_pct=5.0, country_code="TR")
        assert result["hard_block"] is True
        assert result["score"] == 0

    def test_forest_hard_block_default(self):
        result = legal.check(0.0, 0.0, lc_code=10, slope_pct=5.0, country_code="DEFAULT")
        assert result["hard_block"] is True


class TestSoftBlocks:
    def test_cropland_soft_block_tr(self):
        result = legal.check(39.0, 35.0, lc_code=40, slope_pct=3.0, country_code="TR")
        assert result["hard_block"] is False
        assert result["score"] == 40

    def test_cropland_soft_block_default(self):
        result = legal.check(0.0, 0.0, lc_code=40, slope_pct=3.0, country_code="DEFAULT")
        assert result["hard_block"] is False
        assert result["score"] == 40

    def test_cropland_hard_block_de(self):
        result = legal.check(51.0, 10.0, lc_code=40, slope_pct=5.0, country_code="DE")
        assert result["hard_block"] is True
        assert result["score"] == 0


class TestSlopeLimit:
    def test_slope_over_tr_limit(self):
        result = legal.check(39.0, 35.0, lc_code=60, slope_pct=16.0, country_code="TR")
        assert result["hard_block"] is True
        assert result["score"] == 0

    def test_slope_within_tr_limit(self):
        result = legal.check(39.0, 35.0, lc_code=60, slope_pct=14.0, country_code="TR")
        assert result["hard_block"] is False

    def test_slope_over_de_limit(self):
        result = legal.check(51.0, 10.0, lc_code=60, slope_pct=11.0, country_code="DE")
        assert result["hard_block"] is True

    def test_default_slope_limit(self):
        result = legal.check(0.0, 0.0, lc_code=60, slope_pct=21.0, country_code="DEFAULT")
        assert result["hard_block"] is True


class TestCleanSite:
    @pytest.mark.parametrize("lc_code", [20, 30, 60, 100])
    def test_permissible_lc_codes(self, lc_code):
        result = legal.check(37.0, 32.0, lc_code=lc_code, slope_pct=5.0, country_code="TR")
        assert result["score"] == 100
        assert result["hard_block"] is False

    def test_unknown_country_uses_default(self):
        result = legal.check(0.0, 0.0, lc_code=60, slope_pct=5.0, country_code="XX")
        assert result["score"] == 100
        assert result["country_code"] == "XX"

    def test_wdpa_not_checked(self):
        result = legal.check(37.0, 32.0, lc_code=60, slope_pct=5.0)
        assert result["wdpa_checked"] is False


class TestAvailableCountries:
    def test_returns_list(self):
        countries = legal.available_countries()
        assert isinstance(countries, list)
        assert "TR" in countries
        assert "DEFAULT" not in countries

    def test_all_have_rules(self):
        for code in legal.available_countries():
            r = legal.check(0.0, 0.0, lc_code=60, slope_pct=5.0, country_code=code)
            assert "score" in r
