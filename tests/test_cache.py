import time
import pytest
from app.services import cache


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Her test için ayrı geçici cache dizini."""
    monkeypatch.setattr(cache, "_CACHE_DIR", tmp_path)
    yield tmp_path


class TestGetSet:
    def test_miss_returns_none(self):
        assert cache.get("ghi", lat=37.0, lon=32.0) is None

    def test_set_then_get(self):
        cache.set("ghi", 1850.5, ttl_days=1, lat=37.0, lon=32.0)
        assert cache.get("ghi", lat=37.0, lon=32.0) == pytest.approx(1850.5)

    def test_different_namespace_isolated(self):
        cache.set("ghi",    100.0, ttl_days=1, lat=37.0, lon=32.0)
        cache.set("grid",   5.5,   ttl_days=1, lat=37.0, lon=32.0)
        assert cache.get("ghi",  lat=37.0, lon=32.0) == pytest.approx(100.0)
        assert cache.get("grid", lat=37.0, lon=32.0) == pytest.approx(5.5)

    def test_different_coords_isolated(self):
        cache.set("ghi", 1800.0, ttl_days=1, lat=37.0, lon=32.0)
        cache.set("ghi", 2100.0, ttl_days=1, lat=25.0, lon=45.0)
        assert cache.get("ghi", lat=37.0, lon=32.0) == pytest.approx(1800.0)
        assert cache.get("ghi", lat=25.0, lon=45.0) == pytest.approx(2100.0)

    def test_stores_various_types(self):
        cache.set("test", {"a": 1, "b": [1, 2]}, ttl_days=1, x=1)
        assert cache.get("test", x=1) == {"a": 1, "b": [1, 2]}


class TestExpiry:
    def test_expired_entry_returns_none(self, isolated_cache):
        cache.set("ghi", 1850.0, ttl_days=0.0, lat=37.0, lon=32.0)
        # TTL=0 → hemen süresi doluyor
        time.sleep(0.01)
        assert cache.get("ghi", lat=37.0, lon=32.0) is None

    def test_expired_file_deleted(self, isolated_cache):
        cache.set("ghi", 1850.0, ttl_days=0.0, lat=10.0, lon=10.0)
        time.sleep(0.01)
        cache.get("ghi", lat=10.0, lon=10.0)  # süresi geçmiş → sil
        assert len(list(isolated_cache.glob("*.json"))) == 0

    def test_valid_entry_survives(self):
        cache.set("ghi", 1850.0, ttl_days=1, lat=37.0, lon=32.0)
        assert cache.get("ghi", lat=37.0, lon=32.0) is not None


class TestClearExpired:
    def test_removes_expired_keeps_valid(self, isolated_cache):
        cache.set("ghi",  1000.0, ttl_days=0.0, lat=1.0, lon=1.0)
        cache.set("grid", 5.0,    ttl_days=1,   lat=2.0, lon=2.0)
        time.sleep(0.01)
        removed = cache.clear_expired()
        assert removed == 1
        assert len(list(isolated_cache.glob("*.json"))) == 1

    def test_empty_dir_returns_zero(self, isolated_cache):
        assert cache.clear_expired() == 0


class TestCorruptFile:
    def test_corrupt_file_returns_none(self, isolated_cache):
        bad = isolated_cache / "deadbeef.json"
        bad.write_text("NOT_JSON")
        # Bozuk dosya → None dönmeli, patlamamalı
        result = cache.get("ghi", lat=99.0, lon=99.0)
        assert result is None
