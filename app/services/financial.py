import defusedxml.ElementTree as ET
import requests
from app.config import settings


def get_usd_tl() -> float:
    try:
        r = requests.get(settings.tcmb_url, timeout=10)
        root = ET.fromstring(r.content)
        for c in root.findall("Currency"):
            if c.get("Kod") == "USD":
                return float(c.find("ForexSelling").text.replace(",", "."))
    except Exception:
        pass
    return 38.0


def calculate(total_mw: float, annual_gwh: float) -> dict:
    usd_tl        = get_usd_tl()
    investment_usd = total_mw * settings.investment_per_mw_usd
    investment_tl  = investment_usd * usd_tl
    revenue_tl     = annual_gwh * 1_000_000 * settings.kwh_price_tl
    payback        = investment_tl / revenue_tl if revenue_tl > 0 else 0

    return {
        "usd_tl":           round(usd_tl, 2),
        "investment_usd":   investment_usd,
        "investment_tl":    investment_tl,
        "annual_revenue_tl": round(revenue_tl, 0),
        "payback_years":    round(payback, 1),
    }
