# GeoHan Solar Intelligence

Global GES yatırım skoru SaaS platformu.

## Sunucu
- IP: 178.104.69.28
- User: geohan
- Path: /home/geohan/geohan/geohan-solar
- SSH: `ssh geohan@178.104.69.28`
- Root SSH da çalışır (deploy için)

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
```

## GitHub
- Repo: https://github.com/GeoHanMaps/geohan-solar
- CD: main'e push → otomatik build + deploy (GitHub Actions)
- Secrets eksik: DEPLOY_HOST, DEPLOY_USER, DEPLOY_SSH_KEY (henüz eklenmedi)

## Kritik Notlar
- `richdem` Python 3.11 uyumsuz — requirements.txt'ten çıkarıldı
- CORS_ORIGINS .env'de string olarak yazılabilir (`*` veya `a.com,b.com`)
- GEE credentials henüz yok (health'te `gee: error` normal)
