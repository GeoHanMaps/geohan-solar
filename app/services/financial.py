import json
import math
import defusedxml.ElementTree as ET
import requests
from pathlib import Path
from app.config import settings

_COUNTRY_COSTS_FILE = Path(__file__).parent.parent.parent / "config" / "country_costs.json"
_country_data: dict = {}


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

    # Bölge fallback
    default = data["DEFAULT"].copy()
    regions = data.get("_regions", {})
    for region_cfg in regions.values():
        # Ülke bulunamadıysa DEFAULT döner
        pass

    return default


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
    total_usd = line_usd + substation_usd
    return {
        "voltage_level": voltage,
        "line_km": round(grid_km, 1),
        "line_cost_usd": round(line_usd, 0),
        "substation_cost_usd": round(substation_usd, 0),
        "total_usd": round(total_usd, 0),
    }


def _logistics_cost_tl(
    road_km: float,
    total_mw: float,
    cfg: dict,
    usd_tl: float,
) -> dict:
    logistics_factor = cfg.get("logistics_factor", 1.0)
    truck_trips_per_mw = 12 * logistics_factor
    truck_trips = math.ceil(truck_trips_per_mw * total_mw)

    fuel_l_per_100km = 32.0
    diesel_tl_per_l = 42.0
    round_trip_km = road_km * 2
    fuel_per_trip_l = round_trip_km * fuel_l_per_100km / 100
    fuel_total_l = truck_trips * fuel_per_trip_l
    fuel_cost_tl = fuel_total_l * diesel_tl_per_l

    # Yol iyileştirme (2km'den uzaksa)
    road_improvement_tl = 0.0
    if road_km > 2:
        base_road_cost = 800_000  # TL/km
        road_improvement_tl = min(road_km, 20) * base_road_cost * logistics_factor

    total_tl = fuel_cost_tl + road_improvement_tl
    return {
        "truck_trips": truck_trips,
        "road_km": round(road_km, 1),
        "fuel_liters": round(fuel_total_l, 0),
        "fuel_cost_tl": round(fuel_cost_tl, 0),
        "road_improvement_tl": round(road_improvement_tl, 0),
        "total_tl": round(total_tl, 0),
    }


def calculate(
    total_mw: float,
    annual_gwh: float,
    grid_km: float = 0.0,
    road_km: float = 0.0,
    country_code: str = "DEFAULT",
) -> dict:
    cfg = get_country_config(country_code)
    usd_tl = get_usd_tl()

    # Ülkeye özgü EPC maliyeti
    epc_per_mw = cfg.get("epc_usd_per_mw", settings.investment_per_mw_usd)
    base_investment_usd = total_mw * epc_per_mw

    # Arazi maliyeti
    land_cost_usd = 0.0  # alan_ha * cfg.get("land_cost_usd_per_ha", 1500) — ileride eklenecek

    # Şebeke bağlantı maliyeti
    grid_cost = _grid_connection_cost_usd(grid_km, total_mw, cfg)

    # Lojistik/mazot maliyeti
    logistics = _logistics_cost_tl(road_km, total_mw, cfg, usd_tl)

    # Finansman maliyeti faktörü (gelişmiş ülkeler daha ucuz kredi)
    financing_rate = cfg.get("financing_rate", 0.10)

    # Toplam yatırım
    total_investment_usd = base_investment_usd + grid_cost["total_usd"] + land_cost_usd
    total_investment_tl = total_investment_usd * usd_tl + logistics["total_tl"]

    # Yıllık gelir — ülkeye özgü PPA fiyatı varsa onu kullan
    ppa_usd = cfg.get("ppa_usd_per_kwh")
    if ppa_usd:
        revenue_tl = annual_gwh * 1_000_000 * ppa_usd * usd_tl
    else:
        revenue_tl = annual_gwh * 1_000_000 * settings.kwh_price_tl

    payback = total_investment_tl / revenue_tl if revenue_tl > 0 else 0

    # IRR tahmini (basit)
    irr_estimate = (1 / payback) - financing_rate if payback > 0 else 0

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
        "annual_revenue_tl":     round(revenue_tl, 0),
        "financing_rate":        financing_rate,
        "payback_years":         round(payback, 1),
        "irr_estimate":          round(irr_estimate * 100, 1),
        "grid_reliability":      cfg.get("grid_reliability", 0.75),
    }
