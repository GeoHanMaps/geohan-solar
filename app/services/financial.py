import math
import defusedxml.ElementTree as ET
import requests
from app.config import settings

# Şebeke bağlantı hat maliyeti (USD/km) — TEİAŞ Türkiye referans verileri
_GRID_COST_PER_KM = {
    "34kv":  60_000,   # OG bağlantı
    "154kv": 220_000,  # YG bağlantı
    "380kv": 480_000,  # ÇYG bağlantı
}

# Voltaj seviyesi kurulu güce (MW) göre — TEİAŞ standardı
# <5MW: 34kV, 5-50MW: 154kV, >50MW: 380kV
def _grid_voltage(total_mw: float) -> str:
    if total_mw < 5:
        return "34kv"
    if total_mw <= 50:
        return "154kv"
    return "380kv"

# İnşaat lojistik parametreleri
_TRUCK_TRIPS_PER_MW    = 12     # TIR sayısı/MW (panel, çelik, beton, kablo)
_TRUCK_FUEL_L_PER_100KM = 32    # TIR yakıt tüketimi L/100km
_DIESEL_TL_PER_L       = 42.0   # Mazot TL/L (2026 tahmini)
_ROAD_BUILD_COST_PER_KM = 800_000  # TL/km — yol yapım/iyileştirme maliyeti


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


def _grid_connection_cost_usd(grid_km: float, total_mw: float) -> dict:
    voltage = _grid_voltage(total_mw)
    cost_per_km = _GRID_COST_PER_KM[voltage]
    # Trafo merkezi maliyeti (MW başına)
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


def _logistics_cost_tl(road_km: float, total_mw: float) -> dict:
    truck_trips = math.ceil(_TRUCK_TRIPS_PER_MW * total_mw)
    # Gidiş-dönüş
    round_trip_km = road_km * 2
    fuel_per_trip_l = round_trip_km * _TRUCK_FUEL_L_PER_100KM / 100
    fuel_total_l = truck_trips * fuel_per_trip_l
    fuel_cost_tl = fuel_total_l * _DIESEL_TL_PER_L

    # Yol iyileştirme gereksinimi (>2km ise)
    road_improvement_tl = 0.0
    if road_km > 2:
        road_improvement_tl = min(road_km, 20) * _ROAD_BUILD_COST_PER_KM

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
) -> dict:
    usd_tl = get_usd_tl()

    # Temel panel+EPC yatırımı
    base_investment_usd = total_mw * settings.investment_per_mw_usd

    # Şebeke bağlantı maliyeti
    grid_cost = _grid_connection_cost_usd(grid_km, total_mw)

    # Lojistik/mazot maliyeti
    logistics = _logistics_cost_tl(road_km, total_mw)

    # Toplam yatırım
    total_investment_usd = base_investment_usd + grid_cost["total_usd"]
    total_investment_tl  = total_investment_usd * usd_tl + logistics["total_tl"]

    # Yıllık gelir
    revenue_tl = annual_gwh * 1_000_000 * settings.kwh_price_tl

    payback = total_investment_tl / revenue_tl if revenue_tl > 0 else 0

    return {
        "usd_tl":                  round(usd_tl, 2),
        "base_investment_usd":     round(base_investment_usd, 0),
        "grid_connection":         grid_cost,
        "logistics":               logistics,
        "total_investment_usd":    round(total_investment_usd, 0),
        "total_investment_tl":     round(total_investment_tl, 0),
        "annual_revenue_tl":       round(revenue_tl, 0),
        "payback_years":           round(payback, 1),
    }
