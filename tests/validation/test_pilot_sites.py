"""
Sprint 8 — Pilot Validasyon Regresyon Testleri (11 saha).

Çalıştır:
  cd GeoHan && conda activate geohan
  pytest tests/validation/test_pilot_sites.py -v

Pre-kalibrasyon xfail:
  straubing_de — GHI ağırlığı düşük → model fazla ödüllendiriyor.
  scripts/pilot_validation.py --save ile kalibre ettikten sonra PASS olur.
"""

import json
from pathlib import Path

import pytest

from app.services import mcda

_SITES_FILE = Path(__file__).parent / "pilot_sites.json"
_SITES: list[dict] = json.loads(_SITES_FILE.read_text(encoding="utf-8"))

_PRE_CALIBRATION_XFAIL = {"straubing_de"}
_HARD_BLOCK_SITES = {s["id"] for s in _SITES if s["inputs"].get("hard_block", False)}


def _site_ids() -> list[str]:
    return [s["id"] for s in _SITES]


def _site_by_id(site_id: str) -> dict:
    return next(s for s in _SITES if s["id"] == site_id)


def pytest_generate_tests(metafunc):
    if "site" in metafunc.fixturenames:
        metafunc.parametrize("site", _SITES, ids=_site_ids())


def _run_mcda(inp: dict) -> dict:
    return mcda.score(
        slope_pct=inp["slope_pct"],
        ghi=inp["ghi"],
        aspect_score=inp["aspect_score"],
        shadow_score=inp["shadow_score"],
        lc_code=inp["lc_code"],
        grid_km=inp["grid_km"],
        road_km=inp["road_km"],
        yasal_score=inp.get("yasal_score", 100),
        hard_block=inp.get("hard_block", False),
    )


class TestPilotSites:
    def test_score_within_expert_range(self, site: dict):
        """Model skoru uzman beklenti aralığına [min, max] girmeli."""
        site_id = site["id"]
        if site_id in _PRE_CALIBRATION_XFAIL:
            pytest.xfail(
                reason=(
                    f"{site_id}: pre-kalibrasyon beklenen başarısızlık. "
                    "GHI ağırlığı düşük → Bavaria fazla ödüllendiriliyor. "
                    "scripts/pilot_validation.py --save ile kalibre et."
                )
            )

        inp = site["inputs"]
        result = _run_mcda(inp)
        total = result["total"]
        lo, hi = site["expected_score_min"], site["expected_score_max"]

        assert lo <= total <= hi, (
            f"{site['name']} — skor {total:.1f} beklenti [{lo}, {hi}] dışında.\n"
            f"  Kriter skorları: {result['scores']}\n"
            f"  Expert notu: {site['expert_notes'][:120]}"
        )

    def test_breakdown_keys_complete(self, site: dict):
        """Tüm sitelerde breakdown 8 kriter içermeli."""
        result = _run_mcda(site["inputs"])
        assert set(result["scores"].keys()) == set(mcda.get_weights().keys())

    def test_weights_sum_to_one(self, site: dict):
        """Ağırlıklar her site için 1.0'a toplanmalı."""
        result = _run_mcda(site["inputs"])
        assert abs(sum(result["weights"].values()) - 1.0) < 1e-6

    def test_hard_block_sites_score_zero(self):
        """Hard block siteler skor=0 döndürmeli (schwarzwald_de dahil)."""
        for site_id in _HARD_BLOCK_SITES:
            site = _site_by_id(site_id)
            result = _run_mcda(site["inputs"])
            assert result["total"] == 0.0, (
                f"{site_id} hard block skor beklenen 0, alınan: {result['total']}"
            )
            assert result.get("hard_block") is True

    def test_steep_sites_slope_score_below_100(self):
        """Eğimli siteler slope_score < 100 olmalı (egim sinyali)."""
        for site_id in ("bolzano_it", "kastamonu_tr"):
            site = _site_by_id(site_id)
            result = _run_mcda(site["inputs"])
            assert result["scores"]["egim"] < 100, (
                f"{site_id} slope_score beklenen <100, alınan: {result['scores']['egim']}"
            )

    def test_desert_sites_ghi_saturated(self):
        """Çöl siteleri GHI skoru 100 olmalı (GHI >= 2000)."""
        for site_id in ("atacama_cl", "riyadh_sa", "tabuk_sa", "ouarzazate_ma"):
            site = _site_by_id(site_id)
            result = _run_mcda(site["inputs"])
            assert result["scores"]["ghi"] == 100, (
                f"{site_id} GHI skoru 100 değil: {result['scores']['ghi']}"
            )

    def test_remote_sites_grid_zero(self):
        """Uzak şebeke siteleri sebeke skoru 0 olmalı (>30km)."""
        for site_id in ("niamey_ne", "tabuk_sa"):
            site = _site_by_id(site_id)
            result = _run_mcda(site["inputs"])
            assert result["scores"]["sebeke"] == 0, (
                f"{site_id} sebeke skoru beklenen 0, alınan: {result['scores']['sebeke']}"
            )

    def test_bavaria_ghi_zero(self):
        """Bavaria ve Schwarzwald GHI skoru 0 olmalı (GHI < 1200)."""
        for site_id in ("straubing_de", "schwarzwald_de"):
            site = _site_by_id(site_id)
            result = _run_mcda(site["inputs"])
            assert result["scores"]["ghi"] == 0, (
                f"{site_id} GHI skoru beklenen 0, alınan: {result['scores']['ghi']}"
            )

    def test_ranking_order(self):
        """Skor sıralaması mantıklı olmalı: desert > step > dağlık > bulutlu."""
        non_hb = {s["id"]: _run_mcda(s["inputs"])["total"]
                  for s in _SITES if not s["inputs"].get("hard_block", False)}
        assert non_hb["atacama_cl"] > non_hb["kastamonu_tr"]
        assert non_hb["riyadh_sa"] > non_hb["straubing_de"]
        assert non_hb["ouarzazate_ma"] > non_hb["bolzano_it"]
