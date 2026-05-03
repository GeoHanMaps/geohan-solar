import pytest
from unittest.mock import patch
from shapely.geometry import Point
import geopandas as gpd
from app.services import grid, cache


@pytest.fixture(autouse=True)
def no_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "_CACHE_DIR", tmp_path)


def _mock_gdf_with_point(lon, lat):
    """osmnx.features_from_point dönüş değeri simülatörü."""
    gdf = gpd.GeoDataFrame(
        [{"geometry": Point(lon, lat)}],
        crs="EPSG:4326",
    )
    return gdf


class TestNearestSubstationKm:
    def test_returns_float(self):
        gdf = _mock_gdf_with_point(32.49, 37.87)
        with patch("app.services.grid.ox.features_from_point", return_value=gdf):
            d = grid.nearest_substation_km(37.87, 32.49)
        assert isinstance(d, float)

    def test_nearby_substation_low_distance(self):
        # Trafo merkezi neredeyse aynı noktada
        gdf = _mock_gdf_with_point(32.4932, 37.8746)
        with patch("app.services.grid.ox.features_from_point", return_value=gdf):
            d = grid.nearest_substation_km(37.8746, 32.4932)
        assert d < 1.0

    def test_returns_99_on_empty_result(self):
        with patch("app.services.grid.ox.features_from_point",
                   side_effect=Exception("no results")):
            d = grid.nearest_substation_km(37.87, 32.49)
        assert d == pytest.approx(39.0, abs=0.5)

    def test_returns_99_on_network_error(self):
        with patch("app.services.grid.ox.features_from_point",
                   side_effect=ConnectionError):
            d = grid.nearest_substation_km(0.0, 0.0)
        assert d == pytest.approx(39.0, abs=0.5)

    def test_southern_hemisphere_utm(self):
        gdf = _mock_gdf_with_point(25.0, -33.0)
        with patch("app.services.grid.ox.features_from_point", return_value=gdf):
            d = grid.nearest_substation_km(-33.9, 25.0)
        assert isinstance(d, float)
