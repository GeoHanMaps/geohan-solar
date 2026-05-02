"""
MCDA ağırlık kalibrasyon scripti.

Kullanım:
  python scripts/calibrate_weights.py --pilot-json results/pilot.json
  python scripts/calibrate_weights.py --pilot-json results/pilot.json --dry-run

pilot.json formatı (pilot_validate.py --output ile üretilir):
  [
    {
      "name": "...",
      "breakdown": {"egim": {"score": 80, "weight": 0.15}, ...},
      "expected": [60, 90],
      "score": 74.2,
      "ok": true
    },
    ...
  ]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services import calibrate as cal


def main() -> None:
    parser = argparse.ArgumentParser(description="GeoHan MCDA ağırlık kalibrasyonu")
    parser.add_argument("--pilot-json", required=True,
                        help="pilot_validate.py --output ile üretilen JSON dosyası")
    parser.add_argument("--dry-run", action="store_true",
                        help="Hesapla ama config/mcda_weights.json'ı güncelleme")
    args = parser.parse_args()

    path = Path(args.pilot_json)
    if not path.exists():
        print(f"HATA: Dosya bulunamadı: {path}", file=sys.stderr)
        sys.exit(1)

    data: list[dict] = json.loads(path.read_text(encoding="utf-8"))

    # Sadece başarılı, breakdown'u olan lokasyonları al
    usable = []
    skipped = []
    for r in data:
        if r.get("error") or not r.get("breakdown"):
            skipped.append(r.get("name", "?"))
            continue
        exp = r.get("expected", [0, 100])
        usable.append({
            "breakdown": r["breakdown"],
            "expected_min": float(exp[0]),
            "expected_max": float(exp[1]),
        })

    if skipped:
        print(f"Atlanan lokasyonlar (hata/eksik breakdown): {', '.join(skipped)}")

    print(f"Kalibrasyon başlıyor: {len(usable)} lokasyon\n")

    before = cal.load_current_weights()

    try:
        new_weights = cal.calibrate(usable, save=not args.dry_run)
    except (ValueError, RuntimeError) as e:
        print(f"HATA: {e}", file=sys.stderr)
        sys.exit(1)

    cal.compare_weights(before, new_weights)

    # Pilot sonuçlarında tahmin doğruluğunu göster
    print("Kalibrasyon sonrası tahmin doğruluğu:")
    print(f"  {'Lokasyon':<35} {'Beklenti':>12} {'Tahmin':>10} {'Fark':>8}")
    print("  " + "-" * 67)
    w_arr = [new_weights[c] for c in cal.CRITERIA]
    for r, u in zip(data, usable):
        bd = u["breakdown"]
        scores_arr = [bd.get(c, {}).get("score", 0) for c in cal.CRITERIA]
        predicted = sum(s * w for s, w in zip(scores_arr, w_arr))
        exp_mid = (u["expected_min"] + u["expected_max"]) / 2
        name = r.get("name", "?")[:35]
        rng = f"{u['expected_min']:.0f}–{u['expected_max']:.0f}"
        diff = predicted - exp_mid
        print(f"  {name:<35} {rng:>12} {predicted:>10.1f} {diff:>+8.1f}")

    if args.dry_run:
        print("\n(dry-run: config/mcda_weights.json güncellenmedi)")
    else:
        print(f"\nAğırlıklar güncellendi: config/mcda_weights.json")
        print("API yeniden başlatılınca yeni ağırlıklar aktif olur.")


if __name__ == "__main__":
    main()
