import json
import warnings
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_SECRET = "change-me-in-production-use-a-random-32-byte-hex"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gee_project: str = "geohan-solar"
    nasa_power_url: str = "https://power.larc.nasa.gov/api/temporal/climatology/point"
    pvgis_url: str = "https://re.jrc.ec.europa.eu/api/v5_2/MRcalc"
    tcmb_url: str = "https://www.tcmb.gov.tr/kurlar/today.xml"

    kwh_price_tl: float = 4.20
    # YEKA GES-2024 Türkiye referansı: $1.26-1.4M/MW — orta değer kullanıldı
    investment_per_mw_usd: float = 1_100_000
    performance_ratio: float = 0.80

    # Solar kaynakları
    open_meteo_archive_url: str = "https://archive-api.open-meteo.com/v1/archive"
    cams_ads_url: str = "https://ads.atmosphere.copernicus.eu/api"
    nsrdb_url: str = "https://developer.nrel.gov/api/solar/solar_resource/v1.json"

    # API key'ler — .env'den okunur, boşsa kaynak atlanır
    cams_key: str = ""
    nsrdb_key: str = ""
    nsrdb_email: str = ""
    anthropic_api_key: str = ""

    # Cache TTL (gün)
    cache_ttl_solar_days: float = 30.0
    cache_ttl_osm_days: float = 7.0
    cache_ttl_downscale_days: float = 180.0

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"

    # JWT auth
    secret_key: str = _DEFAULT_SECRET
    api_username: str = "admin"
    api_password: str = "geohan2024"
    access_token_expire_minutes: int = 1440

    # CORS — virgülle ayrılmış string ("*" veya "https://a.com,https://b.com")
    # veya JSON array (["*"]) olarak .env'de verilebilir
    cors_origins: list[str] = ["*"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors(cls, v):
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("["):
                return json.loads(v)
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    # Rate limiting (istek/dakika)
    rate_limit_analyses: str = "10/minute"
    rate_limit_default: str = "60/minute"

    # Heatmap COG depolama dizini
    maps_data_dir: str = "data/maps"

    # Production modunda OpenAPI docs kapalı
    debug: bool = False


settings = Settings()

if settings.secret_key == _DEFAULT_SECRET:
    warnings.warn(
        "SECRET_KEY varsayılan değerde — .env dosyasında değiştirin: "
        "python -c \"import secrets; print(secrets.token_hex(32))\"",
        stacklevel=1,
    )
