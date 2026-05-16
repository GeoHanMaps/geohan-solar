# Faz 2 — GES Simülasyon Katmanı: Uygulama Spec'i

> **Bu doküman Sonnet'e devredilebilir.** Tüm tasarım kararları kilitli;
> uygulayıcının karar vermesi gereken hiçbir açık nokta bırakılmadı.
> Belirsizlik çıkarsa: "en düşük blast-radius" + "skor/kapasite sayısını
> değiştirme" kuralına göre davran ve TODO bırak, kendi başına genişletme.

## 0. Amaç ve sınır

Seçilen polygon için **deterministik mühendislik-şeması** GES yerleşimi
üret: panel blokları, iç servis yolları, trafo padleri, şalt sahası (POC),
civar gerçek OSM iletim hatları + trafo merkezleri, ve şalttan en yakın
uygun HV trafoya **düz "önerilen bağlantı" polyline'ı** + erişim yolu.

**Mutlak kural — DOKUNULMAYACAK dosyalar:** `app/services/heatmap.py`,
`app/services/mcda.py`, `app/services/capacity.py`, `app/tasks.py`
(`map_task` dahil), skorlama/migration/Celery hiçbir şey. Faz 2 yalnızca
**yeni dosya ekler** + `maps.py`'ye 1 endpoint + `schemas.py`'ye 2 model +
`retention.py`'ye 1 suffix + frontend ekleri + 1 test dosyası.

Kullanıcı kararları (kilitli): mühendislik şeması (foto-gerçekçi değil);
gerçek OSM bağlam + **düz** önerilen güzergâh (yola snap YOK — o ayrı bir
gelecek seçeneği).

## 1. Ne zaman hesaplanır

**Lazy, ilk GET'te.** `map_task` değiştirilmez. `GET /maps/{id}/layout`
ilk çağrıda hesaplar, `{settings.maps_data_dir}/{map_id}_layout.geojson`
ve `{map_id}_layout.json` (summary) olarak diske yazar; sonraki çağrılar
diskten okur. Gerekçe: map_task deploy edilmiş/çalışıyor (blast-radius),
layout ucuz (vektör + 1 OSM çağrısı), simülasyon bir toggle — herkes
istemez. Migration/Celery/credit değişikliği YOK (heatmap'in 5 kredisine
dahil — yol haritası kararı).

## 2. Buildable mask (tek tanım noktası)

`app/services/layout.py` içinde **tek** fonksiyon: `_buildable_mask(src)`.
v1 tanımı:

```
geçerli  = (data != -9999.0) & (data != -1.0) & (data >= BUILDABLE_SCORE_MIN)
BUILDABLE_SCORE_MIN = 35.0   # modül sabiti, dokümante edilmiş v1 proxy
```

`-1` = LC hard-block (yasak), `-9999` = polygon dışı. Skor eşiği çok dik
eğimi (eğim skoru 0 → toplam düşer) ve çok kötü araziyi kabaca eler.

