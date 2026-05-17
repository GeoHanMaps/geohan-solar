"""
WDPA korunan alan + askeri bölge yasal kontrolü testleri.
OSM / Protected Planet ağ çağrısı yok — osmnx mock'lanır.

Referans nokta: lat=37.0, lon=32.0 (Türkiye, Tuz Gölü civarı)
"""

import pytest
from unittest.mock import patch

import geopandas as gpd
from shapely.geometry import Polygon

from app.services import legal, cache

# ─── Geometrik sabitler ──────────────────────────────────────────────────────────

LAT, LON = 37.0, 32.0

# Sorgu noktasını içeren küçük kutu (~2.2 km × 1.8 km, WGS84)
CONTAINING_BOX = Polygon([
    (LON - 0.01, LAT - 0.01),
    (LON + 0.01, LAT - 0.01),
    (LON + 0.01, LAT + 0.01),
    (LON - 0.01, LAT + 0.01),
])

# Sorgu noktasının ~550 m kuzeyinde, 1 km buffer içinde
BUFFER_BOX = Polygon([
    (LON,        LAT + 0.005),
    (LON + 0.01, LAT + 0.005),
    (LON + 0.01, LAT + 0.015),
    (LON,        LAT + 0.015),
])

# Sorgu noktasının ~5.5 km kuzeyinde, buffer dışında
FAR_BOX = Polygon([
    (LON,        LAT + 0.05),
    (LON + 0.01, LAT + 0.05),
    (LON + 0.01, LAT + 0.06),
    (LON,        LAT + 0.06),
])


def _make_gdf(rows: list[dict]) -> gpd.GeoDataFrame:
    """Test için saf WGS84 GeoDataFrame üretir."""
    if not rows:
        return gpd.GeoDataFrame([], columns=["geometry"], geometry="geometry",
                                crs="EPSG:4326")
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")


@pytest.fixture(autouse=True)
def _tmp_cache(monkeypatch, tmp_path):
    """Her test için izole cache dizini kullan."""
    monkeypatch.setattr(cache, "_CACHE_DIR", tmp_path)


# ─── _norm_iucn ──────────────────────────────────────────────────────────────────

class TestNormIucn:
    @pytest.mark.parametrize("val,expected", [
        ("2",  "II"),  ("1",  "Ia"), ("1a", "Ia"), ("1b", "Ib"),
        ("3",  "III"), ("4",  "IV"), ("5",  "V"),  ("6",  "VI"),
        ("II", "II"),  ("V",  "V"),  ("Ia", "Ia"),
        (None,         "Not Reported"),
        (float("nan"), "Not Reported"),
        ("99",         "Not Reported"),
        ("unknown",    "Not Reported"),
    ])
    def test_norm_iucn(self, val, expected):
        assert legal._norm_iucn(val) == expected


# ─── _classify_osm — WDPA ────────────────────────────────────────────────────────

class TestClassifyOsmWdpa:
    def _geo(self, geom, protect_class=None, name=None):
        return _make_gdf([{
            "geometry":     geom,
            "boundary":     "protected_area",
            "military":     float("nan"),
            "name":         name or float("nan"),
            "protect_class": protect_class or float("nan"),
            "iucn_level":   float("nan"),
        }])

    def test_inside_hard_iucn_is_hard_block(self):
        gdf = self._geo(CONTAINING_BOX, protect_class="2", name="Test NP")
        with patch("app.services.legal.ox.features_from_point", return_value=gdf):
            result = legal.geo_constraints(LAT, LON)
        assert result["wdpa"] is not None
        assert result["wdpa"]["severity"] == "hard"
        assert result["wdpa"]["iucn"] == "II"
        assert result["wdpa"]["name"] == "Test NP"
        assert result["wdpa_checked"] is True

    def test_inside_soft_iucn_is_soft(self):
        gdf = self._geo(CONTAINING_BOX, protect_class="5")
        with patch("app.services.legal.ox.features_from_point", return_value=gdf):
            result = legal.geo_constraints(LAT, LON)
        assert result["wdpa"]["severity"] == "soft"
        assert result["wdpa"]["iucn"] == "V"

    def test_inside_not_reported_iucn_is_soft(self):
        gdf = self._geo(CONTAINING_BOX, protect_class=None)
        with patch("app.services.legal.ox.features_from_point", return_value=gdf):
            result = legal.geo_constraints(LAT, LON)
        assert result["wdpa"]["severity"] == "soft"
        assert result["wdpa"]["iucn"] == "Not Reported"

    def test_buffer_hard_iucn_is_soft(self):
        """Poligon dışında ama buffer içinde → her zaman soft (proje kenarı riski)."""
        gdf = self._geo(BUFFER_BOX, protect_class="2")
        with patch("app.services.legal.ox.features_from_point", return_value=gdf):
            result = legal.geo_constraints(LAT, LON)
        assert result["wdpa"] is not None
        assert result["wdpa"]["severity"] == "soft"   # buffer → soft, IUCN II bile

    def test_far_polygon_no_constraint(self):
        """Buffer dışındaki poligon → kısıt yok."""
        gdf = self._geo(FAR_BOX, protect_class="2")
        with patch("app.services.legal.ox.features_from_point", return_value=gdf):
            result = legal.geo_constraints(LAT, LON)
        assert result["wdpa"] is None

    def test_empty_gdf_clean_site(self):
        gdf = _make_gdf([])
        with patch("app.services.legal.ox.features_from_point", return_value=gdf):
            result = legal.geo_constraints(LAT, LON)
        assert result["wdpa"] is None
        assert result["military"] is None
        assert result["wdpa_checked"] is True


