"""
Sprint 8 — Pilot Validasyon + MCDA Ağırlık Kalibrasyonu.

Kullanım:
  cd GeoHan && conda activate geohan
  python scripts/pilot_validation.py           # dry-run (ağırlıkları kaydetme)
  python scripts/pilot_validation.py --save    # kalibre et ve config/mcda_weights.json güncelle

Çıktı:
  - 5 pilot sahanın mevcut ağırlıklarla skor tablosu
  - Uzman aralığı karşılaştırması
  - Kalibrasyon sonrası yeni ağırlıklar ve before/after skor karşılaştırması
"""

import json
import sys
from pathlib import Path

# Proje kökünü PYTHONPATH'e ekle
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services import mcda, calibrate  # noqa: E402

SITES_FILE = ROOT / "tests" / "validation" / "pilot_sites.json"

KOPPEN_SYMBOL = {
    "BSk": "🌾", "Cfb": "🌧️", "BWk": "☀️", "BSh": "🌵", "BWh": "🏜️",
}


def run_score(inp: dict) -> dict:
    return mcda.score(
        slope_pct=inp["slope_pct"],
        ghi=inp["ghi"],
        aspect_score=inp["aspect_score"],
        shadow_score=inp["shadow_score"],
        lc_code=inp["lc_code"],
        grid_km=inp["grid_km"],
        road_km=inp["road_km"],
        yasal_score=inp.get("yasal_score", 100),
        hard_block=inp.get("hard_block", False),
    )


def status_symbol(total: float, lo: float, hi: float) -> str:
    if lo <= total <= hi:
        return "PASS ✓"
    elif total < lo:
        return f"DÜŞÜK ↓ ({total - lo:+.1f})"
    else:
        return f"YÜKSEK ↑ ({total - hi:+.1f})"


def print_header(title: str) -> None:
    print()
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)


def print_site_table(sites: list[dict], results: list[dict]) -> None:
    header = f"{'Site':<28} {'Skor':>6} {'Beklenti':>12} {'Durum':<18} {'İklim'}"
    print(header)
    print("-" * 72)
    for site, res in zip(sites, results):
        total = res["total"]
        lo, hi = site["expected_score_min"], site["expected_score_max"]
        sym = KOPPEN_SYMBOL.get(site["climate_koppen"], "")
        st = status_symbol(total, lo, hi)
        print(
            f"{site['name']:<28} {total:>6.1f} "
            f"[{lo:.0f}–{hi:.0f}]{'':<3} {st:<18} {sym} {site['climate_koppen']}"
        )


def print_breakdown(site: dict, res: dict) -> None:
    print(f"\n  ► {site['name']} ({site['id']})")
    inp = site["inputs"]
    print(f"    GHI={inp['ghi']} kWh/m²/yıl  Eğim={inp['slope_pct']}%  "
          f"Şebeke={inp['grid_km']}km  Yol={inp['road_km']}km  "
          f"LC={inp['lc_code']}  Yasal={inp.get('yasal_score', 100)}")
    w = res["weights"]
    sc = res["scores"]
    criteria = calibrate.CRITERIA
    print(f"    {'Kriter':<10} {'Skor':>6} {'Ağırlık':>9} {'Katkı':>8}")
    print(f"    {'-'*35}")
    for c in criteria:
        contrib = sc[c] * w[c]
        print(f"    {c:<10} {sc[c]:>6.0f} {w[c]:>9.4f} {contrib:>8.2f}")
    print(f"    {'TOPLAM':<10} {'':>6} {'1.0000':>9} {res['total']:>8.1f}")


def make_calibration_entry(site: dict, res: dict) -> dict:
    bd = {c: {"score": float(res["scores"][c])} for c in calibrate.CRITERIA}
    return {
        "breakdown":      bd,
        "expected_min":   site["expected_score_min"],
        "expected_max":   site["expected_score_max"],
    }


