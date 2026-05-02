import json
from pathlib import Path

_RULES_PATH = Path(__file__).parent.parent.parent / "config" / "country_rules.json"

with open(_RULES_PATH, encoding="utf-8") as _f:
    _RULES: dict = json.load(_f)

# ESA WorldCover kodları — ülkeden bağımsız global hard block
_GLOBAL_HARD_BLOCK = {70, 80, 90, 95}  # kar/buz, su, sulak alan, mangrov


def _rules_for(country_code: str) -> dict:
    return _RULES.get(country_code.upper(), _RULES["DEFAULT"])


def check(
    lat: float,
    lon: float,
    lc_code: int,
    slope_pct: float,
    country_code: str = "DEFAULT",
) -> dict:
    """
    Yasal uygunluk skoru hesaplar (0–100).

    Hard block → score=0, hard_block=True  (MCDA'da toplam skor sıfırlanır)
    Soft block → score=40, hard_block=False (izin süreciyle çözülebilir)
    Temiz      → score=100

    WDPA (korunan alanlar) kontrolü şu an placeholder; offline veri
    yüklendiğinde _check_wdpa(lat, lon) çağrısı aktif edilecek.
    """
    rules = _rules_for(country_code)

    if lc_code in _GLOBAL_HARD_BLOCK:
        return _result(0, True,
                       f"ESA LC {lc_code} — global hard block (su/kar/sulak alan/mangrov)",
                       country_code)

    if lc_code in rules.get("forbidden_lc", []):
        return _result(0, True,
                       f"ESA LC {lc_code} — {country_code} ülke kuralında yasak",
                       country_code)

    max_slope = rules.get("max_slope_pct", 20)
    if slope_pct > max_slope:
        return _result(0, True,
                       f"Eğim %{slope_pct:.1f} > {country_code} sınırı %{max_slope}",
                       country_code)

    if lc_code in rules.get("soft_block_lc", []):
        return _result(40, False,
                       f"ESA LC {lc_code} — soft block, izin gerekebilir ({country_code})",
                       country_code)

    # TODO Sprint 3: WDPA offline shapefile kontrolü
    # if _check_wdpa(lat, lon):
    #     return _result(0, True, "WDPA korunan alan içinde", country_code)

    return _result(100, False, "Bilinen yasal kısıt yok", country_code)


def available_countries() -> list[str]:
    return [k for k in _RULES if k != "DEFAULT"]


def _result(score: int, hard_block: bool, reason: str, country_code: str) -> dict:
    return {
        "score":        score,
        "hard_block":   hard_block,
        "reason":       reason,
        "country_code": country_code,
        "wdpa_checked": False,
    }