# ─── _classify_osm — Askeri ──────────────────────────────────────────────────────

class TestClassifyOsmMilitary:
    def _mil_gdf(self, geom, mil_type, name=None):
        return _make_gdf([{
            "geometry":     geom,
            "boundary":     float("nan"),
            "military":     mil_type,
            "name":         name or float("nan"),
            "protect_class": float("nan"),
            "iucn_level":   float("nan"),
        }])

    @pytest.mark.parametrize("mil_type", ["base", "range", "airfield", "danger_area"])
    def test_military_hard_types_inside(self, mil_type):
        gdf = self._mil_gdf(CONTAINING_BOX, mil_type)
        with patch("app.services.legal.ox.features_from_point", return_value=gdf):
            result = legal.geo_constraints(LAT, LON)
        assert result["military"]["severity"] == "hard"
        assert result["military"]["military_type"] == mil_type
        assert result["military_checked"] is True

    @pytest.mark.parametrize("mil_type", ["barracks", "checkpoint", "trench"])
    def test_military_soft_types(self, mil_type):
        gdf = self._mil_gdf(CONTAINING_BOX, mil_type)
        with patch("app.services.legal.ox.features_from_point", return_value=gdf):
            result = legal.geo_constraints(LAT, LON)
        assert result["military"]["severity"] == "soft"

    def test_unknown_military_tag_ignored(self):
        """Bilinmeyen military tag (ör. 'museum') → kısıt yok."""
        gdf = self._mil_gdf(CONTAINING_BOX, "museum")
        with patch("app.services.legal.ox.features_from_point", return_value=gdf):
            result = legal.geo_constraints(LAT, LON)
        assert result["military"] is None

    def test_military_far_no_constraint(self):
        gdf = self._mil_gdf(FAR_BOX, "base")
        with patch("app.services.legal.ox.features_from_point", return_value=gdf):
            result = legal.geo_constraints(LAT, LON)
        assert result["military"] is None


# ─── check() entegrasyonu ────────────────────────────────────────────────────────

