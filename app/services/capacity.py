import math
from app.schemas import PanelTech, TrackingType
from app.config import settings

PANEL_CFG = {
    PanelTech.mono:     {"efficiency": 0.22, "label": "Monokristal"},
    PanelTech.poly:     {"efficiency": 0.17, "label": "Polikristal"},
    PanelTech.bifacial: {"efficiency": 0.24, "label": "Bifasyal"},
}

TRACKING_CFG = {
    TrackingType.fixed:       {"label": "Sabit Egim",      "gcr": 0.40, "yield_factor": 1.00, "max_slope_pct": 99},
    TrackingType.single_axis: {"label": "Tek Eksen (SAT)", "gcr": 0.30, "yield_factor": 1.20, "max_slope_pct": 3},
    TrackingType.dual_axis:   {"label": "Cift Eksen (DAT)","gcr": 0.20, "yield_factor": 1.35, "max_slope_pct": 1},
}


def calculate(
    slope_pct: float,
    ghi_annual: float,
    area_ha: float,
    panel_tech: PanelTech,
    tracking: TrackingType,
    gcr_override: float | None = None,
) -> dict:
    p = PANEL_CFG[panel_tech]
    t = TRACKING_CFG[tracking]

    gcr = gcr_override or t["gcr"]
    terrain_factor  = math.cos(math.radians(math.atan(slope_pct / 100)))
    row_spacing_fac = max(0.5, 1 - (slope_pct / 100) * 0.5)
    effective_gcr   = gcr * terrain_factor * row_spacing_fac

    yield_factor = t["yield_factor"]
    if tracking == TrackingType.single_axis and slope_pct > t["max_slope_pct"]:
        yield_factor *= 0.7
    if tracking == TrackingType.dual_axis and slope_pct > t["max_slope_pct"]:
        yield_factor *= 0.5

    mw_per_ha  = round(effective_gcr * p["efficiency"] * 10 * yield_factor, 3)
    total_mw   = mw_per_ha * area_ha
    annual_gwh = ghi_annual * total_mw * 1000 * settings.performance_ratio / 1_000_000

    return {
        "mw_per_ha":     mw_per_ha,
        "total_mw":      total_mw,
        "annual_gwh":    round(annual_gwh, 2),
        "panel_label":   p["label"],
        "tracking_label": t["label"],
        "gcr_effective": round(effective_gcr, 3),
    }
