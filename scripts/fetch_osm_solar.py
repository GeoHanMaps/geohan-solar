"""
OSM Overpass API — Dünya genelinde solar farm lokasyonları
power=plant + plant:source=solar veya generator:source=solar

Kullanım:
    python scripts/fetch_osm_solar.py                    # Tüm dünya
    python scripts/fetch_osm_solar.py --country TR       # Sadece Türkiye
    python scripts/fetch_osm_solar.py --country DE       # Sadece Almanya

Çıktı:
    data/benchmark/osm_solar_<country>.geojson
"""

import argparse
import json
import requests
import time
from pathlib import Path

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OUT_DIR = Path(__file__).parent.parent / "data" / "benchmark"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ISO-3166 → Nominatim ülke kodu (bazı ülkeler için özel)
COUNTRY_BBOX = {
    "TR": (35.8, 25.6, 42.1, 44.8),   # Türkiye (güney, batı, kuzey, doğu)
    "DE": (47.2, 5.8, 55.1, 15.1),    # Almanya
    "ES": (35.9, -9.4, 43.9, 4.4),    # İspanya
    "IN": (6.4, 68.1, 35.7, 97.4),    # Hindistan
    "CN": (18.0, 73.5, 53.6, 135.1),  # Çin
    "US": (24.4, -125.0, 49.4, -66.9),# ABD
    "ZA": (-34.8, 16.3, -22.1, 32.9), # Güney Afrika
    "SA": (16.3, 36.5, 32.2, 55.7),   # Suudi Arabistan
    "AU": (-43.6, 113.3, -10.7, 153.6),# Avustralya
    "BR": (-33.8, -73.9, 5.3, -29.3), # Brezilya
    "EG": (21.9, 24.7, 31.7, 37.1),   # Mısır
    "MA": (27.7, -13.2, 35.9, -1.0),  # Fas
    "NG": (4.3, 2.7, 13.9, 14.7),     # Nijerya
}


def build_query(country: str | None = None) -> str:
    bbox_filter = ""
    if country and country.upper() in COUNTRY_BBOX:
        s, w, n, e = COUNTRY_BBOX[country.upper()]
        bbox_filter = f"({s},{w},{n},{e})"

    return f"""
[out:json][timeout:120];
(
  node["power"="plant"]["plant:source"="solar"]{bbox_filter};
  way["power"="plant"]["plant:source"="solar"]{bbox_filter};
  relation["power"="plant"]["plant:source"="solar"]{bbox_filter};
  node["generator:source"="solar"]["power"="generator"]{bbox_filter};
  way["generator:source"="solar"]["power"="generator"]{bbox_filter};
);
out center tags;
"""


def fetch(country: str | None = None) -> list[dict]:
    query = build_query(country)
    label = country or "global"
    print(f"OSM Overpass sorgusu: {label}...")

    resp = requests.post(
        OVERPASS_URL,
        data={"data": query},
        timeout=180,
    )
    resp.raise_for_status()
    elements = resp.json().get("elements", [])
    print(f"  {len(elements)} tesis bulundu")
    return elements


def to_geojson(elements: list[dict]) -> dict:
    features = []
    for el in elements:
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if not lat or not lon:
            continue

        tags = el.get("tags", {})
        mw = None
        for key in ("plant:output:electricity", "generator:output:electricity", "capacity"):
            val = tags.get(key, "")
            if val:
                try:
                    # "50 MW", "50MW", "50000 kW" formatlarını handle et
                    val = val.lower().replace(" ", "")
                    if "kw" in val:
                        mw = float(val.replace("kw", "")) / 1000
                    elif "mw" in val:
                        mw = float(val.replace("mw", ""))
                    elif "gw" in val:
                        mw = float(val.replace("gw", "")) * 1000
                    break
                except ValueError:
                    pass

        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "osm_id":  el.get("id"),
                "osm_type": el.get("type"),
                "name":    tags.get("name"),
                "mw":      mw,
                "operator":tags.get("operator"),
                "start_date": tags.get("start_date"),
                "tags":    tags,
            },
        })

    return {"type": "FeatureCollection", "features": features}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--country", help="ISO-3166-1 alpha-2 ülke kodu (TR, DE, ...)")
    parser.add_argument("--all-countries", action="store_true", help="Bilinen tüm ülkeleri çek")
    args = parser.parse_args()

    if args.all_countries:
        for cc in COUNTRY_BBOX:
            try:
                elements = fetch(cc)
                gj = to_geojson(elements)
                out = OUT_DIR / f"osm_solar_{cc}.geojson"
                out.write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
                print(f"  Kaydedildi: {out} ({len(gj['features'])} tesis)")
                time.sleep(3)  # Overpass rate limit
            except Exception as e:
                print(f"  HATA {cc}: {e}")
    else:
        elements = fetch(args.country)
        gj = to_geojson(elements)
        label = args.country or "global"
        out = OUT_DIR / f"osm_solar_{label}.geojson"
        out.write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
        print(f"\nKaydedildi: {out} ({len(gj['features'])} tesis)")

        # Özet istatistik
        mw_values = [f["properties"]["mw"] for f in gj["features"] if f["properties"]["mw"]]
        if mw_values:
            print(f"MW verisi olan: {len(mw_values)} tesis")
            print(f"Toplam kapasite: {sum(mw_values):.0f} MW")
            print(f"Medyan: {sorted(mw_values)[len(mw_values)//2]:.1f} MW")


if __name__ == "__main__":
    main()