**Bilinen v1 basitleştirmesi + TODO:** Raster'da ayrı eğim/buildable band
yok; eşik bir proxy. Faz 1-E adanmış bir buildable band/fraction getirirse
**sadece bu fonksiyonun içi** ona göre değiştirilecek — çağıran kod
değişmez. Spec'e `# TODO(Faz1-E): replace score-threshold with dedicated
buildable band` yorumu bırak.

## 3. Geometri (hepsi UTM'de)

`grid.py`'deki `_utm_transformer` desenini birebir kullan (kopyala, import
etme — bağımlılık yaratma). Adımlar:

1. Buildable boolean → `rasterio.features.shapes` ile WGS84 polygon(lar).
2. WGS84 → UTM (alan merkezinin zone'u).
3. **Setback:** `polygon.buffer(-SETBACK_M)`, `SETBACK_M = 15.0`. Boşalan
   parçalar düşer (çit/çevre yolu görevi de görür).
4. **Blok ızgarası:** eksen-hizalı (N/E) düzenli grid.
   - `BLOCK_W = 200.0` m (D–B), `BLOCK_H = 120.0` m (K–G)
   - `INTERNAL_ROAD_M = 8.0` m (bloklar arası koridor = iç yol)
   - Grid origin = buildable bbox min köşesi; adım =
     `(BLOCK_W+INTERNAL_ROAD_M, BLOCK_H+INTERNAL_ROAD_M)`.
   - Her hücre için blok dikdörtgenini setback'li polygonla `intersection`;
     sonuç boş değilse ve alanı `> 0.15*BLOCK_W*BLOCK_H` ise tut.
   - **İç yollar:** komşu blok satır/sütunları arası orta-çizgi
     LineString'leri (yalnız iki yanında tutulan blok varsa).
5. **Panel temsili ≈ kapasite ile tutarlı olmalı.** MW tek kaynaktan:
   `capacity.calculate(slope_mean_pct, ghi=1600.0, area_ha=buildable_ha,
   panel_tech, tracking, gcr_override=None)` çağır — `slope_mean_pct`
   raster geçerli piksellerinin ortalamasından *kabaca* türetilemiyorsa
   (raster'da eğim yok) **`slope_mean_pct = 0.0` kullan** ve summary'de
   `slope_assumed: true` bayrağı koy. `ghi` kapasiteyi değil yalnız
   `annual_gwh`'i etkiler; layout summary `annual_gwh` göstermez, o yüzden
   1600 placeholder güvenli (dokümante et). `dc_mw = cap["total_mw"]`,
   `gcr_effective = cap["gcr_effective"]`. Bloklar **görsel temsil**;
   sayı capacity.py'den gelir (yatırımcı layout ile MW'ı yan yana görür,
   tutarlı olmalı — bu yüzden tek kaynak).

## 4. Trafo padleri

```
DC_AC_RATIO = 1.2
MW_PER_TRANSFORMER = 5.0
ac_mw = dc_mw / DC_AC_RATIO
n_tx  = max(1, ceil(ac_mw / MW_PER_TRANSFORMER))
```

Yerleşim **deterministik** (sklearn/rastgelelik YOK): tutulan blokları
snake sırasına diz (row-major, tek satırlar ters), `n_tx` ardışık eşit
gruba böl, her grubun blok-centroid'lerinin ortalamasına bir pad noktası.

## 5. Şalt sahası (POC) + OSM bağlam + güzergâh

`access.py` / `grid.py` osmnx desenini kullan (kopyala). Polygon
centroid'inden:

- **OSM trafo merkezleri:** `ox.features_from_point((lat,lon),
  tags={"power":["substation"]}, dist=30000)` — **`tower` ASLA dahil
  edilmez** (Faz 1-A bug'ı tekrarlanmasın). Her biri `osm_substation`
  feature; `voltage` tag'i parse et (ilk sayı, kV; "/" veya ";" ayır,
  V→kV gerekiyorsa böl). Parse edilemezse `kv=None`.
- **OSM hatları:** `tags={"power":["line"]}, dist=30000`. Her biri
  `osm_line` feature; `voltage` aynı parse.
- **Hedef trafo:** `country_rules.json`'dan `min_grid_kv` (yoksa DEFAULT).
  `kv >= min_grid_kv` olan trafolardan UTM'de en yakını. Yoksa: voltajı
  bilinen herhangi en yakın trafo. Hiç trafo yoksa: `grid.py`
  `_reliability_to_km` ile sentetik mesafe, polygon centroid'inden en
  yakın OSM yola doğru bearing'de bir nokta — feature `properties.synthetic
  = true`.
- **Şalt (POC):** setback'li buildable polygon'un, hedef trafoya UTM'de
  en yakın **sınır vertex'i**. Tek `plant_substation` Point.
- **Önerilen bağlantı:** `plant_substation → hedef trafo` **düz** UTM
  LineString → WGS84. `interconnect_km = uzunluk`. `interconnect_capex_usd
  = interconnect_km * USD_PER_KM_LINE`, `USD_PER_KM_LINE = 250000.0`
  (modül sabiti, dokümante; financial.py'ye bağlanma — blast-radius).
- **Erişim yolu:** plant gate (buildable sınırının en yakın OSM
  drivable yola olan vertex'i) → en yakın OSM yol düğümü, **düz**
  LineString (`access.py` graph desenini kullan; node yoksa feature
  atlanır).

OSM çağrıları başarısız/boşsa: o feature grubu atlanır, layout yine
döner (graceful degrade — `access.py`'deki try/except deseni gibi).

## 6. Çıktı şeması

Tek `FeatureCollection`. Her feature `properties.layer` ∈:
`buildable_area | setback | panel_block | internal_road |
transformer_pad | plant_substation | interconnect_route |
access_route | osm_line | osm_substation`.
İlgili ek property'ler: `osm_line/osm_substation` → `kv` (float|null),
`name`; `interconnect_route` → `km`, `capex_usd`, `synthetic` (bool);
`panel_block` → `idx` (int).

Yan dosya `{map_id}_layout.json` = `LayoutSummary` (aşağıda).

### schemas.py'ye eklenecek (sadece bunlar)

```python
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

class LayoutResponse(BaseModel):
    summary: LayoutSummary
    geojson: dict
