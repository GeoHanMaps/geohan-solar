"""Faz 4.2–4.4 elektriksel çekirdek + grid + single-line testleri."""
import xml.dom.minidom as _xml

import pytest

from app.schemas import ElectricalSummary
from app.services import electrical as e
from app.services import single_line as sl


# ── compute() temel sözleşme ────────────────────────────────────────────────
@pytest.mark.parametrize("mw", [5.0, 50.0, 100.0, 600.0])
def test_compute_validates_and_orders(mw):
    r = e.compute(dc_mw=mw, tracking="fixed")
    ElectricalSummary(**r)                       # şema sözleşmesi
    ac_nameplate = mw / r["dc_ac_ratio"]
    # Güç sıralaması: net AC < AC nameplate < DC
    assert 0 < r["net_ac_mw"] < ac_nameplate < mw
    assert r["n_modules"] == r["n_strings"] * r["modules_per_string"]
    assert r["n_inverters"] >= 1 and r["n_transformers"] >= 1


def test_string_within_inverter_window():
    r = e.compute(dc_mw=100.0, tracking="fixed")
    eq = e._equip()
    p = eq["panels"]["library"][eq["panels"]["default"]]
    inv = eq["inverters"]["library"][eq["inverters"]["default"]]
    # En kötü hal (soğuk Voc) sistem max gerilimini aşmamalı
    voc_cold = p["voc_v"] * (1 + p["beta_voc_pct_per_c"] / 100 *
                             (eq["defaults"]["design_temp_min_c"] - 25))
    assert r["modules_per_string"] * voc_cold <= inv["v_dc_max"] + 1e-6
    assert r["modules_per_string"] >= 1


def test_dc_ac_and_loss_bands():
    r = e.compute(dc_mw=100.0, tracking="fixed")
    assert 1.10 <= r["dc_ac_ratio"] <= 1.35
    for k in ("clipping_loss_pct", "dc_cable_loss_pct", "ac_cable_loss_pct",
              "mv_cable_loss_pct", "transformer_loss_pct"):
        assert r[k] >= 0
    # Tarama-seviyesi makul toplam elektriksel kayıp bandı
    assert 3.0 <= r["total_electrical_loss_pct"] <= 12.0


def test_tracking_raises_dc_ac():
    fx = e.compute(dc_mw=100.0, tracking="fixed")["dc_ac_ratio"]
    sa = e.compute(dc_mw=100.0, tracking="single_axis")["dc_ac_ratio"]
    assert sa >= fx                                # tracker daha yüksek DC/AC hedefi


def test_model_override_changes_equipment():
    base = e.compute(dc_mw=50.0)
    over = e.compute(dc_mw=50.0, panel_model="monoperc_550",
                     inverter_model="central_3450kva_1500v")
    assert "550" in over["panel_model"]
    assert over["inverter_type"] == "central"
    assert over["inverter_model"] != base["inverter_model"]


def test_protection_fields():
    r = e.compute(dc_mw=20.0)
    eq = e._equip()
    p = eq["panels"]["library"][eq["panels"]["default"]]
    assert r["dc_string_fuse_a"] <= p["max_series_fuse_a"]
    assert r["dc_string_fuse_a"] >= p["isc_a"]      # Isc üstü
    assert r["ac_breaker_a"] > 0
    assert isinstance(r["mv_relays"], list) and len(r["mv_relays"]) >= 3


# ── grid_check (pandapower) ──────────────────────────────────────────────────
def test_grid_strong_hv_feasible():
    r = e.compute(dc_mw=100.0)
    g = e.grid_check(r["net_ac_mw"], target_kv=154.0,
                     interconnect_km=10.0, mv_mm2=r["mv_cable_mm2"])
    assert g["grid_feasible"] is True
    assert abs(g["grid_voltage_rise_pct"]) < 5.0
    assert g["grid_short_circuit_mva"] > 0


def test_grid_weak_far_infeasible():
    r = e.compute(dc_mw=100.0)
    g = e.grid_check(r["net_ac_mw"], target_kv=None,
                     interconnect_km=40.0, mv_mm2=r["mv_cable_mm2"])
    assert g["grid_feasible"] is False             # uzun zayıf MV → reddedilir


def test_grid_keys_always_present():
    g = e.grid_check(10.0, 33.0, 5.0, 95.0)
    assert set(g) == {"grid_voltage_rise_pct", "grid_short_circuit_mva",
                      "grid_feasible"}


# ── single-line SVG ──────────────────────────────────────────────────────────
def test_single_line_svg_wellformed():
    r = e.compute(dc_mw=100.0)
    r.update(e.grid_check(r["net_ac_mw"], 154.0, 12.0, r["mv_cable_mm2"]))
    svg = sl.build_svg(r, e.default_mv_kv(), 154.0)
    _xml.parseString(svg)                          # iyi-biçimli XML
    assert svg.strip().endswith("</svg>")
    assert "İnverter" in svg and "Şebeke" in svg


def test_single_line_empty_graceful():
    svg = sl.build_svg({}, 34.5, None)
    _xml.parseString(svg)
    assert "<svg" in svg


# ── Benchmark: 100 MW fixed sahası mantık bandı ─────────────────────────────
def test_benchmark_100mw_fixed():
    r = e.compute(dc_mw=100.0, tracking="fixed")
    assert 70.0 <= r["net_ac_mw"] <= 82.0          # ~80 nameplate − kayıplar
    assert 250.0 <= r["transformer_kva"] <= 5000.0
    assert r["equipment_capex_usd"] / 100_000.0 < 200  # < $200/kW ekipman
