# GeoHan — Oturum Notu (2026-05-03)

## Bu Oturumda Yapılanlar

### 1. Heatmap Renk Şeması Yenilendi (`app/services/tiler.py`)
- Eski: Kırmızı → Sarı → Yeşil (RdYlGn)
- Yeni: **Beyaz (0) → Sarı (50) → Yeşil (100)**
- Hard block alanlar (score = -1): **Saf kırmızı (#FF0000)** iç + **siyah kenarlık** (1px komşu edge detection, scipy olmadan numpy ile)
- `_rdylgn_rgba` → `_score_rgba` olarak yeniden adlandırıldı

### 2. Yasal Kısıt İkonları Düzeltildi (`frontend/index.html`)
- **Sorun:** CartoDB Dark Matter glyph sunucusu ⚠ (U+26A0) karakterini desteklemiyor → hiçbir ikon çıkmıyordu
- **Çözüm:** `text-field: '⚠'` yerine `circle` layer (renkli daire) + `text-field: '!'` (ASCII, her font destekler)
- Hard block → kırmızı daire, soft block → turuncu daire
- **Clustering eklendi:** Yoğun alanlarda daireler birleşip sayı gösteriyor; tıklayınca zoom yapıp ayrışıyor
- Bireysel noktaya tıklayınca popup: kısıtın nedeni (örn. "Tarım Arazisi — 5403 Sayılı Kanun")

### 3. Legend Güncellendi (`frontend/index.html`)
- Gradient bar: beyaz → sarı → yeşil
- Yeni satır: kırmızı swatch + "Yasal Kısıt — GES Yapılamaz"

### 4. IRR Kalibrasyonu (`config/country_costs.json`)
| Ülke | Değişiklik | Eski IRR | Yeni IRR |
|------|-----------|----------|----------|
| SA | PPA $0.025 → $0.038/kWh (bilateral, kamu ihalesi değil) | 1.2% | ~7.5% |
| AU | PPA $0.042 → $0.058/kWh (elektrik + LGC bundled), EPC $1.0M → $0.90M/MW | 1.5% | ~9.0% |
| TR | PPA $0.055 → $0.068/kWh (EPİAŞ bilateral 2024 piyasası) | 2.6% | ~6.3% |

### 5. CD Pipeline Düzeltmesi (`.github/workflows/cd.yml`)
- `git pull origin main` → `git fetch origin main && git reset --hard origin/main`
- Sunucuya doğrudan `scp` ile hotfix yapılınca `git pull` "local changes" hatası veriyordu

### 6. Test Güncellendi (`tests/test_tiler.py`)
- `_rdylgn_rgba` → `_score_rgba` import düzeltildi
- Renk assertion'ları yeni gradient'e göre güncellendi (0 → beyaz, 0.5 → sarı, 1 → yeşil)

---

## Bilinen Durum

- **API:** `http://178.104.69.28/api/v1/health` → `{"status":"ok","gee":"ok","osm":"ok"}`
- **GEE credentials:** Docker volume `geohan-solar_gee-credentials` ile persist ediliyor (`docker compose down -v` yapılırsa kaybolur)
- **Config dosyaları:** `/home/geohan/geohan/geohan-solar/config/` → container'a read-only mount, container restart gerekmeden değişiklik aktif olur (in-memory cache için restart lazım)

---

## Sıradaki Adımlar (Öncelik Sırasıyla)

### Yüksek Öncelik
1. **Constraint popup test** — Yeni harita üret, kısıtlı alandaki kırmızı/turuncu daireye tıkla, popup açılıyor mu kontrol et. Açılmıyorsa browser console'daki hatayı paylaş.

2. **GEE Service Account** — Şu an OAuth refresh token kullanılıyor, expire olursa GEE çöker ve elle müdahale gerekir.
   - Google Cloud Console → IAM → Service Account oluştur
   - Earth Engine → `ee.Initialize(credentials=service_account_credentials)` kullan
   - JSON key'i `.env`'e `GEE_SERVICE_ACCOUNT_JSON=` olarak ekle
   - `app/services/terrain.py` ve `app/services/solar.py`'deki `ee.Initialize()` çağrısını güncelle

### Orta Öncelik
3. **OSM Solar Farm Verisi** — Benchmark için DE/IN/ZA GeoJSON henüz indirilmedi
   ```bash
   python3 scripts/fetch_osm_solar.py --countries DE IN ZA
   ```

4. **Heatmap pixel açıklaması** — Haritada bir noktaya tıklayınca o pikselin skor bileşenlerini göster (slope/GHI/legal vb.). Şu an sadece constraint ikonları tıklanabilir.

5. **Polygon alanı göster** — Polygon çizilince tahmini alan (ha) frontend'de anlık göster (turf.js ile hesaplanabilir).

### Düşük Öncelik
6. **SA/AU benchmark doğrulama** — Yeni IRR değerlerini (SA ~7.5%, AU ~9%) gerçek ACWA/AGL proje raporlarıyla karşılaştır.

7. **Tile cache** — Mevcut haritalar 1 saat cache'leniyor. Yeni renk şemasıyla üretilen haritalar doğru görünüyor ama eski harita URL'leri eski renkleri gösterebilir (kullanıcıya Ctrl+Shift+R söyle).

---

## Sunucu Hızlı Referans

```bash
# Servis durumu
docker compose -f /home/geohan/geohan/geohan-solar/docker-compose.prod.yml ps

# Log izle
docker compose -f /home/geohan/geohan/geohan-solar/docker-compose.prod.yml logs -f api worker

# Config cache temizle (restart)
docker restart geohan-solar-api-1 geohan-solar-worker-1

# GEE credentials volume içeriği
ls /var/lib/docker/volumes/geohan-solar_gee-credentials/_data/
```