```

## 7. Endpoint (maps.py'ye eklenecek tek şey)

```
GET /api/v1/maps/{map_id}/layout  → LayoutResponse
```

- Auth: diğer GET map endpoint'leriyle **birebir aynı** —
  `jobs.load_authorized(session, job_id=map_id,
  token_payload=decode_token(token))`; None → 404.
- `job["status"] != "done"` → 425 (mevcut desen).
- `job["result"]["tiff_path"]` yoksa/dosya yoksa → 404.
- Disk cache varsa oku; yoksa `layout.generate(...)` → diske yaz → dön.
- Kredi YOK (heatmap 5 kredisine dahil).

## 8. retention.py

Purge suffix listesine `_layout.geojson` ve `_layout.json` ekle (tek
satır; mevcut `.tif`/`_constraints.geojson` deseninin yanına). Başka
değişiklik yok.

## 9. Frontend

`solar.html` — "Görünüm" panel-section'ına toggle:
`<button id="layout-btn" class="btn-secondary" onclick="toggleLayout()">⚡ Santral Simülasyonu</button>`
(yalnız heatmap "done" olunca aktif). "Uygunluk Skoru" lejantının altına
layout lejant bloğu.

`solar.js`:
- `toggleLayout()` → ilk açılışta `apiFetch('/api/v1/maps/{id}/layout')`,
  `addLayoutLayers(geojson)`, kapatınca katmanları kaldır (heatmap
  remove deseni gibi; tüm layout layer/source id'leri `lyt-` prefix).
- `addLayoutLayers` — `properties.layer`'a göre filtrelenmiş MapLibre
  paint (profesyonel, uydu altlık üstü):
  - `panel_block`: fill `#1b3a5b` opacity .55 + line `#5b8fc7` width .5
  - `internal_road`: line `#d9c089` width 1 dash [3,2]
  - `setback`: line `#7a8a99` width 1 dash [1,2] opacity .5
  - `transformer_pad`: circle r6 `#f0a13a` stroke #fff 1.5
  - `plant_substation`: circle r8 `#e8c14f` stroke #222 2 (kare hissi:
    `circle-pitch-alignment` default; symbol şart değil)
  - `interconnect_route`: line `#e8c14f` width 2.5 dash [2,1]
  - `access_route`: line `#cfcfcf` width 1.5 dash [2,2]
  - `osm_line`: line, renk `kv`'ye step (≥220→#d33, ≥66→#e90, else #999),
    width 1.5
  - `osm_substation`: circle r5 `#d33` stroke #fff 1
- Sağ panele yeni section "Santral (Simülasyon)": dc_mw, ac_mw,
  n_transformers, interconnect_km — `summary`'den.
- `clearPolygon`/`removeHeatmap` içinde layout da temizlensin
  (`lyt-` prefix toplu kaldırma helper'ı).

## 10. Test (tests/test_layout.py — yeni)

GEE/OSM **monkeypatch**'li (ağ yok). Küçük sentetik GeoTIFF fixture
(ör. 50×50, ortası 0..100 skor, bir köşe -1, kenarlar -9999):
- buildable polygon boş değil; setback uygulanmış (alan < ham buildable)
- her panel_block setback'li buildable içinde
- `n_transformers == ceil(ac_mw/5)`, `n_blocks > 0`
- interconnect_route 2 uçlu, `km > 0`
- OSM monkeypatch boş döndüğünde `osm_*` yok ama FC yine valid + summary
  `synthetic_grid` davranışı
- **determinizm:** aynı girdi → aynı FeatureCollection (json.dumps eşit)
- Kanonik test sayısı disiplinine dikkat (bellek: 439 passed) — yeni
  testler eklenir, mevcutlar değişmez.

## 11. Modül sabitleri (layout.py başında, tek yerde)

```python
BUILDABLE_SCORE_MIN  = 35.0
SETBACK_M            = 15.0
BLOCK_W              = 200.0
BLOCK_H              = 120.0
INTERNAL_ROAD_M      = 8.0
DC_AC_RATIO          = 1.2
MW_PER_TRANSFORMER   = 5.0
USD_PER_KM_LINE      = 250000.0
OSM_SEARCH_M         = 30000
```

## 12. Teslim kontrol listesi

- [ ] `app/services/layout.py` (yeni) — saf fonksiyon, ağ hatası graceful
- [ ] `app/routers/maps.py` — 1 endpoint, mevcut auth deseni
- [ ] `app/schemas.py` — `LayoutSummary`, `LayoutResponse`
- [ ] `app/services/retention.py` — 2 suffix
- [ ] `frontend/solar.html` + `solar.js` — toggle + katmanlar + sağ panel
- [ ] `tests/test_layout.py` — monkeypatch'li, deterministik
- [ ] `ruff check app/ tests/` temiz
- [ ] DOKUNULMAYAN dosyalar gerçekten değişmemiş (`git diff --stat`)
- [ ] Test sayısı: 439 + yeni (mevcut hiçbiri kırılmadı)
