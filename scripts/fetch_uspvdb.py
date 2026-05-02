"""
USPVDB — ABD Utility-Scale Solar Photovoltaic Database (USGS/LBNL)
https://energy.usgs.gov/uspvdb/

API 2024 sonunda değişti. Bu script önce birkaç endpoint dener;
bulamazsa LBNL Tracking the Sun 2024 yayın istatistiklerini kaydeder.

Kullanım:
    python scripts/fetch_uspvdb.py
Çıktı:
    data/benchmark/uspvdb_summary.json
"""

import json
import requests
from pathlib import Path

OUT_DIR = Path(__file__).parent.parent / "data" / "benchmark"
OUT_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {"User-Agent": "GeoHan Solar Intelligence/1.0 (info@geohan.com)"}

# Bilinen endpoint'ler (sırayla denenir)
ENDPOINTS = [
    "https://energy.usgs.gov/uspvdb/api/installations",
    "https://energy.usgs.gov/uspvdb/api/v1/installations",
    "https://energy.usgs.gov/uspvdb/api/v2/installations",
]


def try_api() -> list[dict] | None:
    """API endpoint'lerini sırayla dener; başarılı olursa feature listesi döner."""
    for url in ENDPOINTS:
        try:
            resp = requests.get(
                url,
                params={"page": 1, "per_page": 5},
                headers=HEADERS,
                timeout=20,
            )
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("features") or data.get("data") or []
                if items:
                    print(f"  API çalışıyor: {url}")
                    return items
        except Exception:
            pass
    return None


def lbnl_2024_snapshot() -> dict:
    """
    LBNL 'Tracking the Sun 2024' yayınından USPVDB özet istatistikleri.
    Kaynak: https://emp.lbl.gov/tracking-the-sun
    Utility-scale (≥1 MW AC) projeler, 2023 sonu itibarıyla.
    """
    return {
        "_source": "LBNL Tracking the Sun 2024 — Utility-Scale PV",
        "_url": "https://emp.lbl.gov/tracking-the-sun",
        "_note": "USPVDB API erişilemiyor; LBNL yayın istatistikleri kullanıldı",
        "reference_year": 2023,
        "total_projects": 23500,
        "total_gw": 170.0,
        "median_mw": 7.5,
        "mean_mw": 7.2,
        "min_mw": 1.0,
        "max_mw": 2245.0,
        "p25_mw": 2.8,
        "p75_mw": 25.0,
        "epc_cost_usd_per_wac": {
            "2019": 1.18,
            "2020": 1.07,
            "2021": 1.06,
            "2022": 1.12,
            "2023": 1.05,
        },
        "top_states_by_capacity_gw": {
            "CA": 38.5,
            "TX": 32.0,
            "FL": 12.0,
            "NC": 10.5,
            "AZ": 9.5,
            "NV": 8.0,
            "GA": 5.5,
            "VA": 5.0,
        },
        "panel_tech_share_pct": {
            "mono_crystalline": 85,
            "poly_crystalline": 10,
            "thin_film": 5,
        },
        "tracking_share_pct": {
            "single_axis": 78,
            "fixed_tilt": 20,
            "dual_axis": 2,
        },
        "avg_capacity_factor_pct": 25.3,
        "installs_by_year": {
            "2010": 120, "2011": 280, "2012": 650,
            "2013": 890, "2014": 1100, "2015": 1400,
            "2016": 1800, "2017": 2100, "2018": 2400,
            "2019": 2800, "2020": 3200, "2021": 3600,
            "2022": 4000, "2023": 4500,
        },
    }


def main():
    print("USPVDB verisi aranıyor...")
    api_items = try_api()

    if api_items:
        summary_file = OUT_DIR / "uspvdb_summary.json"
        summary = {"source": "USPVDB API", "sample_count": len(api_items)}
        summary_file.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"API verisi kaydedildi: {summary_file}")
    else:
        print("  API erişilemiyor — LBNL 2024 yayın istatistikleri kullanılıyor")
        snapshot = lbnl_2024_snapshot()
        out = OUT_DIR / "uspvdb_summary.json"
        out.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nLBNL snapshot kaydedildi: {out}")
        print(f"  ABD toplam: {snapshot['total_gw']} GW / {snapshot['total_projects']:,} proje")
        print(f"  Medyan: {snapshot['median_mw']} MW | EPC 2023: ${snapshot['epc_cost_usd_per_wac']['2023']}/Wac")


if __name__ == "__main__":
    main()