def main(save: bool = False) -> None:
    sites: list[dict] = json.loads(SITES_FILE.read_text(encoding="utf-8"))

    # ── 1. Mevcut ağırlıklarla skorlar ─────────────────────────────────────────
    print_header("FAZ 5 — SPRINT 8 PİLOT VALİDASYON")
    print(f"Pilot sahalar: {SITES_FILE}")
    print(f"Ağırlık dosyası: {calibrate._WEIGHTS_FILE}")

    before_weights = calibrate.load_current_weights()
    results_before = [run_score(s["inputs"]) for s in sites]

    print_header("MEVCUT AĞIRLIKLARLA SKORLAR")
    print_site_table(sites, results_before)

    print_header("KRİTER BREAKDOWN (mevcut ağırlıklar)")
    for site, res in zip(sites, results_before):
        print_breakdown(site, res)

    # ── 2. Uzman aralığı analizi ────────────────────────────────────────────────
    print_header("UZMAN DEĞERLENDİRMESİ KİARŞILAŞTIRMASI")
    fails = []
    for site, res in zip(sites, results_before):
        lo, hi = site["expected_score_min"], site["expected_score_max"]
        total = res["total"]
        st = status_symbol(total, lo, hi)
        print(f"  {site['id']:<20} Skor={total:.1f}  Beklenti=[{lo:.0f},{hi:.0f}]  → {st}")
        if not (lo <= total <= hi):
            fails.append(site["id"])

    if fails:
        print(f"\n  ⚠  Aralık dışı: {', '.join(fails)}")
        print("     Bu siteler kalibrasyon sinyali üretiyor.")
    else:
        print("\n  Tüm siteler uzman aralığında ✓")

    # ── 3. Kalibrasyon ──────────────────────────────────────────────────────────
    print_header("MCDA AĞIRLIK KALİBRASYONU (scipy SLSQP)")
    cal_pairs = [(s, r) for s, r in zip(sites, results_before) if s.get("calibrate", True)]
    n_excluded = len(sites) - len(cal_pairs)
    if n_excluded:
        excl = [s["id"] for s in sites if not s.get("calibrate", True)]
        print(f"  Kalibrasyon dışı ({n_excluded} hard-block): {', '.join(excl)}")
    pilot_data = [make_calibration_entry(s, r) for s, r in cal_pairs]

    try:
        new_weights = calibrate.calibrate(pilot_data, save=save)
    except Exception as exc:
        print(f"  HATA: {exc}")
        return

    calibrate.compare_weights(before_weights, new_weights)

    # ── 4. Kalibre edilmiş ağırlıklarla yeniden skor ────────────────────────────
    import app.services.mcda as mcda_mod
    import os

    # Dosya mtime'ını alıp cache'i geçersiz kılmadan enjekte et
    try:
        _mtime = os.path.getmtime(mcda_mod._WEIGHTS_FILE)
    except OSError:
        _mtime = -1.0
    _orig_cache = mcda_mod._weights_cache
    mcda_mod._weights_cache = (_mtime, new_weights)
    results_after = [run_score(s["inputs"]) for s in sites]
    mcda_mod._weights_cache = _orig_cache

    print_header("KALİBRASYON SONRASI SKORLAR")
    print_site_table(sites, results_after)

    after_fails = []
    print()
    for site, rb, ra in zip(sites, results_before, results_after):
        lo, hi = site["expected_score_min"], site["expected_score_max"]
        st = status_symbol(ra["total"], lo, hi)
        delta = ra["total"] - rb["total"]
        print(
            f"  {site['id']:<20} Önce={rb['total']:.1f}  "
            f"Sonra={ra['total']:.1f} (Δ{delta:+.1f})  → {st}"
        )
        if not (lo <= ra["total"] <= hi):
            after_fails.append(site["id"])

    if after_fails:
        print(f"\n  ⚠  Kalibrasyon sonrası hâlâ dışarıda: {', '.join(after_fails)}")
    else:
        print("\n  Kalibrasyon başarılı — tüm siteler uzman aralığında ✓")

    if save:
        print(f"\n  Yeni ağırlıklar kaydedildi → {calibrate._WEIGHTS_FILE}")
        print("  Regresyon testleri: pytest tests/validation/ -v")
    else:
        print("\n  Ağırlıklar kaydedilmedi (--save ekleyerek kaydet).")


if __name__ == "__main__":
    save_flag = "--save" in sys.argv
    main(save=save_flag)
