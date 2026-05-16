"""Yer-gerçeği regresyon: model MW/ha tahmini, doğrulanmış gerçek
santrallerin kamuya açık kapasite/alan oranına tolerans içinde kalmalı.

Yalnızca `verified=true` kayıtlar zorlanır — uydurma/teyitsiz sayıya test
bağlanmaz. Karapınar çapası (bellek: gerçek 0.614 / model 0.616) burada
kalıcı koruma altına alınır; model fiziği kayarsa bu test düşer.
"""
import json
from pathlib import Path

import pytest

from app.schemas import PanelTech, TrackingType
from app.services import capacity

_DATASET = Path(__file__).resolve().parents[2] / "data" / "benchmark" / "real_plants.json"


@pytest.fixture(scope="module")
def dataset():
    return json.loads(_DATASET.read_text(encoding="utf-8"))


def _predicted(plant):
    return capacity.calculate(
        slope_pct=plant["slope_pct"],
        ghi_annual=1800.0,
        area_ha=plant["area_ha"],
        panel_tech=PanelTech(plant["panel_tech"]),
        tracking=TrackingType(plant["tracking"]),
    )["mw_per_ha"]


def test_dataset_schema_sane(dataset):
    assert dataset["plants"], "benchmark seti boş"
    for p in dataset["plants"]:
        assert p["capacity_mw"] > 0
        assert p["area_ha"] > 0
        assert p["panel_tech"] in {t.value for t in PanelTech}
        assert p["tracking"] in {t.value for t in TrackingType}
        assert isinstance(p["verified"], bool)


def test_at_least_one_verified_anchor(dataset):
    assert any(p["verified"] for p in dataset["plants"]), \
        "en az bir doğrulanmış yer-gerçeği çapası olmalı"


def test_verified_plants_within_tolerance(dataset):
    tol = dataset.get("tolerance_pct", 10.0)
    verified = [p for p in dataset["plants"] if p["verified"]]
    for p in verified:
        real = p["capacity_mw"] / p["area_ha"]
        pred = _predicted(p)
        err = abs(pred - real) / real * 100.0
        assert err <= tol, (
            f"{p['name']}: tahmin {pred:.3f} vs gerçek {real:.3f} "
            f"MW/ha — hata {err:.1f}% > tolerans {tol}%"
        )


def test_karapinar_anchor_tight(dataset):
    """Karapınar SITE_UTIL kalibrasyon çapası — sıkı band (≤%3).
    Bellek: gerçek 0.614, model 0.616."""
    kara = next(p for p in dataset["plants"] if p["name"].startswith("Karapınar"))
    real = kara["capacity_mw"] / kara["area_ha"]
    pred = _predicted(kara)
    assert abs(pred - real) / real * 100.0 <= 3.0
