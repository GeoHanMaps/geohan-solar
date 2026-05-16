// ── Map state ──────────────────────────────────────────────────────
let map;
let currentPolygon = null, drawMode = false;
let vertices = [], vertexMarkers = [];
let currentMapId = null, pollInterval = null;
let currentAnalysisId = null, analysisPoll = null;
let currentBasemap = 'satellite';
let layoutVisible = false;
// "Tahmini kapasite" = centroid mw_per_ha × heatmap alanı (ha). İki kaynak
// (heatmap stats + centroid analizi) farklı zamanlarda gelir; ikisi de
// gelince hesaplanır.
let lastAreaKm2 = null, lastMwPerHa = null;

const BASEMAPS = {
  satellite: {
    version: 8,
    sources: {
      esri: {
        type: 'raster',
        tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
        tileSize: 256,
        attribution: '© Esri, Maxar, Earthstar Geographics'
      },
      labels: {
        type: 'raster',
        tiles: ['https://basemaps.cartocdn.com/dark_only_labels/{z}/{x}/{y}.png'],
        tileSize: 256,
      }
    },
    layers: [
      { id: 'esri', type: 'raster', source: 'esri' },
      { id: 'labels', type: 'raster', source: 'labels', paint: { 'raster-opacity': 0.75 } },
    ],
  },
  dark: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
  topo: {
    version: 8,
    sources: {
      osm: {
        type: 'raster',
        tiles: ['https://a.tile.opentopomap.org/{z}/{x}/{y}.png'],
        tileSize: 256,
        attribution: '© OpenTopoMap'
      },
    },
    layers: [{ id: 'osm', type: 'raster', source: 'osm' }],
  },
};

function initMap() {
  map = new maplibregl.Map({
    container: 'map',
    style: BASEMAPS.satellite,
    center: [35.0, 39.0],
    zoom: 5,
    transformRequest: (url, resourceType) => {
      if (resourceType === 'Tile' && url.includes('/api/v1/maps/')) {
        return { url, headers: { 'Authorization': `Bearer ${authToken}` } };
      }
    },
  });
  map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-right');
  map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left');
  map.on('click', onMapClick);
  map.on('dblclick', onMapDblClick);
  map.on('contextmenu', onMapDblClick);
}

function changeBasemap(name) {
  currentBasemap = name;
  // Re-create map fully — simpler than swapping styles with overlays
  const center = map.getCenter();
  const zoom = map.getZoom();
  const hadHeatmap = currentMapId && map.getLayer('heatmap-layer');
  map.remove();
  map = new maplibregl.Map({
    container: 'map',
    style: BASEMAPS[name],
    center: [center.lng, center.lat],
    zoom,
    transformRequest: (url, resourceType) => {
      if (resourceType === 'Tile' && url.includes('/api/v1/maps/')) {
        return { url, headers: { 'Authorization': `Bearer ${authToken}` } };
      }
    },
  });
  map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-right');
  map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left');
  map.on('click', onMapClick);
  map.on('dblclick', onMapDblClick);
  map.on('contextmenu', onMapDblClick);
  map.on('load', () => {
    if (currentPolygon) drawPolygonOnMap(currentPolygon);
    if (hadHeatmap) loadHeatmap(currentMapId);
  });
}

// ── Polygon draw ───────────────────────────────────────────────────
function toggleDraw() {
  drawMode = !drawMode;
  const btn = document.getElementById('draw-btn');
  const hint = document.getElementById('draw-hint');
  if (drawMode) {
    clearPolygon();
    btn.classList.add('active');
    btn.textContent = '✏ Çizim Modu Aktif';
    hint.style.display = 'block';
    map.getCanvas().style.cursor = 'crosshair';
    // Çift tık polygon'u kapatır — harita zoom yapmasın
    map.doubleClickZoom.disable();
  } else {
    btn.classList.remove('active');
    btn.textContent = '✏ Polygon Çiz';
    hint.style.display = 'none';
    map.getCanvas().style.cursor = '';
    map.doubleClickZoom.enable();
  }
}

function onMapClick(e) {
  if (!drawMode) return;
  e.preventDefault();
  const { lng, lat } = e.lngLat;
  vertices.push([lng, lat]);
  const el = document.createElement('div');
  el.className = 'vertex-marker';
  const m = new maplibregl.Marker({ element: el }).setLngLat([lng, lat]).addTo(map);
  vertexMarkers.push(m);
  updateVertexCount();
  if (vertices.length >= 3) drawPreview();
}

