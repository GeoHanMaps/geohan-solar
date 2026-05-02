import pytest
from unittest.mock import patch, MagicMock
from app.services import solar, cache


# ─── Mock response fixtures ────────────────────────────────────────────────────

NASA_RESP = {
    "properties": {"parameter": {"ALLSKY_SFC_SW_DWN": {"ANN": 5.5}}}
}

PVGIS_RESP = {
    "outputs": {"monthly": {"fixed": [
        {"H(h)_m": 2100.0}, {"H(h)_m": 2400.0}, {"H(h)_m": 3500.0},
        {"H(h)_m": 4200.0}, {"H(h)_m": 5100.0}, {"H(h)_m": 5800.0},
        {"H(h)_m": 5900.0}, {"H(h)_m": 5600.0}, {"H(h)_m": 4500.0},
        {"H(h)_m": 3200.0}, {"H(h)_m": 2300.0}, {"H(h)_m": 1900.0},
    ]}}
}

OPEN_METEO_RESP = {
    "daily": {"shortwave_radiation_sum": [18.0] * 365}   # 18 MJ/m²/gün × 365
}

NSRDB_RESP = {
    "outputs": {"avg_ghi": {"annual": 5.0}}   # kWh/m²/gün → × 365
}


def _ok(body):
    m = MagicMock()
    m.json.return_value = body
    m.raise_for_status = MagicMock()
    return m

def _fail():
    m = MagicMock()
    m.raise_for_status.side_effect = Exception("HTTP error")
    return m


@pytest.fixture(autouse=True)
def no_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "_CACHE_DIR", tmp_path)


# ─── Bölge tespiti ────────────────────────────────────────────────────────────

class TestRegionDetection:
    @pytest.mark.parametrize("lat,lon,expected", [
        (37.87, 32.49, "europe_africa_me"),    # Türkiye
        (51.5,  -0.1,  "europe_africa_me"),    # Londra
        (-1.0,  36.8,  "europe_africa_me"),    # Nairobi
        (25.0,  55.0,  "europe_africa_me"),    # Dubai
        (40.7,  -74.0, "americas"),            # New York
        (-23.5, -46.6, "americas"),            # São Paulo
        (35.7,  139.7, "asia_pacific"),        # Tokyo
        (-33.9, 151.2, "asia_pacific"),        # Sydney
        (28.6,  77.2,  "asia_pacific"),        # Delhi
    ])
    def test_region(self, lat, lon, expected):
        assert solar._region(lat, lon) == expected


# ─── Tek kaynak fonksiyonları ─────────────────────────────────────────────────

class TestNasaPower:
    def test_annual_calculation(self):
        with patch("app.services.solar.requests.get", return_value=_ok(NASA_RESP)):
            ghi = solar._from_nasa_power(37.87, 32.49)
        assert ghi == pytest.approx(5.5 * 365)

class TestPvgis:
    def test_annual_sum(self):
        with patch("app.services.solar.requests.get", return_value=_ok(PVGIS_RESP)):
            ghi = solar._from_pvgis(37.87, 32.49)
        expected = sum(
            v * d / 1000
            for v, d in zip(
                [m["H(h)_m"] for m in PVGIS_RESP["outputs"]["monthly"]["fixed"]],
                [31,28,31,30,31,30,31,31,30,31,30,31]
            )
        )
        assert ghi == pytest.approx(expected)

class TestOpenMeteo:
    def test_mj_to_kwh_conversion(self):
        with patch("app.services.solar.requests.get", return_value=_ok(OPEN_METEO_RESP)):
            ghi = solar._from_open_meteo(37.87, 32.49)
        assert ghi == pytest.approx(18.0 * 365 / 3.6, rel=1e-3)

    def test_none_values_skipped(self):
        resp = {"daily": {"shortwave_radiation_sum": [18.0, None, 18.0] + [18.0] * 362}}
        with patch("app.services.solar.requests.get", return_value=_ok(resp)):
            ghi = solar._from_open_meteo(0.0, 0.0)
        assert ghi > 0

class TestNsrdb:
    def test_daily_to_annual(self, monkeypatch):
        monkeypatch.setattr("app.services.solar.settings.nsrdb_key", "test_key")
        with patch("app.services.solar.requests.get", return_value=_ok(NSRDB_RESP)):
            ghi = solar._from_nsrdb(40.7, -74.0)
        assert ghi == pytest.approx(5.0 * 365)

    def test_raises_without_key(self, monkeypatch):
        monkeypatch.setattr("app.services.solar.settings.nsrdb_key", "")
        with pytest.raises(RuntimeError, match="NSRDB key"):
            solar._from_nsrdb(40.7, -74.0)

