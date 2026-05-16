"""
GES Simülasyon Katmanı — deterministik mühendislik şeması GeoJSON üretir.

Lazy: GET /maps/{id}/layout ilk çağrıda hesaplar, diske yazar;
sonraki çağrılar diskten okur. Kredi yok (heatmap 5 cr'ye dahil).

DOKUNULMAYAN: heatmap.py, mcda.py, capacity.py, tasks.py,
scoring/migration/Celery — bu modül yalnızca yeni dosya.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional

import numpy as np
import osmnx as ox
import rasterio
from rasterio.features import shapes as _rasterio_shapes
from shapely.geometry import LineString, Point, box, mapping, shape
from shapely.ops import transform as _shapely_transform
from shapely.ops import unary_union
from pyproj import Transformer

from app.schemas import PanelTech, TrackingType
from app.services import capacity as cap_svc
from app.services.grid import _reliability_to_km

# ── Modül sabitleri ────────────────────────────────────────────────────────────
BUILDABLE_SCORE_MIN  = 35.0
SETBACK_M            = 15.0
BLOCK_W              = 200.0
BLOCK_H              = 120.0
INTERNAL_ROAD_M      = 8.0
DC_AC_RATIO          = 1.2
MW_PER_TRANSFORMER   = 5.0
USD_PER_KM_LINE      = 250_000.0
OSM_SEARCH_M         = 30_000

_RULES_PATH = Path(__file__).parents[2] / "config" / "country_rules.json"
_COUNTRY_RULES: dict = {}


def _load_rules() -> dict:
    global _COUNTRY_RULES
    if not _COUNTRY_RULES:
        _COUNTRY_RULES = json.loads(_RULES_PATH.read_text(encoding="utf-8"))
    return _COUNTRY_RULES


def _min_grid_kv(country_code: str) -> float:
    rules = _load_rules()
    cc = (country_code or "DEFAULT").upper()
    cfg = rules.get(cc) or rules.get("DEFAULT", {})
    return float(cfg.get("min_grid_kv", 0))


def _parse_voltage_kv(raw) -> Optional[float]:
    """OSM voltage tag → max kV; None if missing/invalid."""
    if raw is None:
        return None
    if isinstance(raw, float) and math.isnan(raw):
        return None
    vals = []
    for part in str(raw).replace(";", "/").split("/"):
        part = part.strip()
        if not part:
            continue
        try:
            v = float(part)
            # OSM stores in V or kV; values > 1000 are in V
            vals.append(v / 1000.0 if v > 1000 else v)
        except ValueError:
            pass
    return max(vals) if vals else None


def _utm_epsg(lat: float, lon: float) -> int:
    zone = int((lon + 180) / 6) + 1
    return 32600 + zone if lat >= 0 else 32700 + zone


def _buildable_mask(data: np.ndarray) -> np.ndarray:
    """
    Buildable boolean mask.
    -9999 = polygon dışı, -1 = LC hard-block, < BUILDABLE_SCORE_MIN = düşük skor.
    # TODO(Faz1-E): replace score-threshold with dedicated buildable band
    """
    return (data != -9999.0) & (data != -1.0) & (data >= BUILDABLE_SCORE_MIN)


def _nearest_boundary_vertex(poly, target_pt: Point) -> Optional[Point]:
    """Polygon sınırının hedef noktaya en yakın vertex'i (UTM)."""
    best_d = float("inf")
    best_pt: Optional[Point] = None
    geoms = list(poly.geoms) if poly.geom_type == "MultiPolygon" else [poly]
    for g in geoms:
        if not hasattr(g, "exterior"):
            continue
        for c in g.exterior.coords:
            d = math.hypot(c[0] - target_pt.x, c[1] - target_pt.y)
            if d < best_d:
                best_d = d
                best_pt = Point(c[0], c[1])
    return best_pt


