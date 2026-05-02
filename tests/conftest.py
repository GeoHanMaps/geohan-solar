"""
Global test fixture'ları.

fake_redis_store : Her test için temiz FakeRedis — store.py Redis bağlantısını replace eder.
eager_celery     : Celery task'larını senkron çalıştırır (broker'a bağlantı yok).
auth_headers     : Geçerli JWT token içeren Authorization başlığı.
"""

import pytest
import fakeredis


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


@pytest.fixture
def auth_headers():
    """Korumalı endpoint'ler için geçerli Authorization başlığı."""
    from app.auth import create_access_token
    token = create_access_token(sub="test")
    return {"Authorization": f"Bearer {token}"}