function onMapDblClick(e) {
  if (!drawMode || vertices.length < 3) return;
  e.preventDefault();
  finishPolygon();
}

function finishPolygon() {
  if (vertices.length < 3) return;
  const ring = [...vertices, vertices[0]];
  currentPolygon = { type: 'Polygon', coordinates: [ring] };
  drawMode = false;
  const btn = document.getElementById('draw-btn');
  btn.classList.remove('active');
  btn.textContent = '✏ Polygon Çiz';
  document.getElementById('draw-hint').style.display = 'none';
  map.getCanvas().style.cursor = '';
  map.doubleClickZoom.enable();
  drawPreview(true);
  document.getElementById('clear-btn').style.display = 'block';
  document.getElementById('analyse-btn').disabled = false;
}

function drawPreview(final = false) {
  if (map.getLayer('polygon-fill'))    map.removeLayer('polygon-fill');
  if (map.getLayer('polygon-outline')) map.removeLayer('polygon-outline');
  if (map.getSource('polygon'))        map.removeSource('polygon');
  if (vertices.length < 3) return;
  const ring = [...vertices, vertices[0]];
  drawPolygonOnMap({ type: 'Polygon', coordinates: [ring] }, final);
}

function drawPolygonOnMap(geom, final = true) {
  if (map.getLayer('polygon-fill'))    map.removeLayer('polygon-fill');
  if (map.getLayer('polygon-outline')) map.removeLayer('polygon-outline');
  if (map.getSource('polygon'))        map.removeSource('polygon');
  map.addSource('polygon', { type: 'geojson', data: { type: 'Feature', geometry: geom } });
  map.addLayer({
    id: 'polygon-fill', type: 'fill', source: 'polygon',
    paint: { 'fill-color': '#14a085', 'fill-opacity': final ? 0.15 : 0.08 },
  });
  map.addLayer({
    id: 'polygon-outline', type: 'line', source: 'polygon',
    paint: { 'line-color': '#1ec6a3', 'line-width': 2, 'line-dasharray': final ? [1] : [3, 2] },
  });
}

function updateVertexCount() {
  const el = document.getElementById('vertex-count');
  if (vertices.length > 0) { el.style.display = 'block'; el.textContent = `${vertices.length} köşe`; }
}

function clearPolygon() {
  vertices = [];
  vertexMarkers.forEach(m => m.remove());
  vertexMarkers = [];
  currentPolygon = null;
  if (map.getLayer('polygon-fill'))    map.removeLayer('polygon-fill');
  if (map.getLayer('polygon-outline')) map.removeLayer('polygon-outline');
  if (map.getSource('polygon'))        map.removeSource('polygon');
  removeHeatmap();
  removeLayoutLayers();
  document.getElementById('clear-btn').style.display = 'none';
  document.getElementById('vertex-count').style.display = 'none';
  document.getElementById('analyse-btn').disabled = true;
  document.getElementById('legend').style.display = 'none';
  document.getElementById('layout-btn').style.display = 'none';
  resetRightPanel();
  setStatus('');
}

function resetRightPanel() {
  document.getElementById('right-empty').style.display = 'flex';
  document.getElementById('right-content').style.display = 'none';
  document.getElementById('pdf-btn').disabled = true;
  document.getElementById('layout-section').style.display = 'none';
  lastAreaKm2 = null;
  lastMwPerHa = null;
}

// ── Boundary search ────────────────────────────────────────────────
async function searchBoundary() {
  const q = document.getElementById('boundary-q').value.trim();
  if (!q) return;
  const list = document.getElementById('boundary-list');
  list.innerHTML = '<div style="color:var(--text-dim);font-size:12px;padding:6px">Aranıyor…</div>';
  try {
    const r = await apiFetch(`/api/v1/boundaries/search?q=${encodeURIComponent(q)}`);
    if (!r.ok) { list.innerHTML = '<div style="color:var(--danger);font-size:12px;padding:6px">Bulunamadı.</div>'; return; }
    const results = await r.json();
    if (!results.length) { list.innerHTML = '<div style="color:var(--text-faint);font-size:12px;padding:6px">Sonuç yok.</div>'; return; }
    list.innerHTML = '';
    results.forEach(b => {
      const div = document.createElement('div');
      div.className = 'boundary-item';
      div.innerHTML = `<div class="b-name">${b.name.split(',')[0]}</div><div class="b-area">~${b.area_km2.toLocaleString()} km²</div>`;
      div.onclick = () => selectBoundary(b);
      list.appendChild(div);
    });
  } catch {
    list.innerHTML = '<div style="color:var(--danger);font-size:12px;padding:6px">Bağlantı hatası.</div>';
  }
}

