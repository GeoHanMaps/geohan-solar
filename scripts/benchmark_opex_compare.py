"""
5-ülke kıyaslama: eski OPEX (EPC×1.5%) vs yeni (country_costs.json dinamik)
100ha · Mono · Fixed · ~düz arazi
"""
import sys, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from app.services.financial import (
    get_country_config, _grid_connection_cost_usd,
    _logistics_cost_tl, _irr_bisection, PROJECT_LIFE_YEARS, get_usd_tl
)

# Benchmark parametreleri
AREA_HA        = 100
MW_PER_HA      = 0.616   # Karapınar kalibre (mono, fixed, düz)
TOTAL_MW       = AREA_HA * MW_PER_HA   # ~61.6 MW
PR             = 0.80    # Performance ratio
ROAD_KM        = 5.0
USD_TL_FIXED   = 38.0    # Sabit kur (karşılaştırma için)

COUNTRIES = [
    {"code": "TR", "name": "TR Konya",      "ghi": 1850, "grid_km": 24.5},
    {"code": "SA", "name": "SA Riyad",      "ghi": 2200, "grid_km": 11.2},
    {"code": "ML", "name": "ML Mali",       "ghi": 2000, "grid_km": 65.5},
    {"code": "NE", "name": "NE Nijer",      "ghi": 2100, "grid_km": 73.3},
    {"code": "IN", "name": "IN Ahmedabad",  "ghi": 1900, "grid_km": 33.8},
    {"code": "AU", "name": "AU Perth",      "ghi": 1900, "grid_km":  6.8},
]

OLD_OPEX_PCT = 0.015  # eski sabit oran

print(f"{'Ülke':<14} {'EPC/MW':>9} {'OPEX Eski':>10} {'OPEX Yeni':>10} "
      f"{'IRR Eski':>9} {'IRR Yeni':>9} {'ΔIRR':>7} {'Uyumlu?':>8}")
print("-" * 85)

all_ok = True
for c in COUNTRIES:
    cfg     = get_country_config(c["code"])
    epc     = cfg["epc_usd_per_mw"]
    ppa     = cfg["ppa_usd_per_kwh"]
    opex_new = cfg.get("opex_usd_per_mw_year", 7_000)
    opex_old = epc * OLD_OPEX_PCT

    annual_gwh = TOTAL_MW * c["ghi"] * PR / 1_000
    annual_rev_usd = annual_gwh * 1_000_000 * ppa

    grid   = _grid_connection_cost_usd(c["grid_km"], TOTAL_MW, cfg)
    invest = TOTAL_MW * epc + grid["total_usd"]

    # Eski IRR
    net_old = annual_rev_usd - TOTAL_MW * opex_old
    irr_old = _irr_bisection(invest, net_old) * 100

    # Yeni IRR
    net_new = annual_rev_usd - TOTAL_MW * opex_new
    irr_new = _irr_bisection(invest, net_new) * 100

    delta   = irr_new - irr_old
    ok      = abs(delta) < 2.0
    if not ok:
        all_ok = False

    print(f"{c['name']:<14} ${epc/1e6:>6.2f}M  "
          f"${opex_old/1000:>6.1f}k/MW  ${opex_new/1000:>6.0f}k/MW  "
          f"{irr_old:>8.1f}%  {irr_new:>8.1f}%  "
          f"{delta:>+6.1f}pp  {'✓' if ok else '✗ AŞILDI'}")

print("-" * 85)
print(f"\nSonuç: {'✓ Tüm IRR kaymaları <2pp' if all_ok else '✗ Bazı ülkelerde >2pp kayma var'}")
