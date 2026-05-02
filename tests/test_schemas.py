import pytest
from pydantic import ValidationError
from app.schemas import (
    AnalysisRequest, PanelTech, TrackingType,
    MapRequest, MapStats, MapJobResponse, BoundaryResult,
)


class TestAnalysisRequest:
    def test_valid_request(self):
        req = AnalysisRequest(lat=37.87, lon=32.49, area_ha=50.0)
        assert req.panel_tech == PanelTech.mono
        assert req.tracking == TrackingType.fixed
        assert req.country_code == "DEFAULT"

    def test_lat_out_of_range(self):
        with pytest.raises(ValidationError):
            AnalysisRequest(lat=91.0, lon=32.0, area_ha=10.0)

    def test_lat_negative_ok(self):
        req = AnalysisRequest(lat=-33.9, lon=25.0, area_ha=10.0)
        assert req.lat == -33.9

    def test_lon_out_of_range(self):
        with pytest.raises(ValidationError):
            AnalysisRequest(lat=37.0, lon=181.0, area_ha=10.0)

    def test_area_zero_rejected(self):
        with pytest.raises(ValidationError):
            AnalysisRequest(lat=37.0, lon=32.0, area_ha=0.0)

    def test_area_negative_rejected(self):
        with pytest.raises(ValidationError):
            AnalysisRequest(lat=37.0, lon=32.0, area_ha=-5.0)

    def test_area_over_max_rejected(self):
        with pytest.raises(ValidationError):
            AnalysisRequest(lat=37.0, lon=32.0, area_ha=60000.0)

    def test_gcr_bounds(self):
        with pytest.raises(ValidationError):
            AnalysisRequest(lat=37.0, lon=32.0, area_ha=10.0, gcr=1.5)
        with pytest.raises(ValidationError):
            AnalysisRequest(lat=37.0, lon=32.0, area_ha=10.0, gcr=0.0)

    def test_valid_gcr(self):
        req = AnalysisRequest(lat=37.0, lon=32.0, area_ha=10.0, gcr=0.35)
        assert req.gcr == 0.35

    def test_country_code_set(self):
        req = AnalysisRequest(lat=37.0, lon=32.0, area_ha=10.0, country_code="TR")
        assert req.country_code == "TR"

    def test_name_max_length(self):
        with pytest.raises(ValidationError):
            AnalysisRequest(lat=37.0, lon=32.0, area_ha=10.0, name="x" * 121)

    @pytest.mark.parametrize("tech", ["mono", "poly", "bifacial"])
    def test_panel_tech_values(self, tech):
        req = AnalysisRequest(lat=37.0, lon=32.0, area_ha=10.0, panel_tech=tech)
        assert req.panel_tech == tech

    @pytest.mark.parametrize("tracking", ["fixed", "single_axis", "dual_axis"])
    def test_tracking_values(self, tracking):
        req = AnalysisRequest(lat=37.0, lon=32.0, area_ha=10.0, tracking=tracking)
        assert req.tracking == tracking

    def test_invalid_panel_tech(self):
        with pytest.raises(ValidationError):
            AnalysisRequest(lat=37.0, lon=32.0, area_ha=10.0, panel_tech="perovskite")


_VALID_POLYGON = {
    "type": "Polygon",
    "coordinates": [[[30.0, 36.0], [31.0, 36.0], [31.0, 37.0],
                     [30.0, 37.0], [30.0, 36.0]]],
}


class TestMapRequest:
    def test_valid_polygon(self):
        req = MapRequest(geom=_VALID_POLYGON)
        assert req.resolution_m == 250
        assert req.panel_tech == PanelTech.mono

    def test_invalid_geom_type(self):
        with pytest.raises(ValidationError):
            MapRequest(geom={"type": "Point", "coordinates": [30.0, 36.0]})

    def test_resolution_too_low(self):
        with pytest.raises(ValidationError):
            MapRequest(geom=_VALID_POLYGON, resolution_m=50)

    def test_resolution_too_high(self):
        with pytest.raises(ValidationError):
            MapRequest(geom=_VALID_POLYGON, resolution_m=2000)

    def test_resolution_boundary_values(self):
        MapRequest(geom=_VALID_POLYGON, resolution_m=100)
        MapRequest(geom=_VALID_POLYGON, resolution_m=1000)

    def test_country_code_set(self):
        req = MapRequest(geom=_VALID_POLYGON, country_code="DE")
        assert req.country_code == "DE"

    @pytest.mark.parametrize("tech", ["mono", "poly", "bifacial"])
    def test_panel_tech_values(self, tech):
        req = MapRequest(geom=_VALID_POLYGON, panel_tech=tech)
        assert req.panel_tech == tech

    def test_multipolygon_accepted(self):
        mp = {
            "type": "MultiPolygon",
            "coordinates": [[[[30.0, 36.0], [31.0, 36.0], [31.0, 37.0],
                               [30.0, 37.0], [30.0, 36.0]]]],
        }
        req = MapRequest(geom=mp)
        assert req.geom["type"] == "MultiPolygon"


class TestMapJobResponse:
    def test_minimal(self):
        resp = MapJobResponse(id="abc", status="pending")
        assert resp.stats is None
        assert resp.tile_url_template is None

    def test_with_stats(self):
        stats = MapStats(score_min=20.0, score_max=95.0, score_mean=65.0)
        resp = MapJobResponse(id="abc", status="done", stats=stats)
        assert resp.stats.score_mean == 65.0


class TestBoundaryResult:
    def test_valid(self):
        r = BoundaryResult(
            name="Konya",
            geojson={"type": "Polygon", "coordinates": []},
            bounds=[32.0, 37.0, 34.0, 39.0],
            area_km2=38257.0,
        )
        assert r.area_km2 == 38257.0