function selectBoundary(b) {
  clearPolygon();
  currentPolygon = b.geojson;
  drawPolygonOnMap(b.geojson, true);
  const [w, s, e, n] = b.bounds;
  map.fitBounds([[w, s], [e, n]], { padding: 60 });
  document.getElementById('clear-btn').style.display = 'block';
  document.getElementById('analyse-btn').disabled = false;
  document.getElementById('boundary-list').innerHTML = '';
}

// ── Analysis ───────────────────────────────────────────────────────
async function startAnalysis() {
  if (!currentPolygon) return;
  if (!isAuthed()) { openAuthModal('login'); return; }

  if (pollInterval)   { clearInterval(pollInterval); pollInterval = null; }
  if (analysisPoll)   { clearInterval(analysisPoll); analysisPoll = null; }

  removeHeatmap();
  resetRightPanel();
  setStatus('pending', '⏳ Analiz kuyruğa alındı…');
  document.getElementById('analyse-btn').disabled = true;

  const params = collectParams();

  // Kick off heatmap job
  const mapBody = {
    geom: currentPolygon,
    resolution_m: params.resolution,
    panel_tech:   params.panel_tech,
    tracking:     params.tracking,
    country_code: params.country_code,
    name:         document.getElementById('boundary-q').value || 'Manuel Çizim',
  };

  try {
    const r = await apiFetch('/api/v1/maps', { method: 'POST', body: JSON.stringify(mapBody) });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      if (r.status === 402) {
        setStatus('failed', '✕ Yetersiz kredi. Hesabını yükle.');
      } else {
        setStatus('failed', '✕ Hata: ' + (err.detail || r.statusText));
      }
      document.getElementById('analyse-btn').disabled = false;
      return;
    }
    const job = await r.json();
    currentMapId = job.id;
    setStatus('running', '⚙ Heatmap raster üretiliyor…');
    pollInterval = setInterval(pollMapJob, 3000);

    // Balance refresh (cost charged)
    updateAuthUi();
  } catch {
    setStatus('failed', '✕ Sunucuya bağlanılamadı.');
    document.getElementById('analyse-btn').disabled = false;
    return;
  }

  // In parallel: centroid point analysis for right-panel breakdown
  startCentroidAnalysis(params);
}

function collectParams() {
  return {
    resolution: parseInt(document.getElementById('resolution').value),
    panel_tech: document.getElementById('panel-tech').value,
    tracking:   document.getElementById('tracking').value,
    country_code: document.getElementById('country-code').value.toUpperCase() || 'DEFAULT',
  };
}

async function startCentroidAnalysis(params) {
  const c = polygonCentroid(currentPolygon);
  if (!c) return;
  const body = {
    lat: c.lat, lon: c.lon,
    area_ha: polygonAreaHa(currentPolygon),
    panel_tech: params.panel_tech,
    tracking: params.tracking,
    country_code: params.country_code,
  };
  try {
    const r = await apiFetch('/api/v1/analyses', { method: 'POST', body: JSON.stringify(body) });
    if (!r.ok) return; // optional, fail silently
    const job = await r.json();
    currentAnalysisId = job.id;
    analysisPoll = setInterval(pollAnalysisJob, 3000);
  } catch { /* optional */ }
}

function polygonAreaHa(geom) {
  if (!geom || !geom.coordinates) return 100;
  const ring = geom.coordinates[0];
  if (!ring || ring.length < 4) return 100;
  const n = ring.length - 1;
  // Ortalama enlem ile cos(lat) düzeltmesi — shoelace km²
  const latRad = ring.slice(0, n).reduce((s, p) => s + p[1], 0) / n * Math.PI / 180;
  const kx = Math.cos(latRad) * 111.32;  // derece lon → km
  const ky = 110.54;                      // derece lat → km
  let area = 0;
  for (let i = 0; i < n; i++) {
    const j = (i + 1) % n;
    area += (ring[i][0] * kx) * (ring[j][1] * ky) - (ring[j][0] * kx) * (ring[i][1] * ky);
  }
  const km2 = Math.abs(area) / 2;
  return Math.min(Math.max(km2 * 100, 1), 50000);  // ha, clamp [1, 50000]
}

