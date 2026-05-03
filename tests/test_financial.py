import pytest
from unittest.mock import patch, MagicMock
from app.services import financial


class TestGetUsdTl:
    def test_returns_float_on_success(self):
        xml_body = b"""<?xml version="1.0"?>
        <Tarih_Date>
          <Currency Kod="USD">
            <ForexSelling>38,50</ForexSelling>
          </Currency>
        </Tarih_Date>"""
        mock_resp = MagicMock()
        mock_resp.content = xml_body
        with patch("app.services.financial.requests.get", return_value=mock_resp):
            rate = financial.get_usd_tl()
        assert rate == pytest.approx(38.50)

    def test_fallback_on_network_error(self):
        with patch("app.services.financial.requests.get", side_effect=Exception("timeout")):
            rate = financial.get_usd_tl()
        assert rate == 38.0

    def test_fallback_on_malformed_xml(self):
        mock_resp = MagicMock()
        mock_resp.content = b"not xml"
        with patch("app.services.financial.requests.get", return_value=mock_resp):
            rate = financial.get_usd_tl()
        assert rate == 38.0


class TestCalculate:
    @pytest.fixture(autouse=True)
    def mock_tcmb(self):
        with patch("app.services.financial.get_usd_tl", return_value=38.0):
            yield

    def test_basic_output_keys(self):
        result = financial.calculate(total_mw=10.0, annual_gwh=18.0)
        assert {"usd_tl", "total_investment_usd", "total_investment_tl",
                "annual_revenue_tl", "payback_years"}.issubset(result.keys())

    def test_investment_scales_with_mw(self):
        r1 = financial.calculate(total_mw=10.0, annual_gwh=18.0)
        r2 = financial.calculate(total_mw=20.0, annual_gwh=36.0)
        assert r2["total_investment_usd"] == pytest.approx(r1["total_investment_usd"] * 2)

    def test_revenue_scales_with_gwh(self):
        r1 = financial.calculate(total_mw=10.0, annual_gwh=18.0)
        r2 = financial.calculate(total_mw=10.0, annual_gwh=36.0)
        assert r2["annual_revenue_tl"] == pytest.approx(r1["annual_revenue_tl"] * 2)

    def test_payback_positive(self):
        result = financial.calculate(total_mw=10.0, annual_gwh=18.0)
        assert result["payback_years"] > 0

    def test_zero_gwh_does_not_crash(self):
        result = financial.calculate(total_mw=10.0, annual_gwh=0.0)
        assert result["payback_years"] >= 999.0

    def test_investment_tl_equals_usd_times_rate(self):
        result = financial.calculate(total_mw=5.0, annual_gwh=10.0)
        assert result["total_investment_tl"] == pytest.approx(
            result["total_investment_usd"] * result["usd_tl"], rel=1e-3
        )
