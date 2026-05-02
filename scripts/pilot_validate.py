"""
Sprint 8 — Pilot Validasyon Scripti
====================================
Gerçek arazi koordinatlarında API çağrısı yapar, beklenen skor
aralıklarını doğrular ve ağırlık kalibrasyonu için breakdown raporu üretir.

Kullanım:
    python scripts/pilot_validate.py --base-url http://localhost:8000 \
                                     --username admin --password geohan2024

Sunucu gereklilikleri: GEE, Solar servisi (PVGIS/NASA), OSM erişimi.
"""

import argparse
import json
import sys
import time
from dataclasses import dataclass
from typing import Optional

import requests

# ─── Pilot lokasyonlar ────────────────────────────────────────────────────────
# Her lokasyon için beklenen skor aralığı uzman tahminine dayanır.
# Pilot tamamlandıktan sonra bu değerler gerçek sonuçlarla güncellenir.

@dataclass
class PilotSite:
    name: str
    lat: float
    lon: float
    area_ha: float
    country_code: str
    expected_min: float   # beklenen minimum skor
    expected_max: float   # beklenen maksimum skor
    rationale: str


PILOT_SITES = [
    PilotSite(
        name="Suudi Arabistan — Rub al-Khali çölü",
        lat=22.5, lon=46.5, area_ha=100.0, country_code="SA",
        expected_min=65, expected_max=100,
        rationale="Düz çöl, GHI>2400, seyrek bitki örtüsü → yüksek skor beklenir",
    ),
    PilotSite(
        name="Konya — İç Anadolu stepleri",
        lat=37.87, lon=32.49, area_ha=50.0, country_code="TR",
        expected_min=55, expected_max=90,
        rationale="Orta GHI (~1800), düz step, şebeke altyapısı var",
    ),
    PilotSite(
        name="İspanya — La Mancha ovası",
        lat=39.2, lon=-3.0, area_ha=80.0, country_code="ES",
        expected_min=60, expected_max=92,
        rationale="Yüksek GHI (~1900), tarım+otlak mix, düz arazi",
    ),
    PilotSite(
        name="Almanya — Orta Almanya ormanı",
        lat=51.1, lon=10.0, area_ha=40.0, country_code="DE",
        expected_min=0, expected_max=35,
        rationale="Ormanlık alan (LC=10 hard block), düşük GHI (~1100) → düşük skor",
    ),
    PilotSite(
        name="İsviçre Alpleri — dik arazi",
        lat=46.5, lon=9.0, area_ha=30.0, country_code="CH",
        expected_min=5, expected_max=40,
        rationale="Eğim >%15, gölgeleme yüksek, zorlu arazi → düşük skor",
    ),
]


# ─── API istemci ──────────────────────────────────────────────────────────────

class GeoHanClient:
    def __init__(self, base_url: str, username: str, password: str):
        self.base = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.timeout = 30
        self._authenticate(username, password)

    def _authenticate(self, username: str, password: str) -> None:
        r = self.session.post(
            f"{self.base}/api/v1/auth/token",
            data={"username": username, "password": password},
        )
        r.raise_for_status()
        token = r.json()["access_token"]
        self.session.headers["Authorization"] = f"Bearer {token}"

    def submit(self, site: PilotSite) -> str:
        r = self.session.post(f"{self.base}/api/v1/analyses", json={
            "lat": site.lat, "lon": site.lon, "area_ha": site.area_ha,
            "country_code": site.country_code, "name": site.name,
        })
        r.raise_for_status()
        return r.json()["id"]

    def poll(self, job_id: str, timeout_s: int = 180) -> dict:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            r = self.session.get(f"{self.base}/api/v1/analyses/{job_id}")
            r.raise_for_status()
            data = r.json()
            if data["status"] == "done":
                return data
            if data["status"] == "failed":
                raise RuntimeError(f"Job {job_id} başarısız: {data.get('error')}")
            time.sleep(5)
        raise TimeoutError(f"Job {job_id} {timeout_s}s içinde tamamlanmadı")


# ─── Rapor ────────────────────────────────────────────────────────────────────

def _bar(score: float, width: int = 30) -> str:
    filled = int(score / 100 * width)
    color = "\033[92m" if score >= 60 else "\033[93m" if score >= 35 else "\033[91m"
    return f"{color}{'█' * filled}{'░' * (width - filled)}\033[0m"