def _make_blocks(setback_poly) -> tuple[list, list]:
    """Panel blokları ve iç servis yollarını üret (UTM koordinatlarında)."""
    minx, miny, maxx, maxy = setback_poly.bounds
    step_x = BLOCK_W + INTERNAL_ROAD_M
    step_y = BLOCK_H + INTERNAL_ROAD_M

    col_xs: list[float] = []
    x = minx
    while x < maxx + 1e-3:
        col_xs.append(x)
        x += step_x

    row_ys: list[float] = []
    y = miny
    while y < maxy + 1e-3:
        row_ys.append(y)
        y += step_y

    kept: dict[tuple[int, int], bool] = {}
    blocks: list = []

    for ri, ry in enumerate(row_ys):
        for ci, cx in enumerate(col_xs):
            cell = box(cx, ry, cx + BLOCK_W, ry + BLOCK_H)
            inter = setback_poly.intersection(cell)
            if inter.is_empty or inter.area <= 0.15 * BLOCK_W * BLOCK_H:
                continue
            kept[(ri, ci)] = True
            # Kullan intersection: bloklar setback içinde kalır
            if inter.geom_type == "MultiPolygon":
                inter = max(inter.geoms, key=lambda g: g.area)
            blocks.append(inter)

    roads: list = []

    # Yatay yollar: bitişik satır çiftleri arası, her iki yanında blok varsa
    for ri in range(len(row_ys) - 1):
        y_road = row_ys[ri] + BLOCK_H + INTERNAL_ROAD_M / 2
        for ci in range(len(col_xs)):
            if kept.get((ri, ci)) and kept.get((ri + 1, ci)):
                cx = col_xs[ci]
                roads.append(LineString([(cx, y_road), (cx + BLOCK_W, y_road)]))

    # Dikey yollar: bitişik sütun çiftleri arası, her iki yanında blok varsa
    for ci in range(len(col_xs) - 1):
        x_road = col_xs[ci] + BLOCK_W + INTERNAL_ROAD_M / 2
        for ri in range(len(row_ys)):
            if kept.get((ri, ci)) and kept.get((ri, ci + 1)):
                ry = row_ys[ri]
                roads.append(LineString([(x_road, ry), (x_road, ry + BLOCK_H)]))

    return blocks, roads


def _place_transformers(blocks: list, n_tx: int) -> list[Point]:
    """
    Deterministik trafo pad yerleşimi.
    Snake sırası (row-major, tek satırlar ters) → n_tx ardışık eşit grup →
    her grubun blok centroid ortalaması.
    """
    if not blocks:
        return []

    step_y = BLOCK_H + INTERNAL_ROAD_M
    min_y = min(b.centroid.y for b in blocks)

    rows_map: dict[int, list] = {}
    for b in blocks:
        ri = round((b.centroid.y - min_y) / step_y)
        rows_map.setdefault(ri, []).append(b)

    ordered: list = []
    for ri in sorted(rows_map):
        row_blocks = sorted(rows_map[ri], key=lambda b: b.centroid.x)
        if ri % 2 == 1:
            row_blocks = row_blocks[::-1]
        ordered.extend(row_blocks)

    n = len(ordered)
    pads: list[Point] = []
    for i in range(n_tx):
        start = round(i * n / n_tx)
        end = round((i + 1) * n / n_tx)
        group = ordered[start:end]
        if not group:
            continue
        cx = sum(b.centroid.x for b in group) / len(group)
        cy = sum(b.centroid.y for b in group) / len(group)
        pads.append(Point(cx, cy))

    return pads