function polygonCentroid(geom) {
  if (!geom || !geom.coordinates) return null;
  const ring = geom.coordinates[0];
  if (!ring || ring.length < 3) return null;
  let sx = 0, sy = 0, n = 0;
  for (let i = 0; i < ring.length - 1; i++) {
    sx += ring[i][0]; sy += ring[i][1]; n++;
  }
  return { lon: sx / n, lat: sy / n };
}

async function pollMapJob() {
  if (!currentMapId) return;
  try {
    const r = await apiFetch(`/api/v1/maps/${currentMapId}`);
    const job = await r.json();
    if (job.status === 'running') setStatus('running', '⚙ Heatmap raster üretiliyor…');
    if (job.status === 'pending') setStatus('pending', '⏳ Kuyrukta bekliyor…');
    if (job.status === 'done') {
      clearInterval(pollInterval); pollInterval = null;
      setStatus('done', '✔ Harita hazır');
      showStats(job.stats);
      loadHeatmap(currentMapId);
      document.getElementById('analyse-btn').disabled = false;
      document.getElementById('legend').style.display = 'block';
      document.getElementById('layout-btn').style.display = 'block';
    }
    if (job.status === 'failed') {
      clearInterval(pollInterval); pollInterval = null;
      setStatus('failed', '✕ Hata: ' + (job.error || 'bilinmiyor'));
      document.getElementById('analyse-btn').disabled = false;
    }
  } catch { /* transient */ }
}

async function pollAnalysisJob() {
  if (!currentAnalysisId) return;
  try {
    const r = await apiFetch(`/api/v1/analyses/${currentAnalysisId}`);
    const job = await r.json();
    if (job.status === 'done') {
      clearInterval(analysisPoll); analysisPoll = null;
      renderBreakdown(job.result || job);
      document.getElementById('pdf-btn').disabled = false;
    }
    if (job.status === 'failed') {
      clearInterval(analysisPoll); analysisPoll = null;
    }
  } catch { /* transient */ }
}

// ── Heatmap layer ──────────────────────────────────────────────────
function loadHeatmap(mapId) {
  removeHeatmap();
  const tileUrl = `/api/v1/maps/${mapId}/tiles/{z}/{x}/{y}.png`;
  map.addSource('heatmap', { type: 'raster', tiles: [tileUrl], tileSize: 256, minzoom: 5, maxzoom: 16 });
  const before = map.getLayer('polygon-fill') ? 'polygon-fill' : undefined;
  map.addLayer({ id: 'heatmap-layer', type: 'raster', source: 'heatmap', paint: { 'raster-opacity': 0.70 } }, before);
  loadConstraints(mapId);
}

function removeHeatmap() {
  for (const id of ['constraint-labels','constraint-icons','constraint-count','constraint-clusters'])
    if (map.getLayer(id)) map.removeLayer(id);
  if (map.getSource('constraints'))  map.removeSource('constraints');
  if (map.getLayer('heatmap-layer')) map.removeLayer('heatmap-layer');
  if (map.getSource('heatmap'))      map.removeSource('heatmap');
  removeLayoutLayers();
}

