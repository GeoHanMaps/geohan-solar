from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
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

if settings.cors_origins != ["*"]:
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[h.removeprefix("https://").removeprefix("http://").split("/")[0]
                       for h in settings.cors_origins],
    )

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

_frontend_html = Path(__file__).parent.parent / "frontend" / "index.html"

@app.get("/ui", include_in_schema=False)
def serve_frontend():
    if _frontend_html.exists():
        return FileResponse(str(_frontend_html), media_type="text/html")
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
