import pytest
from app.schemas import PanelTech, TrackingType
from app.services import capacity


class TestCapacityCalculate:
    def test_basic_mono_fixed(self):
        result = capacity.calculate(
            slope_pct=2.0, ghi_annual=1800.0, area_ha=100.0,
            panel_tech=PanelTech.mono, tracking=TrackingType.fixed,
        )
        assert result["mw_per_ha"] > 0
        assert result["total_mw"] == pytest.approx(result["mw_per_ha"] * 100.0, rel=1e-3)
        assert result["annual_gwh"] > 0
        assert result["panel_label"] == "Monokristal"
        assert result["tracking_label"] == "Sabit Egim"

    def test_bifacial_sat(self):
        result = capacity.calculate(
            slope_pct=1.0, ghi_annual=2000.0, area_ha=50.0,
            panel_tech=PanelTech.bifacial, tracking=TrackingType.single_axis,
        )
        assert result["mw_per_ha"] > 0
        assert result["gcr_effective"] < 0.30

    def test_dat_high_slope_penalty(self):
        flat = capacity.calculate(
            slope_pct=0.5, ghi_annual=1800.0, area_ha=10.0,
            panel_tech=PanelTech.mono, tracking=TrackingType.dual_axis,
        )
        steep = capacity.calculate(
            slope_pct=2.0, ghi_annual=1800.0, area_ha=10.0,
            panel_tech=PanelTech.mono, tracking=TrackingType.dual_axis,
        )
        assert flat["mw_per_ha"] > steep["mw_per_ha"]

    def test_sat_slope_penalty_applied(self):
        ok = capacity.calculate(
            slope_pct=2.0, ghi_annual=1800.0, area_ha=10.0,
            panel_tech=PanelTech.mono, tracking=TrackingType.single_axis,
        )
        penalized = capacity.calculate(
            slope_pct=5.0, ghi_annual=1800.0, area_ha=10.0,
            panel_tech=PanelTech.mono, tracking=TrackingType.single_axis,
        )
        assert ok["mw_per_ha"] > penalized["mw_per_ha"]

    def test_gcr_override(self):
        default = capacity.calculate(
            slope_pct=2.0, ghi_annual=1800.0, area_ha=10.0,
            panel_tech=PanelTech.mono, tracking=TrackingType.fixed,
        )
        custom = capacity.calculate(
            slope_pct=2.0, ghi_annual=1800.0, area_ha=10.0,
            panel_tech=PanelTech.mono, tracking=TrackingType.fixed,
            gcr_override=0.20,
        )
        assert custom["mw_per_ha"] < default["mw_per_ha"]

    def test_steep_terrain_reduces_mw(self):
        flat = capacity.calculate(
            slope_pct=0.5, ghi_annual=1800.0, area_ha=10.0,
            panel_tech=PanelTech.mono, tracking=TrackingType.fixed,
        )
        steep = capacity.calculate(
            slope_pct=12.0, ghi_annual=1800.0, area_ha=10.0,
            panel_tech=PanelTech.mono, tracking=TrackingType.fixed,
        )
        assert flat["mw_per_ha"] > steep["mw_per_ha"]

    def test_poly_less_than_mono(self):
        mono = capacity.calculate(
            slope_pct=2.0, ghi_annual=1800.0, area_ha=10.0,
            panel_tech=PanelTech.mono, tracking=TrackingType.fixed,
        )
        poly = capacity.calculate(
            slope_pct=2.0, ghi_annual=1800.0, area_ha=10.0,
            panel_tech=PanelTech.poly, tracking=TrackingType.fixed,
        )
        assert mono["mw_per_ha"] > poly["mw_per_ha"]
