"""
AHP Pairwise Comparison — MCDA Ağırlık Kalibrasyonu.

Kullanım:
  cd GeoHan && conda activate geohan
  python scripts/ahp_calibrate.py                        # dry-run
  python scripts/ahp_calibrate.py --save                 # kaydet
  python scripts/ahp_calibrate.py --file config/ahp_comparisons.json --save

config/ahp_comparisons.json dosyasını düzenleyerek uzman yargılarını girin.
Saaty ölçeği: 1=eşit, 3=az üstün, 5=belirgin, 7=çok üstün, 9=mutlak.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.ahp import ahp_calibrate, parse_comparisons, SAATY_SCALE  # noqa: E402
from app.services.calibrate import CRITERIA, DEFAULT_WEIGHTS, compare_weights, load_current_weights  # noqa: E402

DEFAULT_FILE = ROOT / "config" / "ahp_comparisons.json"
N_PAIRS = len(CRITERIA) * (len(CRITERIA) - 1) // 2


def _bar(cr: float) -> str:
    filled = max(0, min(20, int((1 - cr / 0.20) * 20)))
    color = "OK " if cr < 0.10 else "!! "
    return color + "█" * filled + "░" * (20 - filled) + f" CR={cr:.3f}"


def print_header(title: str) -> None:
    print()
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)


def print_comparison_summary(raw: dict[str, float]) -> None:
    print(f"\n  {len(raw)}/{N_PAIRS} karşılaştırma girildi", end="")
    if len(raw) < N_PAIRS:
        print(f"  (eksik {N_PAIRS - len(raw)} çift → 1.0 [eşit] kabul edildi)")
    else:
        print()

    print(f"\n  {'Karşılaştırma':<28} {'Değer':>6}  Yorum")
    print(f"  {'-' * 55}")
    for k, v in raw.items():
        a, b = k.split("_vs_")
        if v >= 1:
            yorum = SAATY_SCALE.get(int(v), f"{v:.1f}x üstün")
            print(f"  {a:<10} > {b:<15} {v:>6.1f}  {yorum}")
        else:
            yorum = SAATY_SCALE.get(int(round(1 / v)), f"{1/v:.1f}x üstün")
            print(f"  {b:<10} > {a:<15} {1/v:>6.1f}  {yorum} (ters)")


def print_weight_table(result: dict) -> None:
    w = result["weights"]
    print(f"\n  {'Kriter':<10} {'AHP Ağırlık':>12} {'%':>6}  Çubuk")
    print(f"  {'-' * 50}")
    for c in CRITERIA:
        v = w[c]
        bar = "█" * int(v * 40)
        print(f"  {c:<10} {v:>12.4f} {v*100:>5.1f}%  {bar}")


def print_consistency(result: dict) -> None:
    lmax = result["lambda_max"]
    cr   = result["cr"]
    n    = len(CRITERIA)
    print(f"\n  λ_max        = {lmax:.4f}  (tutarlı matris için n={n})")
    print(f"  CR           = {cr:.4f}")
    print(f"  Tutarlılık   {_bar(cr)}")
    if result["consistent"]:
        print(f"\n  Karşılaştırmalar tutarlı (CR < 0.10).")
    else:
        print(f"\n  UYARI: CR >= 0.10 — karşılaştırmalar yeniden değerlendirilmeli.")
        print(f"  İpucu: Büyük değer farklılıklarını (9 vs 1 gibi) gözden geçirin.")


def main(comparisons_file: Path, save: bool) -> None:
    print_header("AHP PAIRWISE COMPARISON — MCDA AĞIRLIK KALİBRASYONU")

    if not comparisons_file.exists():
        print(f"\n  HATA: {comparisons_file} bulunamadı.")
        print(f"  Şablon: config/ahp_comparisons.json dosyasını düzenleyin.")
        sys.exit(1)

    data = json.loads(comparisons_file.read_text(encoding="utf-8"))
    raw_comparisons: dict[str, float] = data.get("comparisons", {})
    expert_name: str = data.get("expert", "Anonim")

    print(f"\n  Dosya     : {comparisons_file}")
    print(f"  Uzman     : {expert_name}")
    print(f"  Kriterler : {', '.join(CRITERIA)}")

    print_header("GİRİLEN KARŞILAŞTIRMALAR")
    print_comparison_summary(raw_comparisons)

    print_header("AHP AĞIRLIK HESABI")
    try:
        comparisons = parse_comparisons(raw_comparisons)
        result = ahp_calibrate(comparisons, save=False, expert_name=expert_name)
    except Exception as exc:
        print(f"\n  HATA: {exc}")
        sys.exit(1)

    print_weight_table(result)

    print_header("TUTARLILIK ANALİZİ")
    print_consistency(result)

    print_header("MEVCUT AĞIRLIKLARLA KARŞILAŞTIRMA")
    before = load_current_weights()
    print(f"\n  Mevcut yöntem: {'AHP' if before != dict(zip(CRITERIA, DEFAULT_WEIGHTS)) else 'Varsayılan'}")
    compare_weights(before, result["weights"])

    if save:
        print_header("KAYIT")
        if not result["consistent"]:
            print(f"\n  CR={result['cr']:.3f} >= 0.10 — kayıt reddedildi.")
            print(f"  Karşılaştırmaları düzeltin ve tekrar deneyin.")
            sys.exit(1)
        try:
            ahp_calibrate(comparisons, save=True, expert_name=expert_name)
            print(f"\n  Ağırlıklar kaydedildi → config/mcda_weights.json")
            print(f"  Regresyon testleri: pytest tests/validation/ -v")
        except Exception as exc:
            print(f"\n  HATA: {exc}")
            sys.exit(1)
    else:
        print(f"\n  Ağırlıklar kaydedilmedi (--save ekleyerek kaydet).")
        if not result["consistent"]:
            print(f"  Not: CR >= 0.10 olduğundan --save ile de kaydedilemez.")


if __name__ == "__main__":
    save_flag = "--save" in sys.argv
    file_arg = next(
        (sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--file" and i + 1 < len(sys.argv)),
        None,
    )
    comp_file = Path(file_arg) if file_arg else DEFAULT_FILE
    main(comp_file, save_flag)
