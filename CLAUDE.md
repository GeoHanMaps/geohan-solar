# GeoHan Solar Intelligence

Global GES yatırım skoru SaaS platformu.

## Sunucu
- IP: 178.104.69.28
- User: geohan
- Path: /home/geohan/geohan/geohan-solar
- SSH: `ssh geohan@178.104.69.28`
- Root SSH da çalışır (deploy için)
- Auth: `admin` / `6383426Md-17`

## Stack
- FastAPI + Celery + Redis (Docker Compose)
- PostgreSQL/PostGIS yok (henüz), cache JSON dosya tabanlı
- Nginx port 80'de reverse proxy (host üzerinde, Docker dışı)
- API health: http://178.104.69.28/api/v1/health

## Komutlar
```bash
# Sunucuda container durumu
docker compose -f /home/geohan/geohan/geohan-solar/docker-compose.yml ps

# Rebuild + restart
cd /home/geohan/geohan/geohan-solar && docker compose up -d --build

# Log izle
docker compose logs -f api

# Token al (test için)
curl -s -X POST http://localhost:8000/api/v1/auth/token \
  -d 'username=admin&password=6383426Md-17' | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])'

# Analiz gönder
curl -s -X POST http://localhost:8000/api/v1/analyses \
  -H 'Content-Type: application/json' -H 'Authorization: Bearer <TOKEN>' \
  -d '{"lat":37.87,"lon":32.49,"country_code":"TR","area_ha":100}'
```

## GitHub
- Repo: https://github.com/GeoHanMaps/geohan-solar
- CD: main'e push → otomatik build + deploy (GitHub Actions)
- **Secrets eksik:** DEPLOY_HOST, DEPLOY_USER, DEPLOY_SSH_KEY — henüz eklenmedi, deploy manuel

## Mevcut Durum (2026-05-02)

### Çalışan Servisler
- API: http://178.104.69.28/api/v1/health → `{"status":"ok","gee":"ok","osm":"ok"}`
- Celery worker, Redis: çalışıyor
- GEE: `ok` (credentials kopyalandı)

### Tamamlanan İşler
- **MCDA skor motoru:** 8 kriter, ağırlıklı (slope/GHI/aspect/shadow/LC/grid/road/legal)
- **Ülke kural motoru:** `config/country_rules.json` — 20+ ülke (TR,DE,ES,FR,SA,ML,NE…)
- **Finansal model v2:** `app/services/financial.py`
  - IRR: 25 yıllık NPV bisection (eski `1/payback - WACC` formülü kaldırıldı)
  - OPEX: EPC × %1.5/MW/yıl dahil
  - PPA: auction bid değil, piyasa/bilateral fiyatlar
  - Payback: USD cinsinden (kur bağımsız)
- **Grid mesafesi fix:** `app/services/grid.py`
  - OSM 100km radius ile gerçek substation ara
  - Bulamazsa `grid_reliability`'den tahmin: `3 + 95×(1-r)^0.7`
  - TR→24.5km (OSM), DE→4.2km (OSM), ML→65.5km (OSM)
