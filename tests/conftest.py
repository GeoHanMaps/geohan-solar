"""
Global test fixture'ları.

fake_redis_store : Her test için temiz FakeRedis — store.py Redis bağlantısını replace eder.
eager_celery     : Celery task'larını senkron çalıştırır (broker'a bağlantı yok).
auth_headers     : Geçerli JWT token içeren Authorization başlığı.

DB fixture'ları (Sprint 9):
db_engine        : Function-scope SQLite :memory: (StaticPool, tablolar oluşturulmuş).
db_session_factory: db_engine'e bağlı sessionmaker.
db_override      : autouse — FastAPI'nin get_session ve session_or_none
                   dep'lerini db_engine'e yönlendirir. Önceki override'ı
                   yedekler/restore eder ki test_register, test_credits gibi
                   override'ları üstüne yığan testler bozulmasın.
"""

import pytest
import fakeredis
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def sample_lat():
    return 37.8746


@pytest.fixture
def sample_lon():
    return 32.4932


@pytest.fixture
def flat_sunny_site():
    return {
        "slope_pct": 2.0, "ghi": 1950.0, "aspect_score": 95.0,
        "shadow_score": 98.0, "lc_code": 60,
        "grid_km": 0.8, "road_km": 0.3,
        "yasal_score": 100, "hard_block": False,
    }


@pytest.fixture
def steep_forested_site():
    return {
        "slope_pct": 25.0, "ghi": 1300.0, "aspect_score": 30.0,
        "shadow_score": 60.0, "lc_code": 10,
        "grid_km": 45.0, "road_km": 15.0,
        "yasal_score": 0, "hard_block": True,
    }


# ─── Redis + Celery test altyapısı ────────────────────────────────────────────

@pytest.fixture(autouse=True)
def fake_redis_store():
    """store._client'ı FakeRedis ile replace et, test sonunda sıfırla."""
    import app.store as store
    fake = fakeredis.FakeRedis(decode_responses=True)
    original = store._client
    store._client = fake
    yield fake
    store._client = original


@pytest.fixture(autouse=True, scope="session")
def eager_celery():
    """
    Celery task'larını broker olmadan senkron çalıştır.
    task_eager_propagates=False: task hata verse bile HTTP 202 döner.
    """
    from app.celery_app import celery_app
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = False
    yield
    celery_app.conf.task_always_eager = False


@pytest.fixture(autouse=True, scope="session")
def disable_rate_limit():
    """slowapi's 10/minute analyses cap trips under the full pytest run;
    tests don't validate rate limiting behaviour, so turn it off globally."""
    from app.limiter import limiter
    limiter.enabled = False
    yield
    limiter.enabled = True


@pytest.fixture
def auth_headers():
    """Korumalı endpoint'ler için geçerli Authorization başlığı. M4'ten
    sonra /analyses ve /maps kredi düşüyor; bu fixture admin sub'u
    kullanıyor → bypass + audit row (test isolation için yeterli)."""
    from app.auth import create_access_token
    from app.config import settings
    token = create_access_token(sub=settings.api_username)
    return {"Authorization": f"Bearer {token}"}


# ─── DB fixture'ları (Sprint 9 M2+) ──────────────────────────────────────────

@pytest.fixture
def db_engine():
    """Fresh in-memory SQLite per test. StaticPool keeps a single connection
    so all sessions/operations see the same tables."""
    from app.db import Base
    # Import models so their tables register on Base.metadata.
    from app.models import user as _user  # noqa: F401
    from app.models import credit_transaction as _ct  # noqa: F401
    from app.models import job_record as _jr  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session_factory(db_engine):
    return sessionmaker(bind=db_engine, autoflush=False, autocommit=False)


@pytest.fixture(autouse=True)
def db_override(db_engine, db_session_factory):
    """Route every FastAPI request's get_session / session_or_none through
    the test's in-memory engine. Restores any prior override on exit so
    tests that set their own (test_register, test_credits) still work."""
    from app.main import app
    from app.db import get_session
    from app.routers.auth import session_or_none

    def _yield_session():
        with db_session_factory() as session:
            yield session

    saved = {
        get_session: app.dependency_overrides.get(get_session),
        session_or_none: app.dependency_overrides.get(session_or_none),
    }
    app.dependency_overrides[get_session] = _yield_session
    app.dependency_overrides[session_or_none] = _yield_session
    yield
    for dep, prev in saved.items():
        if prev is None:
            app.dependency_overrides.pop(dep, None)
        else:
            app.dependency_overrides[dep] = prev
