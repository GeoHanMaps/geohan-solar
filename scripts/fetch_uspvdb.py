"""
USPVDB — ABD Utility-Scale Solar Photovoltaic Database
https://energy.usgs.gov/uspvdb/
23,000+ proje: lokasyon, kapasite, kurulum yılı, maliyet

Kullanım:
    python scripts/fetch_uspvdb.py
Çıktı:
    data/benchmark/uspvdb.geojson
    data/benchmark/uspvdb_summary.json
"""

import json
import math
import requests
from pathlib import Path

USPVDB_API = "https://energy.usgs.gov/uspvdb/api/installations"
OUT_DIR = Path(__file__).parent.parent / "data" / "benchmark"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def fetch_all() -> list[dict]:
    print("USPVDB API'den veri çekiliyor...")
    features = []
    page = 1
    per_page = 1000

    while True:
        resp = requests.get(
            USPVDB_API,
            params={"page": page, "per_page": per_page},
            timeout=60,
        )
        if resp.status_code != 200:
            print(f"  HTTP {resp.status_code} — sayfa {page}")
            break

        data = resp.json()
        items = data.get("features") or data.get("data") or []
        if not items:
            break

        features.extend(items)
        print(f"  Sayfa {page}: {len(items)} proje (toplam: {len(features)})")

        if len(items) < per_page:
            break
        page += 1

    return features


def summarize(features: list[dict]) -> dict:
    capacities = []
    for f in features:
        props = f.get("properties") or f
        mw = props.get("p_cap_ac") or props.get("capacity_mw") or 0
        if mw:
            capacities.append(float(mw))

    if not capacities:
        return {}

    capacities.sort()
    n = len(capacities)
    return {
        "total_projects": n,
        "total_gw": round(sum(capacities) / 1000, 2),
        "median_mw": round(capacities[n // 2], 2),
        "mean_mw": round(sum(capacities) / n, 2),
        "min_mw": round(min(capacities), 2),
        "max_mw": round(max(capacities), 2),
        "p25_mw": round(capacities[n // 4], 2),
        "p75_mw": round(capacities[3 * n // 4], 2),
    }


def main():
    features = fetch_all()

    if not features:
        print("Veri alınamadı.")
        return

    geojson = {"type": "FeatureCollection", "features": features}
    out_file = OUT_DIR / "uspvdb.geojson"
    out_file.write_text(json.dumps(geojson, ensure_ascii=False), encoding="utf-8")
    print(f"\nKaydedildi: {out_file} ({len(features)} proje)")

    summary = summarize(features)
    summary_file = OUT_DIR / "uspvdb_summary.json"
    summary_file.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Özet: {summary_file}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
