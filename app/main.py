from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.gee_init import initialize_ee
from app.limiter import limiter
from app.routers import analyses, auth, batch, credits
from app.schemas import HealthResponse


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cache-Control"] = "no-store"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_ee()
    Path(settings.maps_data_dir).mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="GeoHan Solar-Intelligence API",
    version="0.1.0",
    description="Global GES yatırım skoru — terrain, solar, grid, access, MCDA",
    lifespan=lifespan,
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
    openapi_url="/openapi.json" if settings.debug else None,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

# Healthcheck probe, SSL terminator ve TestClient bu host'lardan gelir —
# TrustedHostMiddleware bunları DAİMA kabul etmeli. CORS_ORIGINS'ten
# türetmeye bağımlı olmadığı için healthcheck-400 coupling'i çözülür.
_ALWAYS_TRUSTED_HOSTS = {"localhost", "127.0.0.1", "::1", "testclient"}


def compute_trusted_hosts(cors_origins: list[str],
                          explicit_trusted: list[str]) -> list[str] | None:
    """TrustedHostMiddleware allowed_hosts'u hesapla. ``None`` → middleware
    eklenmez (cors=['*'], host kısıtı yok). Aksi halde: explicit
    trusted_hosts (boşsa cors_origins'ten türet) ∪ daima-güvenilir iç
    host'lar — healthcheck/SSL host'u CORS'tan bağımsız garanti edilir."""
    if cors_origins == ["*"]:
        return None
    explicit = explicit_trusted or [
        h.removeprefix("https://").removeprefix("http://").split("/")[0]
        for h in cors_origins
    ]
    return sorted(set(explicit) | _ALWAYS_TRUSTED_HOSTS)


_trusted = compute_trusted_hosts(settings.cors_origins, settings.trusted_hosts)
if _trusted is not None:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_trusted)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

from app.routers.maps import router_maps, router_boundaries

app.include_router(auth.router)
app.include_router(credits.router)
app.include_router(analyses.router)
app.include_router(batch.router)
app.include_router(router_maps)
app.include_router(router_boundaries)

_frontend_dir = Path(__file__).parent.parent / "frontend"

if _frontend_dir.is_dir():
    app.mount("/ui", StaticFiles(directory=str(_frontend_dir), html=True), name="ui")
else:
    @app.get("/ui", include_in_schema=False)
    def _frontend_missing():
        return JSONResponse({"detail": "Frontend bulunamadı"}, status_code=404)


@app.get("/api/v1/health", response_model=HealthResponse, tags=["system"])
def health():
    try:
        import ee
        ee.Number(1).getInfo()
        gee_status = "ok"
    except Exception:
        gee_status = "error"

    return HealthResponse(status="ok", gee=gee_status, osm="ok")
