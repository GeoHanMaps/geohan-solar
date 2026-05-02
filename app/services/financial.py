import json
import math
import defusedxml.ElementTree as ET
import requests
from pathlib import Path
from app.config import settings

_COUNTRY_COSTS_FILE = Path(__file__).parent.parent.parent / "config" / "country_costs.json"
_country_data: dict = {}

PROJECT_LIFE_YEARS = 25
_OPEX_PCT_OF_EPC = 0.015   # 1.5 % of EPC/MW/year (solar O&M benchmark)


def _load_country_data() -> dict:
    global _country_data
    if not _country_data:
        _country_data = json.loads(_COUNTRY_COSTS_FILE.read_text(encoding="utf-8"))
    return _country_data


def get_country_config(country_code: str) -> dict:
    data = _load_country_data()
    cc = country_code.upper() if country_code else "DEFAULT"
    if cc in data and not cc.startswith("_"):
        return data[cc]
    return data["DEFAULT"].copy()


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


def _grid_voltage(total_mw: float, cfg: dict) -> str:
    if total_mw < cfg.get("mv_threshold_mw", 5):
        return "34kv"
    if total_mw <= cfg.get("hv_threshold_mw", 50):
        return "154kv"
    return "380kv"


def _grid_connection_cost_usd(grid_km: float, total_mw: float, cfg: dict) -> dict:
    voltage = _grid_voltage(total_mw, cfg)
    cost_per_km = cfg["grid_cost_per_km"].get(voltage, 200000)
    substation_usd = total_mw * 25_000
    line_usd = grid_km * cost_per_km
    return {
        "voltage_level": voltage,
        "line_km": round(grid_km, 1),
        "line_cost_usd": round(line_usd, 0),
        "substation_cost_usd": round(substation_usd, 0),
        "total_usd": round(line_usd + substation_usd, 0),
    }


def _logistics_cost_tl(
    road_km: float,
    total_mw: float,
    cfg: dict,
    usd_tl: float,
) -> dict:
    logistics_factor = cfg.get("logistics_factor", 1.0)
    truck_trips = math.ceil(12 * logistics_factor * total_mw)

    fuel_l_per_100km = 32.0
    diesel_tl_per_l = 42.0
    round_trip_km = road_km * 2
    fuel_per_trip_l = round_trip_km * fuel_l_per_100km / 100
    fuel_total_l = truck_trips * fuel_per_trip_l
    fuel_cost_tl = fuel_total_l * diesel_tl_per_l

    road_improvement_tl = 0.0
    if road_km > 2:
        road_improvement_tl = min(road_km, 20) * 800_000 * logistics_factor

    total_tl = fuel_cost_tl + road_improvement_tl
    return {
        "truck_trips": truck_trips,
        "road_km": round(road_km, 1),
        "fuel_liters": round(fuel_total_l, 0),
        "fuel_cost_tl": round(fuel_cost_tl, 0),
        "road_improvement_tl": round(road_improvement_tl, 0),
        "total_tl": round(total_tl, 0),
    }


def _irr_bisection(
    investment_usd: float,
    net_annual_cashflow_usd: float,
    n: int = PROJECT_LIFE_YEARS,
) -> float:
    """IRR via bisection on NPV = 0. Returns decimal (0.08 = 8%)."""
    if net_annual_cashflow_usd <= 0:
        return -1.0

    def pv_annuity(r: float) -> float:
        if abs(r) < 1e-9:
            return float(n)
        return (1 - (1 + r) ** -n) / r

    def npv(r: float) -> float:
        return -investment_usd + net_annual_cashflow_usd * pv_annuity(r)

    lo, hi = -0.99, 1.00
    # Narrow search range
    if npv(0.0) >= 0:
        lo = 0.0
    else:
        hi = 0.0

    for _ in range(60):
        if abs(hi - lo) < 1e-7:
            break
        mid = (lo + hi) / 2
        if npv(mid) >= 0:
            lo = mid
        else:
            hi = mid

    return (lo + hi) / 2


def calculate(
    total_mw: float,
    annual_gwh: float,
    grid_km: float = 0.0,
    road_km: float = 0.0,
    country_code: str = "DEFAULT",
) -> dict:
    cfg = get_country_config(country_code)
    usd_tl = get_usd_tl()

    epc_per_mw = cfg.get("epc_usd_per_mw", settings.investment_per_mw_usd)
    base_investment_usd = total_mw * epc_per_mw

    grid_cost = _grid_connection_cost_usd(grid_km, total_mw, cfg)
    logistics = _logistics_cost_tl(road_km, total_mw, cfg, usd_tl)

    financing_rate = cfg.get("financing_rate", 0.10)

    total_investment_usd = base_investment_usd + grid_cost["total_usd"]
    total_investment_tl = total_investment_usd * usd_tl + logistics["total_tl"]

    # Annual revenue in both currencies
    ppa_usd = cfg.get("ppa_usd_per_kwh", 0.045)
    annual_revenue_usd = annual_gwh * 1_000_000 * ppa_usd
    annual_revenue_tl = annual_revenue_usd * usd_tl

    # Annual O&M cost
    opex_usd_per_mw_yr = cfg.get("opex_usd_per_mw_year", epc_per_mw * _OPEX_PCT_OF_EPC)
    annual_opex_usd = total_mw * opex_usd_per_mw_yr

    # Payback in USD terms (independent of exchange rate)
    net_annual_usd = annual_revenue_usd - annual_opex_usd
    payback = total_investment_usd / net_annual_usd if net_annual_usd > 0 else 999.0

    # IRR over 25yr project life (decimal)
    irr_raw = _irr_bisection(total_investment_usd, net_annual_usd)
    irr_pct = round(irr_raw * 100, 1)   # stored as %, e.g. 8.3 for 8.3%

    return {
        "country_code":          country_code.upper(),
        "country_name":          cfg.get("name", country_code),
        "usd_tl":                round(usd_tl, 2),
        "epc_per_mw_usd":        epc_per_mw,
        "base_investment_usd":   round(base_investment_usd, 0),
        "grid_connection":       grid_cost,
        "logistics":             logistics,
        "total_investment_usd":  round(total_investment_usd, 0),
        "total_investment_tl":   round(total_investment_tl, 0),
        "annual_revenue_tl":     round(annual_revenue_tl, 0),
        "financing_rate":        financing_rate,
        "payback_years":         round(payback, 1),
        "irr_estimate":          irr_pct,
        "grid_reliability":      cfg.get("grid_reliability", 0.75),
    }
