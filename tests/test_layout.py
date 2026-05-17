"""
layout.py birim testleri — GEE/OSM monkeypatch'li, ağ bağlantısı yok.

Sentetik GeoTIFF (50×50) kullanılır:
  - Kenar 10 piksel: -9999.0 (polygon dışı)
  - İç sol-üst köşe (10:20, 10:20): -1.0 (LC hard-block)
  - Geri kalan iç alan: 60.0 (buildable, >= BUILDABLE_SCORE_MIN=35)
"""
from __future__ import annotations

import io
import json
import math
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_bounds
from shapely.geometry import shape

from app.services import layout as lay


# ── Fixture: sentetik GeoTIFF ──────────────────────────────────────────────────

def _make_tiff(tmp_path: Path) -> Path:
    """50×50 test raster: edges=-9999, inner-corner=-1, interior=60."""
    data = np.full((50, 50), 60.0, dtype=np.float32)
    # Outer border (10 px): nodata
    data[:10, :] = -9999.0
    data[40:, :] = -9999.0
    data[:, :10] = -9999.0
    data[:, 40:] = -9999.0
    # Inner top-left corner: hard-block
    data[10:20, 10:20] = -1.0

    tiff = tmp_path / "test.tif"
    buf = io.BytesIO()
    tf = from_bounds(35.0, 39.0, 35.01, 39.01, 50, 50)
    with rasterio.open(
        buf, "w",
        driver="GTiff", height=50, width=50,
        count=1, dtype="float32",
        crs=CRS.from_epsg(4326),
        transform=tf,
        nodata=-9999.0,
    ) as dst:
        dst.write(data, 1)
    buf.seek(0)
    tiff.write_bytes(buf.read())
    return tiff


@pytest.fixture
def tiff_path(tmp_path):
    return _make_tiff(tmp_path)


@pytest.fixture
def no_osm(monkeypatch):
    """OSM çağrılarını ağsız yapıştır (exception → graceful degrade)."""
    def _raise(*a, **kw):
        raise ConnectionError("no network in tests")

    monkeypatch.setattr("app.services.layout.ox.features_from_point", _raise)
    monkeypatch.setattr("app.services.layout.ox.graph_from_point", _raise)


# ── Yardımcı ──────────────────────────────────────────────────────────────────

def _run(tiff_path, **kwargs):
    """_compute doğrudan çağır (disk cache yok)."""
    return lay._compute(
        tiff_path=str(tiff_path),
        country_code=kwargs.get("country_code", "DEFAULT"),
        panel_tech=kwargs.get("panel_tech", "mono"),
        tracking=kwargs.get("tracking", "fixed"),
    )


def _layers(result) -> dict[str, list]:
    """FeatureCollection'ı layer adına göre grupla."""
    groups: dict[str, list] = {}
    for f in result["geojson"]["features"]:
        lyr = f["properties"]["layer"]
        groups.setdefault(lyr, []).append(f)
    return groups


# ── Testler ────────────────────────────────────────────────────────────────────

def test_buildable_polygon_not_empty(tiff_path, no_osm):
    """Buildable mask rasterdan geçerli polygon çıkarmalı."""
    result = _run(tiff_path)
    layers = _layers(result)
    assert "buildable_area" in layers
    bp = shape(layers["buildable_area"][0]["geometry"])
    assert not bp.is_empty
    assert bp.area > 0


def test_setback_smaller_than_buildable(tiff_path, no_osm):
    """Setback uygulanmış alan ham buildable alandan küçük olmalı."""
    result = _run(tiff_path)
    layers = _layers(result)
    assert "setback" in layers
    buildable_area = shape(layers["buildable_area"][0]["geometry"]).area
    setback_area   = shape(layers["setback"][0]["geometry"]).area
    assert setback_area < buildable_area


