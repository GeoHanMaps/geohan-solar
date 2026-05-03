"""
Claude AI narrative servisi — analiz sonuçlarından yatırım yorumu üretir.
API key yoksa veya çağrı başarısız olursa None döner (PDF yine oluşur).
"""

from __future__ import annotations

_LANG_MAP = {
    "tr": "Turkish", "turkish": "Turkish",
    "en": "English", "english": "English",
    "ar": "Arabic",  "arabic":  "Arabic",
    "de": "German",  "german":  "German",
    "fr": "French",  "french":  "French",
    "es": "Spanish", "spanish": "Spanish",
    "ru": "Russian", "russian": "Russian",
    "zh": "Chinese", "chinese": "Chinese",
    "ja": "Japanese","japanese":"Japanese",
    "pt": "Portuguese","portuguese":"Portuguese",
    "it": "Italian", "italian": "Italian",
    "nl": "Dutch",   "dutch":   "Dutch",
    "ko": "Korean",  "korean":  "Korean",
    "hi": "Hindi",   "hindi":   "Hindi",
    "fa": "Persian", "persian": "Persian",
    "ur": "Urdu",    "urdu":    "Urdu",
    "sw": "Swahili", "swahili": "Swahili",
}

_ESA_LABELS = {
    10: "Tree cover", 20: "Shrubland", 30: "Grassland", 40: "Cropland",
    50: "Built-up", 60: "Bare/sparse vegetation", 70: "Snow/ice",
    80: "Permanent water", 90: "Herbaceous wetland", 95: "Mangroves", 100: "Moss/lichen",
}


def _resolve_language(lang: str) -> str:
    return _LANG_MAP.get(lang.lower(), lang)


def _score_label(score: float) -> str:
    if score >= 80: return "Excellent"
    if score >= 65: return "Good"
    if score >= 45: return "Moderate"
    return "Weak"


def _build_analysis_prompt(result: dict, language: str) -> str:
    bd  = result["breakdown"]
    cap = result["capacity"]
    fin = result["financial"]

    lc_code = int(bd["arazi"]["value"])
    lc_label = _ESA_LABELS.get(lc_code, f"ESA code {lc_code}")
    legal_score = bd["yasal"]["score"]
    legal_status = "BLOCKED (hard legal constraint)" if legal_score == 0 else "Clear"

    low_criteria = sorted(
        [(k, v["score"], v["weight"]) for k, v in bd.items()],
        key=lambda x: x[1]
    )[:3]
    low_str = ", ".join(
        f"{k} (score={s}, weight={int(w*100)}%)" for k, s, w in low_criteria
    )

    return f"""You are a senior solar energy investment analyst. Write a concise investment assessment report section in {language}.

## Site Data
- Coordinates: {result['lat']:.4f}°N, {result['lon']:.4f}°E
- Area: {result['area_ha']} ha | UTM Zone: {result['utm_zone']}
- Total Investment Score: {result['total_score']:.1f}/100 ({_score_label(result['total_score'])})

## Technical Criteria
| Criterion         | Value                         | Score |
|-------------------|-------------------------------|-------|
| Slope             | {bd['egim']['value']}%        | {bd['egim']['score']} |
| GHI (irradiance)  | {bd['ghi']['value']} kWh/m²/yr| {bd['ghi']['score']} |
| Aspect            | {bd['baki']['value']}°        | {bd['baki']['score']} |
| Shading loss      | {bd['golge']['value']}%       | {bd['golge']['score']} |
| Land cover        | {lc_label}                    | {bd['arazi']['score']} |
| Grid distance     | {bd['sebeke']['value']} km    | {bd['sebeke']['score']} |
| Road access       | {bd['erisim']['value']} km    | {bd['erisim']['score']} |
| Legal status      | {legal_status}                | {bd['yasal']['score']} |

## Capacity
- Installed: {cap['total_mw']:.1f} MW ({cap['mw_per_ha']} MW/ha) | Annual: {cap['annual_gwh']:.1f} GWh
- Technology: {cap['panel_tech']} | Tracking: {cap['tracking']} | GCR: {cap['gcr_effective']}

## Financial
- Total investment: ${fin['total_investment_usd']:,.0f}
- Annual revenue: {fin['annual_revenue_tl']/1e6:.1f}M TL
- Payback: {fin['payback_years']:.1f} years | IRR: {fin['irr_estimate']:.1f}%
- Grid reliability: {fin['grid_reliability']*100:.0f}%
- Country: {fin['country_name']}

## Weakest criteria (primary score drivers): {low_str}

---
Write exactly 4 paragraphs in {language}:
1. **Technical suitability** — terrain, solar resource, shading; be specific with numbers
2. **Infrastructure challenges** — grid distance, road access, reliability; note OSM data gaps if grid >20 km
3. **Financial outlook** — IRR, payback, revenue; compare to typical solar benchmarks (good IRR >8%, payback <12yr)
4. **Recommendation** — clear buy/hold/pass verdict with the single most important risk factor

Be direct. No bullet points. No headers. Plain paragraphs only. Reference actual numbers."""


def _build_batch_prompt(results: list[dict], language: str) -> str:
    rows = []
    for i, r in enumerate(results[:10], 1):
        fin = r["financial"]
        site_name = r.get("name") or f"{r['lat']:.3f}N {r['lon']:.3f}E"
        rows.append(
            f"{i}. {site_name}: "
            f"score={r['total_score']:.1f}, IRR={fin['irr_estimate']:.1f}%, "
            f"payback={fin['payback_years']:.1f}yr, grid={r['breakdown']['sebeke']['value']}km"
        )
    table = "\n".join(rows)
    top = results[0]
    fin0 = top["financial"]
    top_name = top.get("name") or f"{top['lat']:.4f}N, {top['lon']:.4f}E"

    return f"""You are a senior solar energy investment analyst. Write a comparative site assessment in {language}.

## Ranked Sites ({len(results)} total, showing top {min(len(results), 10)})
{table}

## Top Site Detail
- Name: {top_name}
- Score: {top['total_score']:.1f}/100 | IRR: {fin0['irr_estimate']:.1f}% | Payback: {fin0['payback_years']:.1f}yr
- Country: {fin0['country_name']}

---
Write exactly 3 paragraphs in {language}:
1. **Portfolio overview** — distribution of scores, general quality of the batch
2. **Top site analysis** — why it ranks first, its strengths and key risk
3. **Selection recommendation** — which site(s) to prioritize for due diligence and why

Be direct. No bullet points. No headers. Plain paragraphs only."""


def generate_analysis(result: dict, language: str = "Turkish") -> str | None:
    """Tekil analiz için Claude narratifi. Hata durumunda None döner."""
    from app.config import settings
    if not settings.anthropic_api_key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        lang = _resolve_language(language)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            messages=[{"role": "user", "content": _build_analysis_prompt(result, lang)}],
        )
        return msg.content[0].text.strip()
    except Exception:
        return None


def generate_batch(results: list[dict], language: str = "Turkish") -> str | None:
    """Batch karşılaştırma narratifi. Hata durumunda None döner."""
    from app.config import settings
    if not settings.anthropic_api_key or not results:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        lang = _resolve_language(language)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": _build_batch_prompt(results, lang)}],
        )
        return msg.content[0].text.strip()
    except Exception:
        return None