async function loadConstraints(mapId) {
  try {
    const r = await apiFetch(`/api/v1/maps/${mapId}/constraints`);
    if (!r.ok) return;
    const geojson = await r.json();
    if (!geojson.features || !geojson.features.length) return;

    map.addSource('constraints', { type: 'geojson', data: geojson, cluster: true, clusterMaxZoom: 13, clusterRadius: 48 });
    map.addLayer({
      id: 'constraint-clusters', type: 'circle', source: 'constraints',
      filter: ['has', 'point_count'],
      paint: {
        'circle-color': '#e53935',
        'circle-radius': ['step', ['get', 'point_count'], 14, 20, 18, 100, 22],
        'circle-stroke-width': 2, 'circle-stroke-color': '#fff', 'circle-opacity': 0.85,
      },
    });
    map.addLayer({
      id: 'constraint-count', type: 'symbol', source: 'constraints',
      filter: ['has', 'point_count'],
      layout: { 'text-field': ['get', 'point_count_abbreviated'], 'text-size': 12, 'text-allow-overlap': true, 'text-ignore-placement': true },
      paint: { 'text-color': '#fff' },
    });
    map.addLayer({
      id: 'constraint-icons', type: 'circle', source: 'constraints',
      filter: ['!', ['has', 'point_count']],
      paint: {
        'circle-color': ['case', ['==', ['get', 'block_type'], 'hard'], '#e53935', '#f57c00'],
        'circle-radius': 9, 'circle-stroke-width': 2, 'circle-stroke-color': '#fff', 'circle-opacity': 0.9,
      },
    });
    map.addLayer({
      id: 'constraint-labels', type: 'symbol', source: 'constraints',
      filter: ['!', ['has', 'point_count']],
      layout: { 'text-field': '!', 'text-size': 14, 'text-allow-overlap': true, 'text-ignore-placement': true },
      paint: { 'text-color': '#fff' },
    });

    map.on('click', 'constraint-clusters', (e) => {
      const src = map.getSource('constraints');
      const cid = e.features[0].properties.cluster_id;
      src.getClusterExpansionZoom(cid, (err, zoom) => {
        if (!err) map.easeTo({ center: e.features[0].geometry.coordinates, zoom });
      });
    });
    map.on('click', 'constraint-icons', (e) => {
      const p = e.features[0].properties;
      const isHard = p.block_type === 'hard';
      const color = isHard ? '#e53935' : '#f57c00';
      const label = isHard ? '🚫 YASAKLI ALAN' : '⚠ İZNE TABİ ALAN';
      new maplibregl.Popup({ maxWidth: '300px' })
        .setLngLat(e.lngLat)
        .setHTML(`<div style="font-family:system-ui;font-size:13px;line-height:1.7">
          <b style="color:${color};font-size:14px;display:block;margin-bottom:4px">${label}</b>
          ${p.reason || 'Kısıtlı alan'}
        </div>`)
        .addTo(map);
    });
    for (const id of ['constraint-clusters','constraint-icons','constraint-labels']) {
      map.on('mouseenter', id, () => { map.getCanvas().style.cursor = 'pointer'; });
      map.on('mouseleave', id, () => { map.getCanvas().style.cursor = ''; });
    }
  } catch(err) { console.warn('Constraint layer error:', err); }
}

function updateOpacity(v) {
  document.getElementById('opacity-val').textContent = v + '%';
  if (map.getLayer('heatmap-layer'))
    map.setPaintProperty('heatmap-layer', 'raster-opacity', v / 100);
}

// ── Right panel render ─────────────────────────────────────────────
function showStats(stats) {
  if (!stats) return;
  document.getElementById('right-empty').style.display = 'none';
  document.getElementById('right-content').style.display = 'block';
  document.getElementById('score-mean-big').textContent = stats.score_mean.toFixed(1);
  document.getElementById('score-min').textContent      = stats.score_min.toFixed(1);
  document.getElementById('score-max').textContent      = stats.score_max.toFixed(1);

  if (stats.area_km2) {
    document.getElementById('kv-area').textContent = stats.area_km2.toFixed(1) + ' km²';
    lastAreaKm2 = stats.area_km2;
    refreshCapacityEstimate();
  }
  if (stats.pixel_count) {
    document.getElementById('kv-points').textContent = stats.pixel_count.toLocaleString();
  }
}

// Kapasite = mw_per_ha (centroid analizi) × alan (heatmap stats, ha).
// Skorla ölçeklenmez — skor bir derate faktörü değil.
function refreshCapacityEstimate() {
  if (lastAreaKm2 == null || lastMwPerHa == null) return;
  const capMW = lastMwPerHa * lastAreaKm2 * 100;  // 1 km² = 100 ha
  document.getElementById('kv-capacity').textContent =
    '~' + capMW.toLocaleString(undefined, { maximumFractionDigits: 0 }) + ' MW';
}

const CRITERIA_LABELS = {
  ghi: 'GHI', solar: 'GHI',
  sebeke: 'Grid', grid: 'Grid',
  egim: 'Slope', slope: 'Slope',
  yasal: 'Legal', legal: 'Legal',
  baki: 'Aspect', aspect: 'Aspect',
  golge: 'Shade', shade: 'Shade',
  arazi: 'Land', land_cover: 'Land',
  erisim: 'Access', access: 'Access',
};