def test_panel_blocks_inside_setback(tiff_path, no_osm):
    """Her panel_block setback polygonun içinde (veya üstünde) olmalı."""
    result = _run(tiff_path)
    layers = _layers(result)
    assert result["summary"]["n_blocks"] > 0, "panel_block üretilmedi"

    setback = shape(layers["setback"][0]["geometry"])
    for f in layers.get("panel_block", []):
        block = shape(f["geometry"])
        # intersection alanı >= blok alanının %90'ı (rasterio piksel kenarlarından
        # kaynaklanan küçük WGS84 dönüşüm hatalarına tolerans)
        overlap = setback.intersection(block).area
        assert overlap >= block.area * 0.90, f"Block {f['properties']['idx']} setback dışında"


def test_n_transformers_formula(tiff_path, no_osm):
    """n_transformers == ceil(ac_mw / 5.0)."""
    result = _run(tiff_path)
    s = result["summary"]
    assert s["n_blocks"] > 0
    expected = max(1, math.ceil(s["ac_mw"] / lay.MW_PER_TRANSFORMER))
    assert s["n_transformers"] == expected


def test_interconnect_route_geometry(tiff_path, no_osm):
    """interconnect_route: 3 noktalı L-route (POC→dirsek→hedef), km>0, synthetic."""
    result = _run(tiff_path)
    layers = _layers(result)
    assert "interconnect_route" in layers, "interconnect_route feature yok"
    feat = layers["interconnect_route"][0]
    coords = feat["geometry"]["coordinates"]
    assert len(coords) == 3, "Mühendislik güzergâhı L-route → 3 nokta"
    assert coords[0] != coords[-1], "POC ile hedef farklı olmalı"
    props = feat["properties"]
    assert props["km"] > 0
    assert props["synthetic"] is True  # OSM yok → sentetik


def test_osm_empty_gives_synthetic_grid(tiff_path, no_osm):
    """OSM çağrısı başarısız → osm_* feature yok, synthetic_grid=True."""
    result = _run(tiff_path)
    layers = _layers(result)
    assert "osm_substation" not in layers
    assert "osm_line" not in layers
    assert result["summary"]["synthetic_grid"] is True


def test_fc_valid_without_osm(tiff_path, no_osm):
    """OSM olmadan FeatureCollection yine geçerli (type + features listesi)."""
    result = _run(tiff_path)
    fc = result["geojson"]
    assert fc["type"] == "FeatureCollection"
    assert isinstance(fc["features"], list)
    assert len(fc["features"]) > 0


def test_determinism(tiff_path, no_osm):
    """Aynı girdi → aynı FeatureCollection (json.dumps eşit)."""
    r1 = _run(tiff_path)
    r2 = _run(tiff_path)
    assert json.dumps(r1["geojson"], sort_keys=True) == json.dumps(r2["geojson"], sort_keys=True)
    assert json.dumps(r1["summary"], sort_keys=True) == json.dumps(r2["summary"], sort_keys=True)


def test_generate_disk_cache(tmp_path, tiff_path, no_osm):
    """generate() disk cache'i kullanmalı: ikinci çağrı aynı sonucu dönmeli."""
    r1 = lay.generate(
        tiff_path=str(tiff_path),
        map_id="cache-test",
        data_dir=str(tmp_path),
    )
    geojson_file = tmp_path / "cache-test_layout.geojson"
    summary_file = tmp_path / "cache-test_layout.json"
    assert geojson_file.exists()
    assert summary_file.exists()

    # İkinci çağrı: diskten oku (OSM monkeypatch kaldırılsa bile farketmez)
    r2 = lay.generate(
        tiff_path=str(tiff_path),
        map_id="cache-test",
        data_dir=str(tmp_path),
    )
    assert json.dumps(r1["summary"], sort_keys=True) == json.dumps(r2["summary"], sort_keys=True)


def test_summary_fields_present(tiff_path, no_osm):
    """LayoutSummary alanlarının tamamı summary dict'te mevcut olmalı."""
    result = _run(tiff_path)
    s = result["summary"]
    required = [
        "dc_mw", "ac_mw", "n_blocks", "n_transformers",
        "buildable_ha", "gcr_effective",
        "interconnect_km", "interconnect_capex_usd",
        "slope_assumed", "synthetic_grid",
    ]
    for key in required:
        assert key in s, f"'{key}' summary'de eksik"
    assert s["slope_assumed"] is True
    assert s["buildable_ha"] > 0
    assert s["gcr_effective"] > 0
