"""
Tek-hat (single-line) şeması — ElectricalSummary'den deterministik SVG.

PV dizi → DC kombinör/sigorta → İnverter → AC kesici → Step-up trafo →
MV toplama barası (+ röleler) → POC/Şalt → Şebeke.

Saf fonksiyon, ağ/dosya yok. Faz 4.5'te PDF rapora gömülür; layout
yanıtında da `single_line_svg` olarak frontend'e döner.
"""
from __future__ import annotations

from typing import Optional
from xml.sax.saxutils import escape

_W = 540
_BOX_W = 360
_BOX_H = 52
_X = (_W - _BOX_W) // 2
_GAP = 34


def _box(y: int, title: str, sub: str, fill: str) -> str:
    return (
        f'<rect x="{_X}" y="{y}" width="{_BOX_W}" height="{_BOX_H}" rx="6" '
        f'fill="{fill}" stroke="#2b4a6f" stroke-width="1.5"/>'
        f'<text x="{_W//2}" y="{y+21}" text-anchor="middle" '
        f'font-family="Inter,Arial,sans-serif" font-size="14" '
        f'font-weight="600" fill="#0e2a47">{escape(title)}</text>'
        f'<text x="{_W//2}" y="{y+39}" text-anchor="middle" '
        f'font-family="Inter,Arial,sans-serif" font-size="11" '
        f'fill="#3a5c80">{escape(sub)}</text>'
    )


def _conn(y1: int, y2: int) -> str:
    return (f'<line x1="{_W//2}" y1="{y1}" x2="{_W//2}" y2="{y2}" '
            f'stroke="#2b4a6f" stroke-width="2"/>')


def build_svg(elec: dict, mv_kv: float,
              target_kv: Optional[float]) -> str:
    """elec = ElectricalSummary dict. Geçersiz/eksikse minimal SVG döner."""
    if not elec:
        return ('<svg xmlns="http://www.w3.org/2000/svg" width="10" '
                'height="10"></svg>')

    grid_kv = (f'{target_kv:g} kV' if target_kv else f'~{mv_kv:g} kV (sentetik)')
    fr = elec.get("grid_feasible")
    grid_sub = (f'Şebeke · {grid_kv} · '
                + ('UYGUN' if fr else 'UYGUN DEĞİL' if fr is False else 'belirsiz'))
    relays = ", ".join(elec.get("mv_relays") or [])

    rows = [
        ("PV Dizi",
         f'{elec["n_modules"]:,} modül · {elec["modules_per_string"]}/string · '
         f'{elec["n_strings"]:,} string', "#eaf2fb"),
        ("DC Kombinör + Sigorta",
         f'string sigortası {elec.get("dc_string_fuse_a","–")} A · '
         f'DC kablo {elec["dc_string_cable_mm2"]:g} mm²', "#eaf2fb"),
        ("İnverter",
         f'{elec["n_inverters"]} × {escape(elec["inverter_model"])} · '
         f'DC/AC {elec["dc_ac_ratio"]} · clip {elec["clipping_loss_pct"]}%',
         "#fbf3e6"),
        ("AC Kesici (LV)",
         f'{elec.get("ac_breaker_a","–")} A · AC kablo '
         f'{elec["ac_lv_cable_mm2"]:g} mm²', "#fbf3e6"),
        ("Step-up Trafo",
         f'{elec["n_transformers"]} × {elec["transformer_kva"]:g} kVA · '
         f'kayıp {elec["transformer_loss_pct"]}%', "#fbe9e9"),
        (f'MV Toplama Barası ({mv_kv:g} kV)',
         f'MV kablo {elec["mv_cable_mm2"]:g} mm² · röle: {relays}', "#fbe9e9"),
        ("POC / Şalt",
         f'net AC {elec["net_ac_mw"]:g} MW · '
         f'V-yük {elec.get("grid_voltage_rise_pct","–")}% · '
         f'Isc {elec.get("grid_short_circuit_mva","–")} MVA', "#e8f5ec"),
        ("Şebeke", grid_sub,
         "#d6f0df" if fr else "#f6dede" if fr is False else "#eee"),
    ]

    parts: list[str] = []
    y = 16
    for i, (t, s, f) in enumerate(rows):
        if i:
            parts.append(_conn(y - _GAP, y))
        parts.append(_box(y, t, s, f))
        y += _BOX_H + _GAP

    h = y - _GAP + 16
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_W}" height="{h}" '
        f'viewBox="0 0 {_W} {h}" font-family="Inter,Arial,sans-serif">'
        f'<rect width="{_W}" height="{h}" fill="#ffffff"/>'
        + "".join(parts) + '</svg>'
    )