function renderBreakdown(result) {
  if (!result) return;
  // API şeması: AnalysisResult.breakdown = { egim:{value,unit,score,weight}, ... }
  const breakdown = result.breakdown || {};
  const container = document.getElementById('criteria-bars');
  container.innerHTML = '';

  Object.entries(breakdown).forEach(([key, val]) => {
    if (!val || typeof val.score !== 'number') return;
    const label = CRITERIA_LABELS[key.toLowerCase()] || key;
    const pct = Math.max(0, Math.min(100, val.score));
    const cls = pct < 40 ? 'low' : pct < 65 ? 'mid' : '';
    const el = document.createElement('div');
    el.className = 'criterion';
    el.innerHTML = `
      <span class="c-name">${label}</span>
      <div class="c-bar"><div class="c-fill ${cls}" style="width:${pct}%"></div></div>
      <span class="c-val">${pct.toFixed(0)}</span>
    `;
    container.appendChild(el);
  });

  // Capacity / financial — şemada capacity.mw_per_ha (top-level değil)
  const mwha = result.capacity && result.capacity.mw_per_ha;
  if (typeof mwha === 'number') {
    document.getElementById('fin-mwha').textContent = mwha.toFixed(3);
    lastMwPerHa = mwha;
    refreshCapacityEstimate();
  }
  const fin = result.financial || {};
  if (fin.total_investment_usd) document.getElementById('fin-capex').textContent = '$' + (fin.total_investment_usd/1e6).toFixed(1) + 'M';
  if (result.irr_estimate !== undefined) document.getElementById('fin-irr').textContent = result.irr_estimate.toFixed(1) + '%';
  if (fin.payback_years !== undefined) {
    const p = fin.payback_years;
    document.getElementById('fin-payback').textContent = p >= 999 ? '—' : p.toFixed(1) + ' yr';
  }

  if (result.narrative) {
    document.getElementById('narrative-section').style.display = 'block';
    document.getElementById('narrative-text').textContent = result.narrative;
  }
}

async function downloadPdf() {
  if (!currentAnalysisId) return;
  try {
    const r = await apiFetch(`/api/v1/analyses/${currentAnalysisId}/report`);
    if (!r.ok) return;
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `GeoHan_${currentAnalysisId}.pdf`;
    a.click(); URL.revokeObjectURL(url);
  } catch {}
}

// ── Layout katmanı ─────────────────────────────────────────────────
const _LYT_LAYERS = [
  'lyt-buildable', 'lyt-setback', 'lyt-blocks-fill', 'lyt-blocks-line',
  'lyt-roads', 'lyt-tx', 'lyt-poc', 'lyt-interconnect', 'lyt-access',
  'lyt-osm-lines', 'lyt-osm-subs',
];
const _LYT_SOURCES = ['lyt-src'];

function removeLayoutLayers() {
  for (const id of _LYT_LAYERS) if (map.getLayer(id)) map.removeLayer(id);
  for (const id of _LYT_SOURCES) if (map.getSource(id)) map.removeSource(id);
  layoutVisible = false;
  document.getElementById('layout-btn').classList.remove('active');
  document.getElementById('layout-legend').style.display = 'none';
  document.getElementById('layout-section').style.display = 'none';
}

async function toggleLayout() {
  if (!currentMapId) return;
  if (layoutVisible) { removeLayoutLayers(); return; }
  const btn = document.getElementById('layout-btn');
  btn.disabled = true;
  btn.textContent = '⚡ Yükleniyor…';
  try {
    const r = await apiFetch(`/api/v1/maps/${currentMapId}/layout`);
    if (!r.ok) { btn.textContent = '⚡ Santral Simülasyonu'; btn.disabled = false; return; }
    const data = await r.json();
    addLayoutLayers(data.geojson, data.summary);
    layoutVisible = true;
    btn.classList.add('active');
    btn.textContent = '⚡ Simülasyonu Gizle';
    document.getElementById('layout-legend').style.display = 'block';
  } catch { /* silently fail */ }
  btn.disabled = false;
}

