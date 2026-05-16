"""Yer-gerçeği MW/ha benchmark — model tahmini vs gerçek çalışan GES'ler.

`app.services.capacity.calculate` saf fiziktir (ağ yok): MW/ha yalnızca
eğim + panel_tech + tracking'e bağlıdır. Bilinen gerçek santrallerin
kamuya açık kapasite/alan oranıyla karşılaştırırız → modelin gerçeği ne
kadar yansıttığını ölçen somut, deterministik bir sayı.

Kullanım:
    python scripts/benchmark_capacity.py
Çıktı: tahmin vs gerçek tablo + abs% hata + agregat + sektör-aralığı
sanity. Çıkış kodu: doğrulanmış (verified) kayıtların hepsi tolerans
içindeyse 0, değilse 1.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from app.schemas import PanelTech, TrackingType  # noqa: E402
from app.services import capacity  # noqa: E402

_DATASET = _ROOT / "data" / "benchmark" / "real_plants.json"
_DUMMY_GHI = 1800.0  # MW/ha GHI'dan bağımsız; sadece çağrı imzası için


def predicted_mw_per_ha(plant: dict) -> float:
    return capacity.calculate(
        slope_pct=plant["slope_pct"],
        ghi_annual=_DUMMY_GHI,
        area_ha=plant["area_ha"],
        panel_tech=PanelTech(plant["panel_tech"]),
        tracking=TrackingType(plant["tracking"]),
    )["mw_per_ha"]


def evaluate(dataset: dict) -> list[dict]:
    rows = []
    for p in dataset["plants"]:
        real = p["capacity_mw"] / p["area_ha"]
        pred = predicted_mw_per_ha(p)
        err = abs(pred - real) / real * 100.0
        rows.append({
            "name": p["name"], "country": p["country"],
            "verified": p["verified"],
            "real": real, "pred": pred, "err_pct": err,
        })
    return rows


def _industry_sanity() -> list[tuple[str, float]]:
    """Modelin kanonik konfig MW/ha'sı (düz arazi) — bellek panel-tech
    tablosuyla kıyas. Yer-gerçeği DEĞİL, iç tutarlılık sanity'si."""
    out = []
    for tech in (PanelTech.mono, PanelTech.poly, PanelTech.bifacial):
        for trk in (TrackingType.fixed, TrackingType.single_axis,
                    TrackingType.dual_axis):
            mwha = capacity.calculate(0.0, _DUMMY_GHI, 100.0, tech, trk)["mw_per_ha"]
            out.append((f"{tech.value}/{trk.value}", mwha))
    return out


def main() -> int:
    dataset = json.loads(_DATASET.read_text(encoding="utf-8"))
    tol = dataset.get("tolerance_pct", 10.0)
    rows = evaluate(dataset)

    print("=== Yer-gerçeği: tahmin vs gerçek MW/ha ===\n")
    print(f"{'Saha':<22}{'Ülke':<6}{'Gerçek':>8}{'Tahmin':>9}"
          f"{'Hata%':>8}  {'Durum'}")
    print("-" * 64)
    for r in rows:
        flag = "[verified]" if r["verified"] else "[unverified]"
        print(f"{r['name']:<22}{r['country']:<6}{r['real']:>8.3f}"
              f"{r['pred']:>9.3f}{r['err_pct']:>7.1f}%  {flag}")

    verified = [r for r in rows if r["verified"]]
    if verified:
        errs = [r["err_pct"] for r in verified]
        print(f"\nDoğrulanmış ({len(verified)}): ort. hata "
              f"{statistics.mean(errs):.2f}% · medyan "
              f"{statistics.median(errs):.2f}% · maks "
              f"{max(errs):.2f}% (tolerans {tol}%)")
    all_errs = [r["err_pct"] for r in rows]
    print(f"Tümü ({len(rows)}): ort. hata {statistics.mean(all_errs):.2f}%")

    print("\n=== Sektör-aralığı sanity (düz arazi, iç tutarlılık) ===")
    for label, mwha in _industry_sanity():
        print(f"  {label:<22}{mwha:>7.3f} MW/ha")

    breaches = [r for r in verified if r["err_pct"] > tol]
    if breaches:
        print("\n[FAIL] TOLERANS ASIMI (dogrulanmis kayit):")
        for b in breaches:
            print(f"  {b['name']}: {b['err_pct']:.1f}% > {tol}%")
        return 1
    print("\n[OK] Tum dogrulanmis kayitlar tolerans icinde.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