- **Voltaj eşiği fix:**
  - TR: `hv_threshold_mw` 50→150 (85MW proje 154kV'a düşer, 380kV değil)
  - ML/NE/NG/CD: `hv_threshold=999` (bu ülkelerde 380kV hat yok)
- **country_costs.json:** 50+ ülke, PPA/EPC/grid maliyet/reliability
- **Benchmark verisi:** `data/benchmark/` — IRENA LCOE 2024, USPVDB özet, OSM solar (TR/SA)
- **Heatmap modülü:** polygon → MCDA raster → XYZ tile (premium)
- **Batch analiz:** 50 lokasyona kadar toplu analiz
- **PDF rapor:** `/api/v1/analyses/{id}/report`
- **Frontend:** `frontend/index.html` — MapLibre GL, polygon çiz, heatmap overlay

### 6 Ülke Kıyaslama (100ha · Mono · Fixed · son durum)
| Ülke | Skor | Grid | Voltaj | Payback | IRR |
|------|------|------|--------|---------|-----|
| TR Konya | 60.9 | 24.5km | 154kV | 18.3yr | 2.6% |
| SA Riyad | 59.1 | 11.2km | 154kV | 21.4yr | 1.2% |
| ML Mali | 67.1 | 65.5km | 154kV | 6.0yr | 16.4% |
| NE Nijer | 64.7 | 73.3km | 154kV | 5.9yr | 16.6% |
| IN Ahmedabad | 61.7 | 33.8km | 154kV | 9.7yr | 9.2% |
| AU Perth | 57.5 | 6.8km | 380kV | 20.6yr | 1.5% |
| DE Stuttgart | 0.0 | 4.2km | — | hard block | — |

> ML/NE yüksek IRR: off-grid diesel yerine geçme ($0.13-0.15/kWh PPA).
> DE hard block: cropland (ESA LC=40), Almanya'da GES yasak.

### Bekleyen İşler (öncelik sırasıyla)
1. **GitHub Actions secrets** ekle → otomatik CD aktif olsun
   - `DEPLOY_HOST=178.104.69.28`, `DEPLOY_USER=root`, `DEPLOY_SSH_KEY=<private key>`
2. **Frontend test** — MapLibre UI'da polygon çiz → heatmap çalışıyor mu?
3. **OSM veri tamamlama** — DE/IN/ZA solar farm geojson'ları henüz indirilmedi
4. **GEE credentials kalıcı hale getir** — docker rebuild'de kayboluyor
5. **SA/AU IRR iyileştirme** — EPC kalibrasyonu veya PPA revizyon

## Mimari Özet

```
POST /api/v1/analyses  →  Celery task  →  GEE (terrain+GHI)
                                        →  OSM (grid+road)
                                        →  MCDA score
                                        →  capacity.py
                                        →  financial.py (IRR/payback)
                                        →  store.set_done()
GET  /api/v1/analyses/{id}  →  JSON sonuç
GET  /api/v1/analyses/{id}/report  →  PDF
POST /api/v1/maps  →  Celery map_task  →  GeoTIFF → XYZ tiles
```

## Kritik Notlar
- `richdem` Python 3.11 uyumsuz — requirements.txt'ten çıkarıldı
- CORS_ORIGINS .env'de string: `*` veya `a.com,b.com` veya `["a.com"]`
- GEE credentials: docker rebuild'den sonra yeniden kopyalanması gerekiyor
- `.env` dosyası sunucuda `/home/geohan/geohan/geohan-solar/.env`
- `irr_estimate` API'de **yüzde** olarak döner (8.3 = %8.3, 0.083 değil)
- Overpass rate limit: ülkeler arası 90s bekle

## Dosya Haritası (kritik)
```
app/
  services/
    financial.py   — IRR bisection, OPEX, ülke maliyetleri
    grid.py        — OSM substation + reliability fallback
    terrain.py     — GEE slope/aspect/LC
    solar.py       — GEE GHI
    mcda.py        — 8 kriter skoru
    capacity.py    — MW/ha hesabı
    legal.py       — ülke kural motoru
  routers/
    analyses.py    — POST/GET /analyses
    maps.py        — heatmap + tiles
    auth.py        — JWT token
config/
  country_costs.json  — 50+ ülke EPC/PPA/grid maliyet
  country_rules.json  — slope/LC/kV kısıtları
data/benchmark/       — IRENA, USPVDB özet, OSM solar geojson'lar
frontend/index.html   — MapLibre GL UI
scripts/
  fetch_osm_solar.py    — OSM Overpass solar farm çekici
  fetch_worldbank.py    — World Bank + IRENA LCOE
  fetch_uspvdb.py       — USPVDB (API çöktü, LBNL snapshot fallback)
```