class TestCheckWithGeoResult:
    def _geo(self, wdpa=None, military=None, wdpa_checked=True, military_checked=True):
        return {
            "wdpa": wdpa, "military": military,
            "wdpa_checked": wdpa_checked, "military_checked": military_checked,
            "constraints": [],
        }

    def test_military_hard_block(self):
        geo = self._geo(military={"severity": "hard", "military_type": "base"})
        result = legal.check(LAT, LON, lc_code=60, slope_pct=5.0,
                             country_code="DEFAULT", geo_result=geo)
        assert result["hard_block"] is True
        assert result["score"] == 0
        assert "askeri" in result["reason"].lower()

    def test_wdpa_hard_block(self):
        geo = self._geo(wdpa={"severity": "hard", "name": "Test NP", "iucn": "II"})
        result = legal.check(LAT, LON, lc_code=60, slope_pct=5.0,
                             country_code="DEFAULT", geo_result=geo)
        assert result["hard_block"] is True
        assert result["score"] == 0
        assert "WDPA" in result["reason"]

    def test_wdpa_soft_block(self):
        geo = self._geo(wdpa={"severity": "soft", "name": "Test PA", "iucn": "V"})
        result = legal.check(LAT, LON, lc_code=60, slope_pct=5.0,
                             country_code="DEFAULT", geo_result=geo)
        assert result["hard_block"] is False
        assert result["score"] == 40
        assert result["wdpa_iucn"] == "V"

    def test_military_soft_block(self):
        geo = self._geo(military={"severity": "soft", "military_type": "barracks"})
        result = legal.check(LAT, LON, lc_code=60, slope_pct=5.0,
                             country_code="DEFAULT", geo_result=geo)
        assert result["hard_block"] is False
        assert result["score"] == 40

    def test_military_hard_beats_wdpa_soft(self):
        geo = self._geo(
            wdpa={"severity": "soft", "name": "PA", "iucn": "V"},
            military={"severity": "hard", "military_type": "range"},
        )
        result = legal.check(LAT, LON, lc_code=60, slope_pct=5.0,
                             country_code="DEFAULT", geo_result=geo)
        assert result["hard_block"] is True
        assert "askeri" in result["reason"].lower()

    def test_clean_site_score_100(self):
        geo = self._geo()
        result = legal.check(LAT, LON, lc_code=60, slope_pct=5.0,
                             country_code="DEFAULT", geo_result=geo)
        assert result["score"] == 100
        assert result["wdpa_checked"] is True
        assert result["hard_block"] is False

    def test_graceful_degradation_no_penalty(self):
        """WDPA erişilemedi → score cezası yok, reason'da uyarı var."""
        geo = self._geo(wdpa_checked=False, military_checked=False)
        result = legal.check(LAT, LON, lc_code=60, slope_pct=5.0,
                             country_code="DEFAULT", geo_result=geo)
        assert result["score"] == 100
        assert result["wdpa_checked"] is False
        assert "doğrulanamadı" in result["reason"]

    def test_lc_hard_block_skips_wdpa(self):
        """LC fast-path → geo_constraints hiç çağrılmaz."""
        with patch("app.services.legal.geo_constraints") as mock_geo:
            result = legal.check(LAT, LON, lc_code=10, slope_pct=5.0, country_code="TR")
        assert result["hard_block"] is True
        mock_geo.assert_not_called()

    def test_slope_hard_block_skips_wdpa(self):
        with patch("app.services.legal.geo_constraints") as mock_geo:
            result = legal.check(LAT, LON, lc_code=60, slope_pct=20.0, country_code="TR")
        assert result["hard_block"] is True
        mock_geo.assert_not_called()

    def test_geo_result_param_skips_network(self):
        """geo_result verilince geo_constraints çağrılmaz."""
        geo = self._geo()
        with patch("app.services.legal.geo_constraints") as mock_geo:
            result = legal.check(LAT, LON, lc_code=60, slope_pct=5.0, geo_result=geo)
        mock_geo.assert_not_called()
        assert result["score"] == 100


# ─── Graceful degradation (OSM fail) ─────────────────────────────────────────────

class TestGracefulDegradation:
    def test_osm_timeout_returns_unchecked(self):
        with patch("app.services.legal.ox.features_from_point",
                   side_effect=Exception("Overpass timeout")):
            result = legal.geo_constraints(LAT, LON)
        assert result["wdpa_checked"] is False
        assert result["military_checked"] is False
        assert result["wdpa"] is None

    def test_osm_fail_no_token_graceful(self, monkeypatch):
        monkeypatch.setattr("app.services.legal.settings.protected_planet_token", "")
        with patch("app.services.legal.ox.features_from_point",
                   side_effect=Exception("network error")):
            result = legal.geo_constraints(0.0, 0.0)
        assert result["wdpa_checked"] is False


# ─── Cache ────────────────────────────────────────────────────────────────────────

class TestWdpaCache:
    def test_cache_hit_skips_osm(self):
        geo = {
            "wdpa": None, "military": None,
            "wdpa_checked": True, "military_checked": True,
            "constraints": [],
        }
        cache.set("wdpa", geo, ttl_days=30,
                  lat=round(LAT, 3), lon=round(LON, 3),
                  r=30_000)
        with patch("app.services.legal.ox.features_from_point") as mock_osm:
            result = legal.geo_constraints(LAT, LON)
        mock_osm.assert_not_called()
        assert result["wdpa_checked"] is True

    def test_different_coords_both_queried(self):
        gdf = _make_gdf([])
        with patch("app.services.legal.ox.features_from_point",
                   return_value=gdf) as mock_osm:
            legal.geo_constraints(LAT, LON)
            legal.geo_constraints(LAT + 0.1, LON + 0.1)
        assert mock_osm.call_count == 2
