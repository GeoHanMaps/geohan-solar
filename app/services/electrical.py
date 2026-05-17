"""
EPC-seviyesi elektriksel çekirdek — string boyutlama → inverter seçimi →
gerçek DC/AC + clipping → kablo (DC/AC/MV) IEC kesit + ohmik kayıp →
trafo kVA + kayıp → net AC + ekipman CAPEX.

Veri kaynağı: `config/equipment.json` (gerçek utility-scale GES datasheet'leri
+ IEC 60228). Deterministik, ağ çağrısı yok. `capacity.py` DC MW'ı tek kaynak —
buradan yalnız OKUNUR, dokunulmaz.

Tarama-seviyesi basitleştirmeler (EPC-screening, Faz 4.3/4.4'te derinleşir):
- PF≈1 kabulü → her segmentte %güç-kaybı ≈ %gerilim-düşümü.
- Clipping ampirik DC/AC eğrisi (Faz sonrası pvlib ile kalibre edilebilir).
- Segment uzunlukları temsilî sabit (Faz 4.4 gerçek güzergâhla değiştirir).
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional

_EQUIP_PATH = Path(__file__).parents[2] / "config" / "equipment.json"
_EQUIP: dict = {}

# Temsilî tek-yön segment uzunlukları (m) — Faz 4.4 gerçek güzergâhla değişir
_L_DC_STRING_M = 120.0
_L_AC_LV_M     = 150.0
_L_MV_M        = 500.0
_SIZING_FACTOR = 1.25  # IEC akım boyutlama / fuse faktörü
_TX_MAX_KVA    = 5000.0


def _equip() -> dict:
    global _EQUIP
    if not _EQUIP:
        _EQUIP = json.loads(_EQUIP_PATH.read_text(encoding="utf-8"))
    return _EQUIP


def _pick(library: dict, key: Optional[str], default_key: str) -> tuple[str, dict]:
    k = key if key and key in library else default_key
    return k, library[k]


def _vdrop_pct(current_a: float, r20_ohm_km: float, length_m: float,
               voltage_v: float, dc: bool) -> float:
    """Dirençsel gerilim düşümü %. DC: 2 telli; AC: 3-faz (√3)."""
    r_total = r20_ohm_km / 1000.0 * length_m          # Ω (tek yön)
    factor = 2.0 if dc else math.sqrt(3.0)
    if voltage_v <= 0:
        return 999.0
    return factor * current_a * r_total / voltage_v * 100.0


def _select_cable(material: str, current_a: float, length_m: float,
                  voltage_v: float, vdrop_max_pct: float,
                  dc: bool) -> tuple[float, float, int]:
    """
    IEC kesit seçimi paralel iletkenle. Tek kesit ampasiteyi (I·1.25)
    karşılamıyorsa gerekli paralel run sayısı hesaplanır; her iletken
    akımın 1/n'ini taşır. En küçük kesit: ampasite ≥ (I/n)·1.25 VE
    %Vdrop ≤ hedef. PF≈1 → %kayıp ≈ %vdrop (paralel run drop'u değiştirmez).
    Dönüş: (kesit_mm2, vdrop_pct, n_parallel).
    """
    table = _equip()["cables"][material]
    items = sorted(((float(mm2), v["r20"], float(v["amp"])) for mm2, v in table.items()),
                   key=lambda t: t[0])
    max_amp = max(a for _, _, a in items)
    n_par = max(1, math.ceil(current_a * _SIZING_FACTOR / max_amp))
    i_cond = current_a / n_par
    need_a = i_cond * _SIZING_FACTOR
    chosen = None
    for mm2, r20, amp in items:
        if amp < need_a:
            continue
        vd = _vdrop_pct(i_cond, r20, length_m, voltage_v, dc)
        if chosen is None:
            chosen = (mm2, vd, n_par)       # ampasiteyi geçen ilk kesit (yedek)
        if vd <= vdrop_max_pct:
            return mm2, vd, n_par
    if chosen is not None:
        return chosen                       # vdrop hedefi tutmadı → en iyi çaba
    mm2, r20, _ = items[-1]
    return mm2, _vdrop_pct(i_cond, r20, length_m, voltage_v, dc), n_par


def compute(
    dc_mw: float,
    tracking: str = "fixed",
    *,
    panel_model: Optional[str] = None,
    inverter_model: Optional[str] = None,
    cable_spec: Optional[str] = None,  # noqa: ARG001 — Faz 4.4 segment override
    mv_kv: Optional[float] = None,
) -> dict:
    """ElectricalSummary alanlarıyla birebir dict döndürür. grid_* = Faz 4.3."""
    eq = _equip()
    defs = eq["defaults"]
    cap_usd = eq["capex"]

    pname, p = _pick(eq["panels"]["library"], panel_model, eq["panels"]["default"])
    iname, inv = _pick(eq["inverters"]["library"], inverter_model, eq["inverters"]["default"])
    mv_kv = float(mv_kv if mv_kv else defs["mv_kv"])

    # ── 1. String boyutlama (sıcaklık pencereleri) ───────────────────────────
    t_min  = defs["design_temp_min_c"]
    t_cell = defs["design_temp_max_cell_c"]
    voc_cold = p["voc_v"] * (1 + p["beta_voc_pct_per_c"] / 100.0 * (t_min - 25.0))
    # Vmp sıcaklık katsayısı ≈ γPmax − αIsc (P=V·I güç ayrışımı)
    vmp_coeff = p["gamma_pmax_pct_per_c"] - p["alpha_isc_pct_per_c"]
    vmp_hot = p["vmp_v"] * (1 + vmp_coeff / 100.0 * (t_cell - 25.0))

    n_by_voc  = math.floor(inv["v_dc_max"] / voc_cold)
    n_by_mppt = math.floor(inv["mppt_v_max"] / vmp_hot)
    mods_per_string = max(1, min(n_by_voc, n_by_mppt))
    v_string = mods_per_string * p["vmp_v"]   # nominal string Vmp (vdrop paydası)

    # ── 2. Sayımlar + gerçek DC/AC ───────────────────────────────────────────
    pmax_w = p["pmax_w"]
    n_mod_ideal = max(1, round(dc_mw * 1e6 / pmax_w))
    n_strings = max(1, round(n_mod_ideal / mods_per_string))
    n_modules = n_strings * mods_per_string
    dc_kw = n_modules * pmax_w / 1000.0

    dc_ac_target = defs["dc_ac_ratio"].get(tracking, defs["dc_ac_ratio"]["fixed"])
    ac_kw_needed = dc_kw / dc_ac_target
    n_inverters = max(1, math.ceil(ac_kw_needed / inv["ac_kva"]))
    total_ac_kva = n_inverters * inv["ac_kva"]
    dc_ac_ratio = dc_kw / total_ac_kva

    # ── 3. Clipping (ampirik tarama eğrisi) ──────────────────────────────────
    clip_pct = max(0.0, min(8.0, (dc_ac_ratio - 1.15) * 18.0))

    # ── 4. Kablo sistemi (DC string / AC LV / MV toplama) ────────────────────
    seg = eq["cables"]["segments"]
    imp = p["imp_a"]
    dc_mm2, dc_loss, dc_par = _select_cable(
        seg["dc_string"]["material"], imp, _L_DC_STRING_M,
        v_string, seg["dc_string"]["v_drop_max_pct"], dc=True)
    i_ac_lv = inv["ac_kva"] * 1000.0 / (math.sqrt(3.0) * inv["v_ac_v"])
    ac_mm2, ac_loss, ac_par = _select_cable(
        seg["ac_lv"]["material"], i_ac_lv, _L_AC_LV_M,
        inv["v_ac_v"], seg["ac_lv"]["v_drop_max_pct"], dc=False)
    i_mv = total_ac_kva * 1000.0 / (math.sqrt(3.0) * mv_kv * 1000.0)
    mv_mm2, mv_loss, mv_par = _select_cable(
        seg["mv_collect"]["material"], i_mv, _L_MV_M,
        mv_kv * 1000.0, seg["mv_collect"]["v_drop_max_pct"], dc=False)

    # ── 5. Trafo kVA + kayıp ─────────────────────────────────────────────────
    tr = eq["transformers"]
    lf = tr["load_factor"]
    n_tx = max(1, math.ceil(total_ac_kva / (_TX_MAX_KVA * lf)))
    per_kva_needed = total_ac_kva / n_tx / lf
    tx_kva = next((float(r) for r in tr["kva_ratings"] if r >= per_kva_needed),
                  float(tr["kva_ratings"][-1]))
    tx_loss = tr["loss_no_load_pct"] + tr["loss_load_pct"] * lf ** 2

    # ── 5b. Koruma koordinasyonu ─────────────────────────────────────────────
    prot = eq["protection"]
    dc_need = p["isc_a"] * prot["dc_string_fuse_factor"]
    dc_fuse_a = next(
        (float(f) for f in prot["dc_fuse_std_a"]
         if f >= dc_need and f <= p["max_series_fuse_a"]),
        float(p["max_series_fuse_a"]))
    i_inv_nom = inv["ac_kva"] * 1000.0 / (math.sqrt(3.0) * inv["v_ac_v"])
    ac_breaker_a = round(i_inv_nom * prot["ac_breaker_factor"], 1)

    # ── 6. Enerji derate zinciri + net AC GÜCÜ ───────────────────────────────
    inv_eta = inv["eta_euro_pct"] / 100.0
    # Enerji domeni (DC → POC teslim, financial GWh için): clip + inverter
    # + tüm kablolar + trafo
    energy_factor = ((1 - clip_pct / 100.0) * inv_eta
                     * (1 - dc_loss / 100.0) * (1 - ac_loss / 100.0)
                     * (1 - mv_loss / 100.0) * (1 - tx_loss / 100.0))
    total_loss_pct = (1 - energy_factor) * 100.0
    # Net AC GÜCÜ = POC kapasitesi; AC nameplate'i aşamaz. Inverter-sonrası
    # kayıplar (AC LV + MV + trafo) düşülür; clipping/DC enerji-domenidir.
    ac_nameplate_mw = total_ac_kva / 1000.0
    net_ac_mw = (ac_nameplate_mw
                 * (1 - ac_loss / 100.0) * (1 - mv_loss / 100.0)
                 * (1 - tx_loss / 100.0))

    # ── 7. Ekipman CAPEX ─────────────────────────────────────────────────────
    cpm = cap_usd["cable_usd_per_mm2_m"]
    cable_capex = (
        dc_mm2 * _L_DC_STRING_M * n_strings   * dc_par * cpm[seg["dc_string"]["material"]]
        + ac_mm2 * _L_AC_LV_M   * n_inverters * ac_par * cpm[seg["ac_lv"]["material"]]
        + mv_mm2 * _L_MV_M      * n_tx        * mv_par * cpm[seg["mv_collect"]["material"]]
    )
    equip_capex = (total_ac_kva * cap_usd["inverter_usd_per_kva"]
                   + n_tx * tx_kva * cap_usd["transformer_usd_per_kva"]
                   + cable_capex)

    return {
        "panel_model": p["label"],
        "inverter_model": inv["label"],
        "inverter_type": inv["type"],
        "n_modules": int(n_modules),
        "modules_per_string": int(mods_per_string),
        "n_strings": int(n_strings),
        "n_inverters": int(n_inverters),
        "dc_ac_ratio": round(dc_ac_ratio, 3),
        "clipping_loss_pct": round(clip_pct, 3),
        "dc_cable_loss_pct": round(dc_loss, 3),
        "ac_cable_loss_pct": round(ac_loss, 3),
        "mv_cable_loss_pct": round(mv_loss, 3),
        "transformer_loss_pct": round(tx_loss, 3),
        "total_electrical_loss_pct": round(total_loss_pct, 3),
        "net_ac_mw": round(net_ac_mw, 3),
        "transformer_kva": float(tx_kva),
        "n_transformers": int(n_tx),
        "dc_string_cable_mm2": float(dc_mm2),
        "ac_lv_cable_mm2": float(ac_mm2),
        "mv_cable_mm2": float(mv_mm2),
        "equipment_capex_usd": float(round(equip_capex, 0)),
        "dc_string_fuse_a": float(dc_fuse_a),
        "ac_breaker_a": float(ac_breaker_a),
        "mv_relays": list(prot["mv_relays"]),
        "grid_voltage_rise_pct": None,
        "grid_short_circuit_mva": None,
        "grid_feasible": None,
    }


def default_mv_kv() -> float:
    return float(_equip()["defaults"]["mv_kv"])


def _cable_r20(material: str, mm2: float) -> float:
    table = _equip()["cables"][material]
    d = {float(k): v["r20"] for k, v in table.items()}
    if mm2 in d:
        return d[mm2]
    return d[min(d, key=lambda k: abs(k - mm2))]


def _grid_strength_mva(vn_kv: float) -> float:
    """Şebeke kısa-devre gücü kestirimi (voltaj sınıfına göre, eşik tablosu)."""
    for thr, mva in _equip()["defaults"]["grid_strength_mva"]:
        if vn_kv >= thr:
            return float(mva)
    return float(_equip()["defaults"]["grid_strength_mva"][-1][1])


def grid_check(
    net_ac_mw: float,
    target_kv: Optional[float],
    interconnect_km: float,
    mv_mm2: float,
    mv_kv: Optional[float] = None,
) -> dict:
    """
    pandapower MV besleyici yük-akışı + kısa-devre → POC bağlanabilirlik.
    Plant POC'ta sgen (net AC, PF≈1) → ext_grid (şebeke) hattı üzerinden.
    Tüm hatalar graceful: None döner (Faz 4.2 alanları korunur).
    """
    out = {"grid_voltage_rise_pct": None, "grid_short_circuit_mva": None,
           "grid_feasible": None}
    try:
        import pandapower as pp

        eq = _equip()
        defs = eq["defaults"]
        mv_material = eq["cables"]["segments"]["mv_collect"]["material"]
        mv_kv = float(mv_kv if mv_kv else defs["mv_kv"])
        vn = float(target_kv) if target_kv and target_kv > 0 else mv_kv
        sc_strength = _grid_strength_mva(vn)
        r = _cable_r20(mv_material, mv_mm2)
        x = defs["mv_line_x_ohm_per_km"]
        c = defs["mv_line_c_nf_per_km"]
        length_km = max(0.05, float(interconnect_km))

        net = pp.create_empty_network()
        b_grid = pp.create_bus(net, vn_kv=vn)
        b_poc = pp.create_bus(net, vn_kv=vn)
        pp.create_ext_grid(net, b_grid, vm_pu=1.0,
                           s_sc_max_mva=sc_strength, rx_max=0.1)
        pp.create_line_from_parameters(
            net, b_grid, b_poc, length_km=length_km,
            r_ohm_per_km=r, x_ohm_per_km=x, c_nf_per_km=c, max_i_ka=5.0)
        pp.create_sgen(net, b_poc, p_mw=net_ac_mw, q_mvar=0.0)
        pp.runpp(net)
        vm_poc = float(net.res_bus.vm_pu.at[b_poc])
        v_rise = (vm_poc - 1.0) * 100.0

        # Kısa-devre: pandapower.sc; başarısızsa analitik (Zsource+Zline)
        try:
            import pandapower.shortcircuit as psc
            psc.calc_sc(net, bus=b_poc, branch_results=False)
            ikss_ka = float(net.res_bus_sc.ikss_ka.at[b_poc])
            sc_mva = math.sqrt(3.0) * vn * ikss_ka
        except Exception:
            z_src = vn ** 2 / sc_strength
            z_line = math.hypot(r, x) * length_km
            sc_mva = vn ** 2 / (z_src + z_line) if (z_src + z_line) > 0 else None

        v_lim = defs["grid_voltage_rise_max_pct"]
        ratio_min = defs["grid_sc_ratio_min"]
        feasible = abs(v_rise) <= v_lim and (
            sc_mva is not None and sc_mva >= ratio_min * net_ac_mw)

        out["grid_voltage_rise_pct"] = round(v_rise, 3)
        out["grid_short_circuit_mva"] = round(sc_mva, 1) if sc_mva else None
        out["grid_feasible"] = bool(feasible)
    except Exception:
        pass
    return out
