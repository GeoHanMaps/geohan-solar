# GeoHan Solar Intelligence

Global GES yatırım skoru SaaS platformu. Detaylı geçmiş → bellek dosyaları.

## Sunucu
- IP: 178.104.69.28 | User: geohan | Path: /home/geohan/geohan/geohan-solar
- SSH: `ssh geohan@178.104.69.28` (key-based; root SSH kapalı)
- Şifre: Bitwarden/1Password

## Stack
- FastAPI + Celery + Redis (Docker Compose)
- Nginx port 80+443, SSL Let's Encrypt — https://geohanmaps.com
- PostgreSQL/PostGIS yok; cache Redis + JSON dosya tabanlı

## Komutlar
```bash
# Container durumu / rebuild / log
docker compose -f /home/geohan/geohan/geohan-solar/docker-compose.yml ps
cd /home/geohan/geohan/geohan-solar && docker compose up -d --build
docker compose logs -f api

# Token al
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
- CD: main'e push → otomatik build + deploy
- Secrets: DEPLOY_HOST, DEPLOY_USER, DEPLOY_SSH_KEY ✓

## Mimari
```
POST /api/v1/analyses  →  Celery  →  GEE (terrain+GHI) + OSM (grid+road)
                                  →  MCDA → capacity.py → financial.py
GET  /api/v1/analyses/{id}/report →  PDF + AI narrative
POST /api/v1/maps                 →  GeoTIFF → XYZ tiles (premium heatmap)
```

## Dosya Haritası
```
app/services/
  financial.py   — IRR bisection, OPEX (country_costs.json'dan dinamik)
  grid.py        — OSM substation + reliability fallback
  solar.py       — CAMS/NSRDB/PVGIS/Open-Meteo/NASA POWER
  mcda.py        — 8 kriter skoru
  capacity.py    — MW/ha (terrain_factor × GCR × SITE_UTIL=0.70)
  legal.py       — ülke kural motoru
  narrative.py   — Claude Haiku AI yorum
config/
  country_costs.json  — 50+ ülke: EPC/PPA/grid/opex_usd_per_mw_year
  country_rules.json  — slope/LC/kV kısıtları
```

## Kritik Notlar
- `irr_estimate` API'de yüzde döner (8.3 = %8.3)
- OPEX: gelişmiş $12k/MW/yıl, gelişmekte $7k/MW/yıl (`opex_usd_per_mw_year`)
- GEE credentials rebuild sonrası bozulabilir → yukarıdaki docker cp komutu
- CORS_ORIGINS .env'de string: `*` veya `a.com,b.com`
- Solar pipeline: GSA yok. CAMS→PVGIS→Open-Meteo→NASA POWER
- Overpass rate limit: ülkeler arası 90s bekle
- `richdem` Python 3.11 uyumsuz — requirements.txt'te yok
- Tests: 373 passed, 1 xfailed (kanonik — straubing_de pre-kalibrasyon xfail kasıtlı)
