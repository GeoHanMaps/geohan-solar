"""
World Bank / IFC Proje Veritabanı — Solar projeler
https://financesone.worldbank.org/

Kullanım:
    python scripts/fetch_worldbank.py
Çıktı:
    data/benchmark/worldbank_solar.json
"""

import json
import requests
from pathlib import Path

OUT_DIR = Path(__file__).parent.parent / "data" / "benchmark"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# World Bank Projects API (v2 — 2024+)
WB_API = "https://search.worldbank.org/api/v2/projects"
WB_HEADERS = {"User-Agent": "GeoHan Solar Intelligence/1.0 (info@geohan.com)"}


def fetch_solar_projects() -> list[dict]:
    print("World Bank proje veritabanından solar projeler çekiliyor...")
    projects = []
    rows = 100
    start = 0

    # searchStr parametresi ile geniş arama, sonra filtrele
    search_terms = ["solar photovoltaic", "solar energy project", "photovoltaic"]

    for term in search_terms:
        start = 0
        while True:
            resp = requests.get(
                WB_API,
                params={
                    "format": "json",
                    "rows": rows,
                    "start": start,
                    "searchStr": term,
                    "fl": "id,project_name,countryname,country_code,totalamt,boardapprovaldate,closingdate,sector1,status",
                },
                headers=WB_HEADERS,
                timeout=60,
            )

            if resp.status_code != 200:
                print(f"  '{term}': HTTP {resp.status_code}")
                break

            data = resp.json()
            raw = data.get("projects", {})
            if isinstance(raw, dict):
                items = list(raw.values())
            elif isinstance(raw, list):
                items = raw
            else:
                items = []

            if not items:
                print(f"  '{term}': veri yok (total={data.get('total',0)})")
                break

            # Sadece proje adında solar/pv geçenleri al
            kw = {"solar", "pv", "photovoltaic"}
            solar = [p for p in items
                     if any(w in (p.get("project_name") or "").lower() for w in kw)]

            existing_ids = {p.get("id") for p in projects}
            new_items = [p for p in solar if p.get("id") not in existing_ids]
            projects.extend(new_items)

            if new_items:
                print(f"  '{term}' sayfa {start//rows+1}: {len(new_items)} solar proje")

            if len(items) < rows or start > 2000:
                break
            start += rows

    return projects


def fetch_irena_lcoe_snapshot() -> dict:
    """
    IRENA 2024 LCOE verisi — manuel olarak girilmiş (PDF'den).
    Ülke/bölge bazında güneş LCOE USD/kWh
    """
    return {
        "_source": "IRENA Renewable Power Generation Costs 2024",
        "_url": "https://www.irena.org/Publications/2025/Jul/Renewable-power-generation-costs-in-2024",
        "global_avg_lcoe_usd_kwh": 0.043,
        "regions": {
            "China":          {"lcoe": 0.033, "capacity_gw_2024": 880},
            "India":          {"lcoe": 0.038, "capacity_gw_2024": 90},
            "USA":            {"lcoe": 0.070, "capacity_gw_2024": 180},
            "Europe":         {"lcoe": 0.055, "capacity_gw_2024": 320},
            "Middle East":    {"lcoe": 0.020, "capacity_gw_2024": 45},
            "Africa":         {"lcoe": 0.065, "capacity_gw_2024": 12},
            "Latin America":  {"lcoe": 0.045, "capacity_gw_2024": 55},
            "Asia Pacific":   {"lcoe": 0.048, "capacity_gw_2024": 120},
        },
        "country_benchmarks": {
            "SA": {"lcoe": 0.013, "notes": "Al Dhafra / NEOM en düşük teklifler"},
            "AE": {"lcoe": 0.014, "notes": "Mohammed bin Rashid Al Maktoum"},
            "CL": {"lcoe": 0.022, "notes": "Atacama çölü rekoru"},
            "IN": {"lcoe": 0.028, "notes": "ISTS müstesna ihale"},
            "BR": {"lcoe": 0.032, "notes": "ANEEL açık artırma"},
            "CN": {"lcoe": 0.033, "notes": "Ulusal ortalama"},
            "TR": {"lcoe": 0.0325, "notes": "YEKA GES-2024"},
            "MX": {"lcoe": 0.035, "notes": "CFE müzayede"},
            "AU": {"lcoe": 0.042, "notes": "ARENA verisi"},
            "US": {"lcoe": 0.043, "notes": "LBNL verisi"},
            "DE": {"lcoe": 0.058, "notes": "BNetzA ihale"},
            "JP": {"lcoe": 0.085, "notes": "FIT/FIP tariff"},
            "NG": {"lcoe": 0.070, "notes": "REFIT program tahmin"},
            "ZA": {"lcoe": 0.062, "notes": "REIPPPP Round 6"},
        },
    }


def main():
    # World Bank projeleri
    wb_projects = fetch_solar_projects()
    if wb_projects:
        out = OUT_DIR / "worldbank_solar.json"
        out.write_text(json.dumps(wb_projects, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nWorld Bank: {len(wb_projects)} proje → {out}")

        # Ülke dağılımı
        by_country: dict[str, int] = {}
        for p in wb_projects:
            cc = p.get("country_code", "??")
            by_country[cc] = by_country.get(cc, 0) + 1
        top = sorted(by_country.items(), key=lambda x: -x[1])[:10]
        print("Top 10 ülke:", top)
    else:
        print("World Bank verisi alınamadı (API değişmiş olabilir).")

    # IRENA LCOE snapshot
    irena = fetch_irena_lcoe_snapshot()
    out2 = OUT_DIR / "irena_lcoe_2024.json"
    out2.write_text(json.dumps(irena, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"IRENA LCOE snapshot → {out2}")


if __name__ == "__main__":
    main()