def print_report(results: list[dict]) -> int:
    print("\n" + "=" * 72)
    print("GeoHan Sprint 8 — Pilot Validasyon Raporu")
    print("=" * 72)

    passed = failed = 0
    calibration_notes = []

    for r in results:
        site: PilotSite = r["site"]
        score: Optional[float] = r.get("score")
        error: Optional[str] = r.get("error")
        breakdown: Optional[dict] = r.get("breakdown")

        status_icon = "✓" if r.get("ok") else "✗"
        print(f"\n{status_icon} {site.name}")
        print(f"  Koordinat : {site.lat}, {site.lon} | {site.country_code}")
        print(f"  Beklenti  : {site.expected_min}–{site.expected_max}")

        if error:
            print(f"  HATA      : {error}")
            failed += 1
            continue

        if score is None:
            print("  Skor      : -")
            failed += 1
            continue

        ok = site.expected_min <= score <= site.expected_max
        if ok:
            passed += 1
        else:
            failed += 1

        print(f"  Skor      : {score:.1f}  {_bar(score)}")
        print(f"  Sonuç     : {'PASS' if ok else 'FAIL'} (beklenti: {site.expected_min}–{site.expected_max})")

        if breakdown:
            print("  Kriter dağılımı:")
            for key, v in breakdown.items():
                label = {"egim": "Eğim", "ghi": "GHI", "baki": "Bakı",
                         "golge": "Gölge", "arazi": "Arazi", "sebeke": "Şebeke",
                         "erisim": "Erişim", "yasal": "Yasal"}.get(key, key)
                s = v["score"]
                w = v["weight"]
                print(f"    {label:<8} : {s:5.1f} × {w:.2f} = {s*w:5.1f}  {_bar(s, 20)}")

        if not ok:
            diff = score - site.expected_min if score < site.expected_min else score - site.expected_max
            calibration_notes.append(
                f"  {site.name}: skor {score:.1f}, beklenti dışı ({diff:+.1f})"
            )

    print("\n" + "─" * 72)
    print(f"Sonuç: {passed}/{len(results)} PASS  |  {failed} FAIL")

    if calibration_notes:
        print("\nKalibrasyona dikkat gerektiren lokasyonlar:")
        for note in calibration_notes:
            print(note)

    print("=" * 72)
    return failed


def print_weight_calibration(results: list[dict]) -> None:
    """Kriter bazlı ortalama skor — ağırlık revizyonu için referans."""
    from collections import defaultdict
    sums: dict = defaultdict(list)
    for r in results:
        if r.get("breakdown"):
            for k, v in r["breakdown"].items():
                sums[k].append(v["score"])
    if not sums:
        return
    print("\nKalibrasyona referans — kriter bazlı ortalama ham skor:")
    for k, vals in sums.items():
        avg = sum(vals) / len(vals)
        print(f"  {k:<8}: {avg:5.1f}  (n={len(vals)})")


# ─── Ana akış ─────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="GeoHan pilot validasyon")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="geohan2024")
    parser.add_argument("--timeout", type=int, default=180,
                        help="Her analiz için maksimum bekleme süresi (saniye)")
    parser.add_argument("--output", help="Sonuçları JSON dosyasına yaz")
    args = parser.parse_args()

    print(f"Bağlanılıyor: {args.base_url}")
    client = GeoHanClient(args.base_url, args.username, args.password)
    print(f"{len(PILOT_SITES)} lokasyon analiz edilecek\n")

    results = []
    for site in PILOT_SITES:
        print(f"→ Gönderiliyor: {site.name}")
        try:
            job_id = client.submit(site)
            job = client.poll(job_id, timeout_s=args.timeout)
            result_data = job.get("result", {})
            score = result_data.get("total_score")
            breakdown = result_data.get("breakdown") if result_data else None
            ok = score is not None and site.expected_min <= score <= site.expected_max
            results.append({
                "site": site, "score": score, "breakdown": breakdown,
                "ok": ok, "job_id": job_id,
            })
            print(f"  Skor: {score:.1f}  {'✓' if ok else '✗'}")
        except Exception as exc:
            results.append({"site": site, "error": str(exc), "ok": False})
            print(f"  HATA: {exc}")

    n_failed = print_report(results)
    print_weight_calibration(results)

    if args.output:
        serializable = []
        for r in results:
            s = r["site"]
            serializable.append({
                "name": s.name, "lat": s.lat, "lon": s.lon,
                "expected": [s.expected_min, s.expected_max],
                "score": r.get("score"), "ok": r.get("ok"),
                "error": r.get("error"),
                "breakdown": r.get("breakdown"),
            })
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2)
        print(f"\nSonuçlar kaydedildi: {args.output}")

    sys.exit(0 if n_failed == 0 else 1)


if __name__ == "__main__":
    main()