function addLayoutLayers(geojson, summary) {
  removeLayoutLayers();
  map.addSource('lyt-src', { type: 'geojson', data: geojson });

  const layer = (id, type, filter, paint, layout) => {
    const spec = { id, type, source: 'lyt-src', filter, paint };
    if (layout) spec.layout = layout;
    map.addLayer(spec);
  };

  layer('lyt-buildable', 'fill',
    ['==', ['get', 'layer'], 'buildable_area'],
    { 'fill-color': '#14a085', 'fill-opacity': 0.06 });

  layer('lyt-setback', 'line',
    ['==', ['get', 'layer'], 'setback'],
    { 'line-color': '#7a8a99', 'line-width': 1, 'line-dasharray': [1, 2], 'line-opacity': 0.5 });

  layer('lyt-blocks-fill', 'fill',
    ['==', ['get', 'layer'], 'panel_block'],
    { 'fill-color': '#1b3a5b', 'fill-opacity': 0.55 });

  layer('lyt-blocks-line', 'line',
    ['==', ['get', 'layer'], 'panel_block'],
    { 'line-color': '#5b8fc7', 'line-width': 0.5 });

  layer('lyt-roads', 'line',
    ['==', ['get', 'layer'], 'internal_road'],
    { 'line-color': '#d9c089', 'line-width': 1, 'line-dasharray': [3, 2] });

  layer('lyt-tx', 'circle',
    ['==', ['get', 'layer'], 'transformer_pad'],
    { 'circle-radius': 6, 'circle-color': '#f0a13a', 'circle-stroke-width': 1.5, 'circle-stroke-color': '#fff' });

  layer('lyt-poc', 'circle',
    ['==', ['get', 'layer'], 'plant_substation'],
    { 'circle-radius': 8, 'circle-color': '#e8c14f', 'circle-stroke-width': 2, 'circle-stroke-color': '#222' });

  layer('lyt-interconnect', 'line',
    ['==', ['get', 'layer'], 'interconnect_route'],
    { 'line-color': '#e8c14f', 'line-width': 2.5, 'line-dasharray': [2, 1] });

  layer('lyt-access', 'line',
    ['==', ['get', 'layer'], 'access_route'],
    { 'line-color': '#cfcfcf', 'line-width': 1.5, 'line-dasharray': [2, 2] });

  // OSM iletim hatları — voltaja göre renk
  layer('lyt-osm-lines', 'line',
    ['==', ['get', 'layer'], 'osm_line'],
    {
      'line-color': [
        'step', ['coalesce', ['get', 'kv'], 0],
        '#999', 66, '#e90', 220, '#d33',
      ],
      'line-width': 1.5,
    });

  layer('lyt-osm-subs', 'circle',
    ['==', ['get', 'layer'], 'osm_substation'],
    { 'circle-radius': 5, 'circle-color': '#d33', 'circle-stroke-width': 1, 'circle-stroke-color': '#fff' });

  // Sağ panel güncelle
  if (summary) {
    document.getElementById('layout-section').style.display = 'block';
    document.getElementById('lyt-dc-mw').textContent  = summary.dc_mw.toFixed(1) + ' MW';
    document.getElementById('lyt-ac-mw').textContent  = summary.ac_mw.toFixed(1) + ' MW';
    document.getElementById('lyt-n-tx').textContent   = summary.n_transformers;
    document.getElementById('lyt-km').textContent     = summary.interconnect_km.toFixed(1) + ' km';
  }
}

// ── Status ─────────────────────────────────────────────────────────
function setStatus(type, msg) {
  const el = document.getElementById('status-box');
  if (!msg) { el.style.display = 'none'; return; }
  el.className = type;
  el.textContent = msg;
  el.style.display = 'block';
}

async function loadHistory() {
  // placeholder — full impl later
  alert('Geçmiş sayfası yakında.');
}

// ── Override auth handler to gate workspace ────────────────────────
function onAuthChanged() {
  updateAuthUi();
  // If not authed when landing on /ui/solar, force modal
  if (!isAuthed()) {
    openAuthModal('login');
  }
}

// ── Init ───────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initMap();
  document.getElementById('boundary-q').addEventListener('keydown', e => {
    if (e.key === 'Enter') searchBoundary();
  });
  // Re-check auth state after shared.js inits
  setTimeout(() => {
    if (!isAuthed()) openAuthModal('login');
  }, 100);
});
