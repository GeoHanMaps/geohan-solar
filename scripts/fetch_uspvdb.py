"""
USPVDB — ABD Utility-Scale Solar Photovoltaic Database (USGS/LBNL)
https://eerscmap.usgs.gov/uspvdb/

Veri şu anda CSV olarak sunulmaktadır. Script CSV indirir, GeoJSON'a çevirir.

Kullanım:
    python scripts/fetch_uspvdb.py
Çıktı:
    data/benchmark/uspvdb.geojson
    data/benchmark/uspvdb_summary.json
"""

import csv
import io
import json
import math
import zipfile
import requests
from pathlib import Path

OUT_DIR = Path(__file__).parent.parent / "data" / "benchmark"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# USGS USPVDB — v2 public CSV (zip)
USPVDB_CSV_URL = "https://eerscmap.usgs.gov/uspvdb/data/uspvdb_v2_pub.csv.zip"
HEADERS = {"User-Agent": "GeoHan Solar Intelligence/1.0 (info@geohan.com)"}


def fetch_csv() -> list[dict]:
    print("USPVDB CSV indiriliyor (USGS)...")
    resp = requests.get(USPVDB_CSV_URL, headers=HEADERS, timeout=120, stream=True)
    resp.raise_for_status()

    content = b""
    for chunk in resp.iter_content(chunk_size=65536):
        content += chunk
    print(f"  İndirilen: {len(content)/1024/1024:.1f} MB")

    rows = []
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        csv_name = next(n for n in zf.namelist() if n.endswith(".csv"))
        print(f"  CSV: {csv_name}")
        with zf.open(csv_name) as f:
            reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
            for row in reader:
                rows.append(row)

    print(f"  {len(rows)} kayıt okundu")
    return rows


def rows_to_geojson(rows: list[dict]) -> dict:
    features = []
    for row in rows:
        try:
            lat = float(row.get("ylat") or row.get("lat") or 0)
            lon = float(row.get("xlong") or row.get("lon") or 0)
            if lat == 0 and lon == 0:
                continue
        except (ValueError, TypeError):
            continue

        # Kapasite (AC veya DC, MW)
        mw = None
        for key in ("p_cap_ac", "p_cap_dc", "capacity_mw", "cap_mw"):
            val = row.get(key, "").strip()
            if val:
                try:
                    mw = float(val)
                    break
                except ValueError:
                    pass

        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "case_id":    row.get("case_id"),
                "p_name":     row.get("p_name"),
                "p_state":    row.get("p_state"),
                "p_cap_ac":   mw,
                "p_year":     row.get("p_year") or row.get("install_year"),
                "p_tech":     row.get("p_tech_pri"),
                "p_axis":     row.get("p_axis"),
            },
        })

    return {"type": "FeatureCollection", "features": features}


def summarize(features: list[dict]) -> dict:
    capacities = []
    for f in features:
        mw = f["properties"].get("p_cap_ac")
        if mw and mw > 0:
            capacities.append(mw)

    if not capacities:
        return {}

    capacities.sort()
    n = len(capacities)
    by_year: dict[str, int] = {}
    for f in features:
        yr = str(f["properties"].get("p_year") or "")
        if yr.isdigit():
            by_year[yr] = by_year.get(yr, 0) + 1

    return {
        "total_projects": n,
        "total_gw": round(sum(capacities) / 1000, 2),
        "median_mw": round(capacities[n // 2], 2),
        "mean_mw": round(sum(capacities) / n, 2),
        "min_mw": round(min(capacities), 2),
        "max_mw": round(max(capacities), 2),
        "p25_mw": round(capacities[n // 4], 2),
        "p75_mw": round(capacities[3 * n // 4], 2),
        "installs_by_year": dict(sorted(by_year.items())),
    }


def main():
    try:
        rows = fetch_csv()
    except Exception as e:
        print(f"HATA: {e}")
        return

    gj = rows_to_geojson(rows)
    out_file = OUT_DIR / "uspvdb.geojson"
    out_file.write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
    print(f"\nKaydedildi: {out_file} ({len(gj['features'])} proje)")

    summary = summarize(gj["features"])
    summary_file = OUT_DIR / "uspvdb_summary.json"
    summary_file.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Özet: {summary_file}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
