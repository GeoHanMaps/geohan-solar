# GeoHan Financial Model — v2 (2026-05-02)

## 5-Ülke Kıyaslama (100 ha · Mono · Fixed · 2026-05-02)

| Ülke | Lokasyon | Skor | Grid (OSM) | Voltaj | Payback | IRR |
|------|----------|------|-----------|--------|---------|-----|
| TR | Konya 37.87,32.49 | 60.9 | 24.5 km | 154 kV | 18.3 yr | 2.6% |
| SA | Riyad 24.0,45.0 | 59.1 | 11.2 km | 154 kV | 21.4 yr | 1.2% |
| ML | Mali 17.5,-3.2 | 67.1 | 65.5 km | 380 kV | 6.4 yr | 15.1% |
| NE | Niamey 13.5,2.1 | 64.7 | 73.3 km | 380 kV | 6.4 yr | 15.3% |
| DE | Stuttgart 48.5,9.8 | 0.0 | 4.2 km | — | hard block | — |

> DE hard block: tarım arazisi (ESA LC=40), Almanya'da GES yasak (EEG 2023).  
> ML/NE yüksek IRR: off-grid/mini-grid — dizel ($0.15–0.20/kWh) yerine güneş, PPA $0.13–0.15/kWh.

---

## Model Parametreleri

### EPC Maliyeti (USD/MW)
| Bölge | Örnek | EPC |
|-------|-------|-----|
| Batı Avrupa | DE, FR | $850–920K |
| Türkiye | TR | $1,100K |
| Orta Doğu | SA, AE | $650–680K |
| Güney Asya | IN | $525K |
| Sub-Saharan Afrika | ML, NE | $1,200–1,300K |

### PPA Fiyatları (bilateral piyasa, açık artırma bid değil)
| Ülke | PPA (USD/kWh) | Kaynak/Gerekçe |
|------|--------------|----------------|
| TR | $0.055 | EPİAŞ piyasa / bilateral PPA |
| SA | $0.025 | REPDO özel IPP (NEOM kamu rekoru $0.013 değil) |
| AE | $0.026 | Bilateral; Al Dhafra kamu rekoru $0.0135 değil |
| IN | $0.045 | DISCOM tariff; SECI ihale rekoru $0.028 değil |
| DE | $0.120 | Sanayi elektrik fiyatı |
| ML | $0.130 | Mini-grid dizel yerine geçme |
| NE | $0.150 | Off-grid; neredeyse şebekesiz |

### Voltaj Eşikleri (TR örneği)
| Kapasite | Voltaj | Neden |
|----------|--------|-------|
| < 5 MW | 34 kV | TEİAŞ MV bağlantısı |
| 5 – 150 MW | 154 kV | TEİAŞ HV (standart) |
| > 150 MW | 380 kV | TEİAŞ EHV |

> Önceki model TR'de 50 MW eşiği kullanıyordu → 85 MW proje 380 kV'a düşüyordu (hatalı).  
> Düzeltildi: `hv_threshold_mw: 50 → 150`

---

## IRR Hesabı — Metodoloji

**Eski (hatalı):** `IRR = 1/payback - WACC`  
→ Payback=26 yr, WACC=12% → IRR = 3.8% − 12% = **−8.2%** (anlamsız)

**Yeni (doğru):** 25 yıllık NPV bisection

```
NPV(r) = −Investment + NetAnnualCF × [(1 − (1+r)^−25) / r] = 0
```

Net yıllık nakit akışı:
```
NetCF = AnnualRevenue_USD − OPEX_USD
OPEX  = total_mw × (epc_per_mw × 1.5%)   ← ~$12–17K/MW/yıl
```

---

## OSM Benchmark Verileri (2026-05-02)

| Ülke | OSM Tesis | MW Verisi Olan | Toplam Tag'li | Medyan | Notlar |
|------|-----------|---------------|--------------|--------|--------|
| TR | 35,356 | 217 | 3,894 MW | 5.0 MW | Gerçek ~25 GW → %16 kapsam |
| SA | 195,395 | 53 | 14,417 MW | 14.0 MW | Residential+utility karışık; top: 2.4GW Al Shuaibah |
| DE | 7,343 | 1,307 | 12,981 MW | 3.2 MW | Utility-scale only; gerçek ~90 GW → %14 kapsam |
| IN | 29,698 | 545 | 25,379 MW | 12.7 MW | Gerçek ~90 GW → %28 kapsam |
| ZA | 62,787 | 268 | 5,579 MW | 1.5 MW | REIPPPP projeleri; gerçek ~10 GW |
| ML | 102 | 0 | 0 MW | — | Utility-scale yok; grid fallback devreye girer |

> Mali için OSM'de utility-scale GES yok → `grid_reliability=0.45` → tahmini mesafe 62 km.
> Bu, OSM grid mesafesinin (65.5 km) neden reliability tahminiyle uyuştuğunu açıklar.

## Grid Mesafesi — OSM Fallback

OSM Overpass ile 100 km yarıçapında substation/tower arama.  
OSM sonuç yoksa ülkenin `grid_reliability` değerinden tahmin:

```
est_km = 3 + 95 × (1 − reliability)^0.7
```

| Ülke | reliability | Tahmini mesafe |
|------|-------------|---------------|
| DE | 0.998 | ~3 km |
| TR | 0.88 | ~19 km |
| NG | 0.55 | ~53 km |
| ML | 0.45 | ~62 km |
| NE | 0.35 | ~73 km |

---

## "Afrika'nın En Ücra Köyünde GES" — Sonuç

Mali / Nijer gibi neredeyse şebekesiz bölgelerde:

- **GHI:** 2,200–2,300 kWh/m²/yıl (dünya rekoru seviyesi)
- **EPC:** Yüksek lojistik nedeniyle $1,200–1,300K/MW
- **Grid:** Yok → off-grid/mini-grid ($0.13–0.15/kWh dizel yerine geçme PPA)
- **Sonuç: Payback ~6 yıl, IRR ~%15**

Yüksek maliyet + yüksek güneş + yüksek dizel ikame değeri = **karlı proje**.