def _osm_context(
    lat_c: float,
    lon_c: float,
    setback_utm,
    to_utm: Transformer,
    from_utm: Transformer,
    country_code: str,
) -> tuple[list, Optional[Point], float, float, Optional[float], bool]:
    """
    OSM trafo/hat öznitelikleri + POC + bağlantı güzergâhı + erişim yolu üret.

    Returns:
        (features, plant_sub_wgs84, interconnect_km, interconnect_capex_usd,
         target_kv, synthetic_grid)
    Graceful degrade: OSM çağrısı başarısız olursa ilgili feature atlanır.
    """
    features: list = []
    synthetic_grid = False
    target_kv: Optional[float] = None
    interconnect_km = 0.0
    interconnect_capex_usd = 0.0
    plant_sub_wgs84: Optional[Point] = None

    min_kv = _min_grid_kv(country_code)
    cx_utm, cy_utm = to_utm.transform(lon_c, lat_c)

    # ── OSM trafo merkezleri ─────────────────────────────────────────
    # tower ASLA dahil edilmez (Faz1-A bug tekrarlanmasın)
    subs_list: list[tuple[float, float, Optional[float], str]] = []
    try:
        gdf = ox.features_from_point(
            (lat_c, lon_c),
            tags={"power": ["substation"]},
            dist=OSM_SEARCH_M,
        )
        for _, row in gdf.iterrows():
            g = row.geometry
            if g is None or g.is_empty:
                continue
            c = g if g.geom_type == "Point" else g.centroid
            kv = _parse_voltage_kv(row.get("voltage"))
            name = str(row.get("name") or "")
            subs_list.append((c.x, c.y, kv, name))
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(c.x, 6), round(c.y, 6)]},
                "properties": {"layer": "osm_substation", "kv": kv, "name": name},
            })
    except Exception:
        pass

    # ── OSM iletim hatları ───────────────────────────────────────────
    try:
        gdf_lines = ox.features_from_point(
            (lat_c, lon_c),
            tags={"power": ["line"]},
            dist=OSM_SEARCH_M,
        )
        for _, row in gdf_lines.iterrows():
            g = row.geometry
            if g is None or g.is_empty:
                continue
            kv = _parse_voltage_kv(row.get("voltage"))
            name = str(row.get("name") or "")
            features.append({
                "type": "Feature",
                "geometry": mapping(g),
                "properties": {"layer": "osm_line", "kv": kv, "name": name},
            })
    except Exception:
        pass

    # ── Hedef trafo seç ──────────────────────────────────────────────
    target_sub_utm: Optional[Point] = None

    if subs_list:
        best_d = float("inf")
        # Önce voltaj filtresini geç
        for slon, slat, kv, _ in subs_list:
            if min_kv > 0 and kv is not None and kv < min_kv:
                continue
            ex, ey = to_utm.transform(slon, slat)
            d = math.hypot(cx_utm - ex, cy_utm - ey)
            if d < best_d:
                best_d = d
                target_sub_utm = Point(ex, ey)
                target_kv = kv

        # Filtreyi geçen yoksa: voltajı bilinen herhangi en yakın
        if target_sub_utm is None:
            for slon, slat, kv, _ in subs_list:
                ex, ey = to_utm.transform(slon, slat)
                d = math.hypot(cx_utm - ex, cy_utm - ey)
                if d < best_d:
                    best_d = d
                    target_sub_utm = Point(ex, ey)
                    target_kv = kv

    if target_sub_utm is None:
        synthetic_grid = True
        est_km = _reliability_to_km(0.75)
        # Sentetik hedef: polygon centroid'inden doğuya (basit yön)
        target_sub_utm = Point(cx_utm + est_km * 1000, cy_utm)

    # ── Şalt (POC) ───────────────────────────────────────────────────
    poc_utm = _nearest_boundary_vertex(setback_utm, target_sub_utm)

    if poc_utm is not None:
        route_len = math.hypot(poc_utm.x - target_sub_utm.x, poc_utm.y - target_sub_utm.y)
        interconnect_km = route_len / 1000.0
        interconnect_capex_usd = interconnect_km * USD_PER_KM_LINE

        poc_lon, poc_lat = from_utm.transform(poc_utm.x, poc_utm.y)
        plant_sub_wgs84 = Point(poc_lon, poc_lat)
        tgt_lon, tgt_lat = from_utm.transform(target_sub_utm.x, target_sub_utm.y)

        features.append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [round(poc_lon, 6), round(poc_lat, 6)],
                    [round(tgt_lon, 6), round(tgt_lat, 6)],
                ],
            },
            "properties": {
                "layer": "interconnect_route",
                "km": round(interconnect_km, 2),
                "capex_usd": round(interconnect_capex_usd, 0),
                "synthetic": synthetic_grid,
            },
        })

    # ── Erişim yolu ──────────────────────────────────────────────────
    try:
        G = ox.graph_from_point(
            (lat_c, lon_c),
            dist=15_000,
            network_type="drive",
            custom_filter='["highway"~"motorway|trunk|primary|secondary|tertiary"]',
        )
        best_d = float("inf")
        nearest_node_wgs84: Optional[tuple[float, float]] = None
        nearest_node_utm: Optional[Point] = None
        for _, data in G.nodes(data=True):
            nx_u, ny_u = to_utm.transform(data["x"], data["y"])
            d = math.hypot(cx_utm - nx_u, cy_utm - ny_u)
            if d < best_d:
                best_d = d
                nearest_node_wgs84 = (data["x"], data["y"])
                nearest_node_utm = Point(nx_u, ny_u)

        if nearest_node_utm is not None and nearest_node_wgs84 is not None:
            gate_utm = _nearest_boundary_vertex(setback_utm, nearest_node_utm)
            if gate_utm is not None:
                gate_lon, gate_lat = from_utm.transform(gate_utm.x, gate_utm.y)
                road_lon, road_lat = nearest_node_wgs84
                features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [
                            [round(gate_lon, 6), round(gate_lat, 6)],
                            [round(road_lon, 6), round(road_lat, 6)],
                        ],
                    },
                    "properties": {"layer": "access_route"},
                })
    except Exception:
        pass

    return features, plant_sub_wgs84, interconnect_km, interconnect_capex_usd, target_kv, synthetic_grid


