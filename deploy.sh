#!/bin/bash
# GeoHan Production Deploy Script
# Kullanım:
#   İlk kurulum:  ./deploy.sh <sunucu_ip> --setup
#   Güncelleme:   ./deploy.sh <sunucu_ip>
#   Rollback:     ./deploy.sh <sunucu_ip> --rollback

set -euo pipefail

SERVER_IP="${1:?Kullanım: ./deploy.sh <sunucu_ip> [--setup|--rollback]}"
SERVER_USER="ubuntu"
MODE="${2:-deploy}"

HEALTH_URL="http://localhost:8000/api/v1/health"
COMPOSE="docker compose -f docker-compose.prod.yml"

# ─── Rollback ─────────────────────────────────────────────────────────────────
if [ "$MODE" = "--rollback" ]; then
  echo "==> Rollback başlıyor: $SERVER_IP"
  ssh "$SERVER_USER@$SERVER_IP" << 'ENDSSH'
set -e
cd /opt/geohan
ROLLBACK=$(cat .rollback_image 2>/dev/null || echo "")
if [ -z "$ROLLBACK" ]; then
  echo "HATA: Rollback için önceki image kaydı yok (.rollback_image)"
  exit 1
fi
echo "Rollback hedefi: $ROLLBACK"
sed -i "s|^API_IMAGE=.*|API_IMAGE=$ROLLBACK|" .env
docker pull "$ROLLBACK" 2>/dev/null || true
docker compose -f docker-compose.prod.yml up -d api worker
echo "Rollback tamamlandı."
ENDSSH
  exit 0
fi

# ─── İlk Kurulum ──────────────────────────────────────────────────────────────
if [ "$MODE" = "--setup" ]; then
  echo "==> İlk kurulum başlıyor: $SERVER_IP"

  echo "  Docker & certbot kuruluyor..."
  ssh "$SERVER_USER@$SERVER_IP" << 'ENDSSH'
set -e
sudo apt-get update -q
sudo apt-get install -y -q docker.io docker-compose-v2 certbot
sudo systemctl enable docker
sudo systemctl start docker
sudo usermod -aG docker "$USER"
sudo mkdir -p /opt/geohan
sudo chown "$USER":"$USER" /opt/geohan
ENDSSH

  echo "  SSL sertifikası alınıyor..."
  ssh "$SERVER_USER@$SERVER_IP" << 'ENDSSH'
set -e
sudo certbot certonly --standalone \
    -d geohanmaps.com \
    -d www.geohanmaps.com \
    --non-interactive \
    --agree-tos \
    -m metehandemirbas96@gmail.com || echo "Sertifika zaten var, atlanıyor."
sudo mkdir -p /opt/geohan/nginx/certs
sudo cp /etc/letsencrypt/live/geohanmaps.com/fullchain.pem /opt/geohan/nginx/certs/
sudo cp /etc/letsencrypt/live/geohanmaps.com/privkey.pem   /opt/geohan/nginx/certs/
sudo chmod 640 /opt/geohan/nginx/certs/*.pem
ENDSSH

  echo "  GEE credentials kopyalanıyor..."
  ssh "$SERVER_USER@$SERVER_IP" "docker volume inspect geohan_gee-credentials >/dev/null 2>&1 || docker volume create geohan_gee-credentials"
  ssh "$SERVER_USER@$SERVER_IP" "docker run --rm -v geohan_gee-credentials:/creds alpine mkdir -p /creds/earthengine"
  scp ~/.config/earthengine/credentials "$SERVER_USER@$SERVER_IP:/tmp/gee_creds"
  ssh "$SERVER_USER@$SERVER_IP" << 'ENDSSH'
docker run --rm -v geohan_gee-credentials:/creds -v /tmp/gee_creds:/src:ro \
  alpine sh -c "cp /src /creds/earthengine/credentials && chmod 600 /creds/earthengine/credentials"
rm -f /tmp/gee_creds
ENDSSH

  echo "  İlk kurulum tamamlandı. Şimdi normal deploy çalıştır:"
  echo "  ./deploy.sh $SERVER_IP"
  exit 0
fi

# ─── Normal Deploy ────────────────────────────────────────────────────────────
echo "==> Deploy başlıyor: $SERVER_IP"

echo "  Proje dosyaları kopyalanıyor..."
rsync -az --exclude='.git' \
          --exclude='__pycache__' \
          --exclude='*.pyc' \
          --exclude='.env' \
          --exclude='cache/' \
          --exclude='tests/' \
          --exclude='data/' \
          ./ "$SERVER_USER@$SERVER_IP:/opt/geohan/"

echo "  .env kopyalanıyor..."
scp .env "$SERVER_USER@$SERVER_IP:/opt/geohan/.env"
ssh "$SERVER_USER@$SERVER_IP" "chmod 600 /opt/geohan/.env"

echo "  Uygulama güncelleniyor..."
ssh "$SERVER_USER@$SERVER_IP" << ENDSSH
set -e
cd /opt/geohan

# Mevcut image'ı rollback için kaydet
CURRENT=\$(docker inspect \$(${COMPOSE} ps -q api 2>/dev/null | head -1) --format '{{.Image}}' 2>/dev/null || echo "")
[ -n "\$CURRENT" ] && echo "\$CURRENT" > .rollback_image && echo "  Rollback image kaydedildi: \$CURRENT"

# Image'ı çek ve başlat
${COMPOSE} pull || true
${COMPOSE} up -d --no-build

# Health check — 90 saniye
echo "  Health check..."
HEALTHY=0
for i in \$(seq 1 18); do
  sleep 5
  STATUS=\$(curl -sf $HEALTH_URL | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null || echo "error")
  if [ "\$STATUS" = "ok" ]; then
    echo "  Sağlıklı (\${i}x5s)"
    HEALTHY=1
    break
  fi
  echo "  Bekleniyor... (\$i/18) — \$STATUS"
done

if [ "\$HEALTHY" -eq 0 ]; then
  echo "HATA: Uygulama sağlıksız — './deploy.sh $SERVER_IP --rollback' ile geri alın"
  exit 1
fi

${COMPOSE} ps
ENDSSH

echo ""
echo "Deploy tamamlandı!"
echo "  https://geohanmaps.com/api/v1/health"
