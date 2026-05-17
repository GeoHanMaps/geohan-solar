from __future__ import annotations
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, model_validator


class PanelTech(str, Enum):
    mono     = "mono"
    poly     = "poly"
    bifacial = "bifacial"


class TrackingType(str, Enum):
    fixed       = "fixed"
    single_axis = "single_axis"
    dual_axis   = "dual_axis"


class AnalysisRequest(BaseModel):
    lat:          float        = Field(..., ge=-90,  le=90,    description="Enlem (WGS84)")
    lon:          float        = Field(..., ge=-180, le=180,   description="Boylam (WGS84)")
    area_ha:      float        = Field(..., gt=0,    le=50000, description="Alan (hektar)")
    name:         Optional[str] = Field(None, max_length=120)
    panel_tech:   PanelTech    = PanelTech.mono
    tracking:     TrackingType = TrackingType.fixed
    gcr:          Optional[float] = Field(None, gt=0, lt=1)
    country_code: Optional[str] = Field(None, max_length=2,
                                        description="ISO-3166-1 alpha-2 ülke kodu (TR, DE, …)")
    language:     str           = Field("Turkish",
                                        description="Rapor dili — dil adı (English, Arabic, German…) veya ISO kodu (en, ar, de…)")

    @model_validator(mode="after")
    def tracking_slope_warning(self):
        return self


class CriterionScore(BaseModel):
    value:  float
    unit:   str
    score:  int
    weight: float


class ScoreBreakdown(BaseModel):
    egim:    CriterionScore
    ghi:     CriterionScore
    baki:    CriterionScore
    golge:   CriterionScore
    arazi:   CriterionScore
    sebeke:  CriterionScore
    erisim:  CriterionScore
    yasal:   CriterionScore


class CapacityResult(BaseModel):
    mw_per_ha:          float
    total_mw:           float
    annual_gwh:         float
    panel_tech:         str
    tracking:           str
    gcr_effective:      float
    buildable_fraction: float = 1.0


class GridConnectionCost(BaseModel):
    voltage_level:       str
    line_km:             float
    line_cost_usd:       float
    substation_cost_usd: float
    total_usd:           float


class LogisticsCost(BaseModel):
    truck_trips:          int
    road_km:              float
    fuel_liters:          float
    fuel_cost_tl:         float
    road_improvement_tl:  float
    total_tl:             float


class FinancialResult(BaseModel):
    country_code:              str
    country_name:              str
    usd_tl:                    float
    epc_per_mw_usd:            float
    base_investment_usd:       float
    grid_connection:           GridConnectionCost
    logistics:                 LogisticsCost
    total_investment_usd:      float
    total_investment_tl:       float
    ppa_usd_per_kwh:           float
    annual_revenue_usd:        float
    annual_revenue_tl:         float
    opex_usd_per_mw_year:      float
    annual_opex_usd:           float
    net_annual_cashflow_usd:   float
    financing_rate:            float
    payback_years:             float
    irr_estimate:              float
    grid_reliability:          float


class LegalDetail(BaseModel):
    score:             int
    hard_block:        bool
    reason:            str
    wdpa_checked:      bool
    military_checked:  bool = False
    wdpa_name:         Optional[str] = None
    wdpa_iucn:         Optional[str] = None
    constraints:       list = []


class AnalysisResult(BaseModel):
    """Analiz tamamlandığında dönen tam sonuç."""
    lat:          float
    lon:          float
    area_ha:      float
    utm_zone:     int
    total_score:  float
    irr_estimate: float = 0.0
    hard_block:   bool = False
    ghi_p50:      float = 0.0   # TMY P50 yıllık GHI (kWh/m²/yıl)
    ghi_p90:      float = 0.0   # P90 exceedance — banka finansmanı için
    breakdown:    ScoreBreakdown
    capacity:     CapacityResult
    financial:    FinancialResult
    legal_detail: Optional[LegalDetail] = None


class JobResponse(BaseModel):
    """Her GET /analyses/{id} isteğinde döner."""
    id:     str
    status: str            # pending | running | done | failed
    name:   Optional[str] = None
    error:  Optional[str] = None
    result: Optional[AnalysisResult] = None


class JobListItem(BaseModel):
    id:     str
    status: str
    name:   Optional[str] = None


class HealthResponse(BaseModel):
    status: str = "ok"
    gee:    str
    osm:    str


# ─── Heatmap / Map analizi ────────────────────────────────────────────────────

class MapRequest(BaseModel):
    geom:         dict            = Field(..., description="GeoJSON Polygon veya MultiPolygon")
    resolution_m: int             = Field(250, ge=100, le=1000, description="Grid çözünürlüğü (m)")
    panel_tech:   PanelTech       = PanelTech.mono
    tracking:     TrackingType    = TrackingType.fixed
    country_code: str             = Field("DEFAULT", max_length=2)
    name:         Optional[str]   = Field(None, max_length=120)
    panel_model:    Optional[str] = Field(None, max_length=64,
                        description="config/equipment.json panels.library anahtarı (None → kütüphane default)")
    inverter_model: Optional[str] = Field(None, max_length=64,
                        description="config/equipment.json inverters.library anahtarı (None → kütüphane default)")
    cable_spec:     Optional[str] = Field(None, max_length=64,
                        description="Kablo profili override (None → equipment.json cables.segments default)")

    @model_validator(mode="after")
    def geom_type_check(self):
        t = self.geom.get("type", "")
        if t not in ("Polygon", "MultiPolygon"):
            raise ValueError("geom.type Polygon veya MultiPolygon olmalı")
        coords = self.geom.get("coordinates", [])
        total = sum(len(ring) for ring in (coords if t == "Polygon" else
                    [r for poly in coords for r in poly]))
        if total > 2000:
            raise ValueError("Polygon çok karmaşık: maksimum 2000 koordinat noktası")
        return self


