"""
Admin sınır servisi testleri — osmnx.geocode_to_gdf mock'lanır.
"""

import geopandas as gpd
from shapely.geometry import box
from unittest.mock import patch


def _make_gdf(name="Konya", geom=None):
    if geom is None:
        geom = box(32.0, 37.0, 34.0, 39.0)
    return gpd.GeoDataFrame(
        [{"display_name": name, "geometry": geom}],
        geometry="geometry",
        crs="EPSG:4326",
    )


class TestSearch:
    def test_returns_results(self):
        with patch("app.services.boundaries.ox.geocode_to_gdf", return_value=_make_gdf()):
            from app.services.boundaries import search
            results = search("Konya")
        assert len(results) == 1
        assert results[0]["name"] == "Konya"

    def test_result_has_required_keys(self):
        with patch("app.services.boundaries.ox.geocode_to_gdf", return_value=_make_gdf()):
            from app.services.boundaries import search
            r = search("Konya")[0]
        assert "name" in r
        assert "geojson" in r
        assert "bounds" in r
        assert "area_km2" in r

    def test_bounds_length_four(self):
        with patch("app.services.boundaries.ox.geocode_to_gdf", return_value=_make_gdf()):
            from app.services.boundaries import search
            r = search("Konya")[0]
        assert len(r["bounds"]) == 4

    def test_area_positive(self):
        with patch("app.services.boundaries.ox.geocode_to_gdf", return_value=_make_gdf()):
            from app.services.boundaries import search
            r = search("Konya")[0]
        assert r["area_km2"] > 0

    def test_empty_query_returns_empty(self):
        from app.services.boundaries import search
        assert search("") == []
        assert search("   ") == []

    def test_osmnx_exception_returns_empty(self):
        with patch("app.services.boundaries.ox.geocode_to_gdf",
                   side_effect=Exception("network error")):
            from app.services.boundaries import search
            assert search("nowhere") == []

    def test_empty_gdf_returns_empty(self):
        empty_gdf = gpd.GeoDataFrame(columns=["display_name", "geometry"],
                                      geometry="geometry", crs="EPSG:4326")
        with patch("app.services.boundaries.ox.geocode_to_gdf", return_value=empty_gdf):
            from app.services.boundaries import search
            assert search("empty") == []

    def test_none_gdf_returns_empty(self):
        with patch("app.services.boundaries.ox.geocode_to_gdf", return_value=None):
            from app.services.boundaries import search
            assert search("test") == []

    def test_max_five_results(self):
        rows = [{"display_name": f"Place{i}", "geometry": box(i, i, i+1, i+1)}
                for i in range(8)]
        big_gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
        with patch("app.services.boundaries.ox.geocode_to_gdf", return_value=big_gdf):
            from app.services.boundaries import search
            results = search("many")
        assert len(results) <= 5

    def test_cache_hit_skips_osmnx(self, tmp_path, monkeypatch):
        import app.services.cache as cache_mod
        monkeypatch.setattr(cache_mod, "_CACHE_DIR", tmp_path)

        cached = [{"name": "cached", "geojson": {}, "bounds": [0,0,1,1], "area_km2": 1.0}]
        with patch("app.services.boundaries.cache_get", return_value=cached):
            with patch("app.services.boundaries.ox.geocode_to_gdf") as mock_ox:
                from app.services.boundaries import search
                result = search("konya")
        mock_ox.assert_not_called()
        assert result == cached
