# GeoHan Solar Intelligence

Global GES yatırım skoru SaaS platformu.

## Sunucu
- IP: 178.104.69.28
- User: geohan (sudo yetkili)
- Path: /home/geohan/geohan/geohan-solar
- SSH: `ssh geohan@178.104.69.28` (key-based; root SSH kapalı)
- API şifresi Bitwarden/1Password'da

## Stack
- FastAPI + Celery + Redis (Docker Compose)
- PostgreSQL/PostGIS yok (henüz), cache JSON dosya tabanlı
- Nginx port 80+443 reverse proxy (host üzerinde, Docker dışı; SSL Let's Encrypt)
- API health: https://geohanmaps.com/api/v1/health

## Komutlar
```bash
# Sunucuda container durumu
docker compose -f /home/geohan/geohan/geohan-solar/docker-compose.yml ps

# Rebuild + restart
cd /home/geohan/geohan/geohan-solar && docker compose up -d --build

# Log izle
docker compose logs -f api

# Token al (test için) — şifreyi .env veya Bitwarden'dan al
curl -s -X POST http://localhost:8000/api/v1/auth/token \
  -d 'username=admin&password=<PASSWORD>' | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])'

# Analiz gönder
curl -s -X POST http://localhost:8000/api/v1/analyses \
  -H 'Content-Type: application/json' -H 'Authorization: Bearer <TOKEN>' \
  -d '{"lat":37.87,"lon":32.49,"country_code":"TR","area_ha":100}'

# GEE credentials kopyala (rebuild sonrası gerekebilir)
docker cp ~/.config/earthengine/credentials \
  $(docker compose ps -q api):/home/geohan/.config/earthengine/credentials
docker compose restart api worker
```

## GitHub
- Repo: https://github.com/GeoHanMaps/geohan-solar
- CD: main'e push → otomatik build + deploy (GitHub Actions)
- Secrets: DEPLOY_HOST, DEPLOY_USER, DEPLOY_SSH_KEY ✓

## Mevcut Durum (2026-05-07)

### Çalışan Servisler
- API: https://geohanmaps.com/api/v1/health → `{"status":"ok","gee":"error","osm":"ok"}`
- Celery worker, Redis: çalışıyor
- GEE: **error** — credentials yeniden kopyalanması gerekiyor

### Tamamlanan İşler
- **MCDA skor motoru:** 8 kriter, ağırlıklı (slope/GHI/aspect/shadow/LC/grid/road/legal)
- **Ülke kural motoru:** `config/country_rules.json` — 20+ ülke (TR,DE,ES,FR,SA,ML,NE…)
- **Finansal model v2:** `app/services/financial.py`
  - IRR: 25 yıllık NPV bisection
  - OPEX: EPC × %1.5/MW/yıl dahil
  - PPA: piyasa/bilateral fiyatlar
  - Payback: USD cinsinden (kur bağımsız)
- **Grid mesafesi fix:** OSM 100km radius, reliability fallback
- **country_costs.json:** 50+ ülke, PPA/EPC/grid maliyet/reliability
- **Heatmap modülü:** polygon → MCDA raster → XYZ tile (premium)
- **Batch analiz:** 50 lokasyona kadar toplu analiz
- **PDF rapor + AI narrative:** Claude Haiku entegrasyonu
- **CI/CD:** GitHub Actions — lint + test + deploy + rollback
- **SSL + domain:** geohanmaps.com, Let's Encrypt (2026-08-01'e kadar)
- **Güvenlik sertleştirme:** fail2ban, UFW, SSH hardening, bandit 0 issue

### 6 Ülke Kıyaslama (100ha · Mono · Fixed)
| Ülke | Skor | Grid | Voltaj | Payback | IRR |
|------|------|------|--------|---------|-----|
| TR Konya | 60.9 | 24.5km | 154kV | 18.3yr | 2.6% |
| SA Riyad | 59.1 | 11.2km | 154kV | 21.4yr | 1.2% |
| ML Mali | 67.1 | 65.5km | 154kV | 6.0yr | 16.4% |
| NE Nijer | 64.7 | 73.3km | 154kV | 5.9yr | 16.6% |
| IN Ahmedabad | 61.7 | 33.8km | 154kV | 9.7yr | 9.2% |
| AU Perth | 57.5 | 6.8km | 380kV | 20.6yr | 1.5% |
| DE Stuttgart | 0.0 | 4.2km | — | hard block | — |

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
- GEE credentials: docker rebuild'den sonra yeniden kopyalanması gerekebilir (volume bozulursa)
- `.env` dosyası sunucuda `/home/geohan/geohan/geohan-solar/.env` (chmod 600)
- `irr_estimate` API'de **yüzde** olarak döner (8.3 = %8.3, 0.083 değil)
- Overpass rate limit: ülkeler arası 90s bekle
- Solar pipeline: GSA kullanılmıyor (lisans alınmadı). CAMS→PVGIS→Open-Meteo→NASA POWER

## Dosya Haritası (kritik)
```
app/
  services/
    financial.py   — IRR bisection, OPEX, ülke maliyetleri
    grid.py        — OSM substation + reliability fallback
    terrain.py     — GEE slope/aspect/LC
    solar.py       — çok-kaynaklı GHI (CAMS/NSRDB/PVGIS/Open-Meteo/NASA POWER)
    mcda.py        — 8 kriter skoru
    capacity.py    — MW/ha hesabı
    legal.py       — ülke kural motoru
    narrative.py   — Claude Haiku AI yorum
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