class MapStats(BaseModel):
    score_min:   float
    score_max:   float
    score_mean:  float
    area_km2:    float = 0.0
    pixel_count: int   = 0


class MapJobResponse(BaseModel):
    id:                str
    status:            str
    name:              Optional[str]   = None
    error:             Optional[str]   = None
    stats:             Optional[MapStats] = None
    tile_url_template: Optional[str]   = None


class BoundaryResult(BaseModel):
    name:     str
    geojson:  dict
    bounds:   list[float]
    area_km2: float


# ─── Batch analiz ─────────────────────────────────────────────────────────────

class BatchLocation(BaseModel):
    lat:  float        = Field(..., ge=-90,  le=90)
    lon:  float        = Field(..., ge=-180, le=180)
    name: Optional[str] = Field(None, max_length=120)


class BatchRequest(BaseModel):
    locations:    list[BatchLocation] = Field(..., min_length=1, max_length=50)
    area_ha:      float               = Field(..., gt=0, le=50000)
    panel_tech:   PanelTech           = PanelTech.mono
    tracking:     TrackingType        = TrackingType.fixed
    gcr:          Optional[float]     = Field(None, gt=0, lt=1)
    country_code: str                 = Field("DEFAULT", max_length=2)
    language:     str                 = Field("Turkish",
                                              description="Rapor dili — dil adı veya ISO kodu")


class BatchLocationResult(BaseModel):
    rank:         int
    lat:          float
    lon:          float
    name:         Optional[str]
    total_score:  float
    irr_estimate: float = 0.0
    breakdown:    ScoreBreakdown
    capacity:     CapacityResult
    financial:    FinancialResult


class BatchJobResponse(BaseModel):
    id:              str
    status:          str
    total_locations: int
    completed:       int       = 0
    results:         list[BatchLocationResult] = []
    error:           Optional[str] = None


# ─── Sprint 9 M2: Auth / kullanıcı şemaları ─────────────────────────────────

class RegisterRequest(BaseModel):
    email:    str = Field(..., min_length=3, max_length=320,
                          description="Kullanıcı e-postası")
    password: str = Field(..., min_length=8, max_length=128,
                          description="Şifre (en az 8 karakter)")


class TokenResponse(BaseModel):
    access_token: str
    token_type:   str = "bearer"


class UserResponse(BaseModel):
    id:      str
    email:   str
    credits: int


# ─── Sprint 9 M3: Credit ledger şemaları ────────────────────────────────────

class BalanceResponse(BaseModel):
    user_id: str
    credits: int


class CreditTransactionItem(BaseModel):
    id:            str
    amount:        int
    balance_after: int
    reason:        str
    reference_id:  Optional[str] = None
    created_at:    str


class CreditHistoryResponse(BaseModel):
    items: list[CreditTransactionItem]
    total: int


# ─── Faz 2: GES Simülasyon Katmanı ──────────────────────────────────────────

class ElectricalSummary(BaseModel):
    """Faz 4.2 elektriksel çekirdek çıktısı; grid_* alanları Faz 4.3 yük-akışından."""
    panel_model:               str
    inverter_model:            str
    inverter_type:             str
    n_modules:                 int
    modules_per_string:        int
    n_strings:                 int
    n_inverters:               int
    dc_ac_ratio:               float
    clipping_loss_pct:         float
    dc_cable_loss_pct:         float
    ac_cable_loss_pct:         float
    mv_cable_loss_pct:         float
    transformer_loss_pct:      float
    total_electrical_loss_pct: float
    net_ac_mw:                 float
    transformer_kva:           float
    n_transformers:            int
    dc_string_cable_mm2:       float
    ac_lv_cable_mm2:           float
    mv_cable_mm2:              float
    equipment_capex_usd:       float
    dc_string_fuse_a:          Optional[float] = None
    ac_breaker_a:              Optional[float] = None
    mv_relays:                 Optional[list[str]] = None
    grid_voltage_rise_pct:     Optional[float] = None
    grid_short_circuit_mva:    Optional[float] = None
    grid_feasible:             Optional[bool]  = None


class LayoutSummary(BaseModel):
    dc_mw: float
    ac_mw: float
    n_blocks: int
    n_transformers: int
    buildable_ha: float
    gcr_effective: float
    interconnect_km: float
    interconnect_capex_usd: float
    target_substation_kv: Optional[float] = None
    slope_assumed: bool = False
    synthetic_grid: bool = False
    electrical: Optional[ElectricalSummary] = None


class LayoutResponse(BaseModel):
    summary: LayoutSummary
    geojson: dict
    single_line_svg: Optional[str] = None