def _empty_result() -> dict:
    return {
        "summary": {
            "dc_mw": 0.0, "ac_mw": 0.0,
            "n_blocks": 0, "n_transformers": 0,
            "buildable_ha": 0.0, "gcr_effective": 0.0,
            "interconnect_km": 0.0, "interconnect_capex_usd": 0.0,
            "target_substation_kv": None,
            "slope_assumed": True, "synthetic_grid": False,
        },
        "geojson": {"type": "FeatureCollection", "features": []},
    }


def _compute(
    tiff_path: str,
    country_code: str,
    panel_tech: str,
    tracking: str,
) -> dict:
    """Deterministik layout hesapla (ağ çağrıları graceful degrade)."""

    # 1. GeoTIFF oku
    with rasterio.open(tiff_path) as src:
        data = src.read(1)
        tf = src.transform

    # 2. Buildable mask → WGS84 polygon(lar)
    mask = _buildable_mask(data)
    if not mask.any():
        return _empty_result()

    mask_u8 = mask.astype(np.uint8)
    polys = [
        shape(g)
        for g, v in _rasterio_shapes(mask_u8, mask=mask_u8, transform=tf)
        if v == 1
    ]
    polys = [p for p in polys if p.is_valid and not p.is_empty]
    if not polys:
        return _empty_result()

    buildable_wgs84 = unary_union(polys)

    # 3. UTM dönüşümü
    c = buildable_wgs84.centroid
    lat_c, lon_c = c.y, c.x
    utm_epsg = _utm_epsg(lat_c, lon_c)
    to_utm = Transformer.from_crs("EPSG:4326", f"EPSG:{utm_epsg}", always_xy=True)
    from_utm = Transformer.from_crs(f"EPSG:{utm_epsg}", "EPSG:4326", always_xy=True)

    def project(geom):
        return _shapely_transform(to_utm.transform, geom)

    def unproject(geom):
        return _shapely_transform(from_utm.transform, geom)

    buildable_utm = project(buildable_wgs84)
    buildable_ha = buildable_utm.area / 10_000  # m² → ha

    features: list = [
        {"type": "Feature", "geometry": mapping(buildable_wgs84), "properties": {"layer": "buildable_area"}},
    ]

    # 4. Setback
    setback_utm = buildable_utm.buffer(-SETBACK_M)
    if setback_utm.is_empty:
        return _empty_result()

    setback_wgs84 = unproject(setback_utm)
    features.append(
        {"type": "Feature", "geometry": mapping(setback_wgs84), "properties": {"layer": "setback"}}
    )

    # 5. Kapasite (tek kaynak — capacity.py)
    # slope_mean_pct=0.0: raster'da eğim bandı yok (v1 proxy)
    # ghi=1600.0: yalnız annual_gwh'i etkiler; layout summary göstermez
    pt_enum = PanelTech(panel_tech)
    tr_enum = TrackingType(tracking)
    cap = cap_svc.calculate(
        slope_pct=0.0,
        ghi_annual=1600.0,
        area_ha=buildable_ha,
        panel_tech=pt_enum,
        tracking=tr_enum,
        gcr_override=None,
        buildable_fraction=1.0,
    )
    dc_mw = cap["total_mw"]
    gcr_effective = cap["gcr_effective"]
    ac_mw = dc_mw / DC_AC_RATIO
    n_tx = max(1, math.ceil(ac_mw / MW_PER_TRANSFORMER))

    # 6. Panel blokları + iç yollar
    blocks_utm, roads_utm = _make_blocks(setback_utm)
    n_blocks = len(blocks_utm)

    for i, blk in enumerate(blocks_utm):
        features.append({
            "type": "Feature",
            "geometry": mapping(unproject(blk)),
            "properties": {"layer": "panel_block", "idx": i},
        })

    for road in roads_utm:
        features.append({
            "type": "Feature",
            "geometry": mapping(unproject(road)),
            "properties": {"layer": "internal_road"},
        })

    # 7. Trafo padleri
    tx_pts_utm = _place_transformers(blocks_utm, n_tx)
    for pt in tx_pts_utm:
        features.append({
            "type": "Feature",
            "geometry": mapping(unproject(pt)),
            "properties": {"layer": "transformer_pad"},
        })

    # 8. OSM bağlam + güzergâhlar
    (
        osm_feats, plant_sub_wgs84,
        interconnect_km, interconnect_capex_usd,
        target_kv, synthetic_grid,
    ) = _osm_context(lat_c, lon_c, setback_utm, to_utm, from_utm, country_code)

    features.extend(osm_feats)

    if plant_sub_wgs84 is not None:
        features.append({
            "type": "Feature",
            "geometry": mapping(plant_sub_wgs84),
            "properties": {"layer": "plant_substation"},
        })

    summary = {
        "dc_mw": round(dc_mw, 3),
        "ac_mw": round(ac_mw, 3),
        "n_blocks": n_blocks,
        "n_transformers": n_tx,
        "buildable_ha": round(buildable_ha, 2),
        "gcr_effective": round(gcr_effective, 3),
        "interconnect_km": round(interconnect_km, 2),
        "interconnect_capex_usd": float(round(interconnect_capex_usd, 0)),
        "target_substation_kv": target_kv,
        "slope_assumed": True,
        "synthetic_grid": synthetic_grid,
    }

    return {
        "summary": summary,
        "geojson": {"type": "FeatureCollection", "features": features},
    }


def generate(
    tiff_path: str,
    map_id: str,
    data_dir: str,
    country_code: str = "DEFAULT",
    panel_tech: str = "mono",
    tracking: str = "fixed",
) -> dict:
    """
    Lazy disk-cached layout üret.
    İlk çağrıda hesaplar, {map_id}_layout.geojson + {map_id}_layout.json yazar.
    Sonraki çağrılar diskten okur.
    """
    base = Path(data_dir)
    geojson_path = base / f"{map_id}_layout.geojson"
    summary_path = base / f"{map_id}_layout.json"

    if geojson_path.exists() and summary_path.exists():
        return {
            "summary": json.loads(summary_path.read_text(encoding="utf-8")),
            "geojson": json.loads(geojson_path.read_text(encoding="utf-8")),
        }

    result = _compute(tiff_path, country_code, panel_tech, tracking)

    base.mkdir(parents=True, exist_ok=True)
    geojson_path.write_text(json.dumps(result["geojson"]), encoding="utf-8")
    summary_path.write_text(json.dumps(result["summary"]), encoding="utf-8")

    return result