class TestCams:
    def test_raises_without_key(self, monkeypatch):
        monkeypatch.setattr("app.services.solar.settings.cams_key", "")
        with pytest.raises(RuntimeError, match="CAMS key"):
            solar._from_cams(37.87, 32.49)


# ─── Routing ve fallback ──────────────────────────────────────────────────────

class TestRouting:
    def _mock_get(self, success_url_fragment, fail_others=True):
        """Sadece belirli URL'ye başarılı yanıt ver."""
        def side_effect(url, **kwargs):
            # Open-Meteo
            if "open-meteo" in url:
                if success_url_fragment == "open-meteo":
                    return _ok(OPEN_METEO_RESP)
                return _fail()
            # PVGIS
            if "pvgis" in url or "jrc.ec" in url:
                if success_url_fragment == "pvgis":
                    return _ok(PVGIS_RESP)
                return _fail()
            # NASA
            if "nasa" in url or "power.larc" in url:
                if success_url_fragment == "nasa":
                    return _ok(NASA_RESP)
                return _fail()
            # NSRDB
            if "nrel.gov" in url:
                if success_url_fragment == "nsrdb":
                    return _ok(NSRDB_RESP)
                return _fail()
            return _fail()
        return side_effect

    def test_europe_falls_to_pvgis_when_cams_missing(self, monkeypatch):
        monkeypatch.setattr("app.services.solar.settings.cams_key", "")
        with patch("app.services.solar.requests.get",
                   side_effect=self._mock_get("pvgis")):
            ghi = solar.get_annual_ghi(37.87, 32.49)
        assert ghi > 0

    def test_americas_uses_nsrdb(self, monkeypatch):
        monkeypatch.setattr("app.services.solar.settings.nsrdb_key", "test_key")
        with patch("app.services.solar.requests.get",
                   side_effect=self._mock_get("nsrdb")):
            ghi = solar.get_annual_ghi(40.7, -74.0)
        assert ghi > 0

    def test_falls_back_to_open_meteo(self, monkeypatch):
        monkeypatch.setattr("app.services.solar.settings.cams_key", "")
        monkeypatch.setattr("app.services.solar.settings.nsrdb_key", "")
        with patch("app.services.solar.requests.get",
                   side_effect=self._mock_get("open-meteo")):
            ghi = solar.get_annual_ghi(37.87, 32.49)
        assert ghi > 0

    def test_raises_when_all_fail(self, monkeypatch):
        monkeypatch.setattr("app.services.solar.settings.cams_key", "")
        monkeypatch.setattr("app.services.solar.settings.nsrdb_key", "")
        with patch("app.services.solar.requests.get", return_value=_fail()):
            with pytest.raises(RuntimeError):
                solar.get_annual_ghi(37.87, 32.49)


# ─── Cache ────────────────────────────────────────────────────────────────────

class TestCaching:
    def test_second_call_uses_cache(self, monkeypatch):
        monkeypatch.setattr("app.services.solar.settings.cams_key", "")
        monkeypatch.setattr("app.services.solar.settings.nsrdb_key", "")
        with patch("app.services.solar.requests.get",
                   return_value=_ok(PVGIS_RESP)) as mock_get:
            solar.get_annual_ghi(37.87, 32.49)
            solar.get_annual_ghi(37.87, 32.49)
        assert mock_get.call_count == 1

    def test_different_coords_both_fetched(self, monkeypatch):
        monkeypatch.setattr("app.services.solar.settings.cams_key", "")
        with patch("app.services.solar.requests.get",
                   return_value=_ok(PVGIS_RESP)) as mock_get:
            solar.get_annual_ghi(37.87, 32.49)
            solar.get_annual_ghi(25.00, 45.00)
        assert mock_get.call_count == 2


# ─── source_info ──────────────────────────────────────────────────────────────

class TestSourceInfo:
    def test_turkey_pipeline(self):
        info = solar.source_info(37.87, 32.49)
        assert info["region"] == "europe_africa_me"
        assert "pvgis" in info["pipeline"]
        assert "nasa_power" in info["pipeline"]

    def test_us_pipeline(self):
        info = solar.source_info(40.7, -74.0)
        assert info["region"] == "americas"
        assert "nsrdb" in info["pipeline"]
