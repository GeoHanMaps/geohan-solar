"""
GeoHan — Çoklu lokasyon taraması.
app.services kullanır; mantık yinelenmez.

Kullanım:
    conda activate geohan
    python scripts/find_best_location.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.gee_init import initialize_ee
from app.services import terrain, solar, grid, access, capacity, mcda, legal, financial
from app.schemas import PanelTech, TrackingType

initialize_ee()

# ─── PARAMETRELER ─────────────────────────────────────────────────────────────
AREA_HA      = 100
PANEL_TECH   = PanelTech.mono
TRACKING     = TrackingType.fixed
COUNTRY_CODE = "TR"

ADAYLAR = [
    {"isim": "Karapinar, Konya",   "lat": 37.73, "lon": 33.55},
    {"isim": "Harran, Sanliurfa",  "lat": 36.87, "lon": 39.03},
    {"isim": "Aksaray Ovasi",      "lat": 38.37, "lon": 33.97},
    {"isim": "Konya-Cumra",        "lat": 37.57, "lon": 32.78},
    {"isim": "Kirsehir Plateau",   "lat": 39.10, "lon": 34.10},
    {"isim": "Bozova, Sanliurfa",  "lat": 37.37, "lon": 38.51},
    {"isim": "Cankiri Ovasi",      "lat": 40.20, "lon": 33.60},
    {"isim": "Afyon-Iscehisar",    "lat": 38.87, "lon": 30.43},
]

# ─── TARAMA ───────────────────────────────────────────────────────────────────
print(f"\n{'Lokasyon':<28}  {'Egim%':>5}  {'GHI':>5}  {'Seb.km':>6}  {'Yol.km':>6}  {'SKOR':>5}")
print("-" * 65)

sonuclar = []

for a in ADAYLAR:
    lat, lon, isim = a["lat"], a["lon"], a["isim"]
    try:
        t   = terrain.analyse(lat, lon)
        ghi = solar.get_annual_ghi(lat, lon)
        gkm = grid.nearest_substation_km(lat, lon)
        rkm = access.nearest_road_km(lat, lon)
        leg = legal.check(lat, lon, t["lc_code"], t["slope_mean_pct"], COUNTRY_CODE)
        cap = capacity.calculate(t["slope_mean_pct"], ghi, AREA_HA, PANEL_TECH, TRACKING)
        fin = financial.calculate(cap["total_mw"], cap["annual_gwh"])
        res = mcda.score(
            t["slope_mean_pct"], ghi, t["aspect_score"], t["shadow_score"],
            t["lc_code"], gkm, rkm,
            yasal_score=leg["score"], hard_block=leg["hard_block"],
        )
        skor = res["total"]
        sonuclar.append({**a, "skor": skor, "terrain": t, "ghi": ghi,
                         "gkm": gkm, "rkm": rkm, "cap": cap, "fin": fin,
                         "leg": leg})
        block = " [BLOK]" if leg["hard_block"] else ""
        print(f"{isim:<28}  {t['slope_mean_pct']:5.1f}  {ghi:5.0f}  {gkm:6.1f}  {rkm:6.1f}  {skor:5.0f}{block}")
    except Exception as e:
        print(f"{isim:<28}  HATA: {str(e)[:35]}")

# ─── EN İYİ 3 ─────────────────────────────────────────────────────────────────
sonuclar.sort(key=lambda x: x["skor"], reverse=True)

print("\n" + "=" * 65)
print("  EN İYİ 3 LOKASYON")
print("=" * 65)
for i, s in enumerate(sonuclar[:3], 1):
    print(f"\n  {i}. {s['isim']}")
    print(f"     Skor       : {s['skor']:.0f}/100")
    print(f"     Koordinat  : {s['lat']:.4f}N  {s['lon']:.4f}E")
    print(f"     Eğim/GHI   : %{s['terrain']['slope_mean_pct']:.1f}  /  {s['ghi']:.0f} kWh/m²/yıl")
    print(f"     Toplam MW  : {s['cap']['total_mw']:.1f} MW  ({s['cap']['mw_per_ha']:.3f} MW/ha)")
    print(f"     Yıllık     : {s['cap']['annual_gwh']:.1f} GWh")
    print(f"     Yatırım    : ${s['fin']['investment_usd']:,.0f}  |  Geri ödeme: {s['fin']['payback_years']:.1f} yıl")
    if s["leg"]["hard_block"]:
        print(f"     [!] Yasal kısıt: {s['leg']['reason']}")
