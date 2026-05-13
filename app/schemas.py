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
    mw_per_ha:    float
    total_mw:     float
    annual_gwh:   float
    panel_tech:   str
    tracking:     str
    gcr_effective: float


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
    score:        int
    hard_block:   bool
    reason:       str
    wdpa_checked: bool


class AnalysisResult(BaseModel):
    """Analiz tamamlandığında dönen tam sonuç."""
    lat:          float
    lon:          float
    area_ha:      float
    utm_zone:     int
    total_score:  float
    irr_estimate: float = 0.0
    hard_block:   bool = False
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
    score_min:  float
    score_max:  float
    score_mean: float


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
