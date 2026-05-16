"""Sprint 9 #3 — heatmap artefact retention (retention-only, no new infra).

Guards: expired managed files are purged, fresh ones kept, unmanaged files
never touched, and the sweep is safe on a missing dir / subdirs.
"""
import os

from app.services.retention import purge_expired_artifacts

_DAY = 86_400


def _write(path, mtime):
    path.write_bytes(b"x")
    os.utime(path, (mtime, mtime))


def test_expired_purged_fresh_kept(tmp_path):
    now = 1_000_000_000.0
    old_tif = tmp_path / "a.tif"
    old_geojson = tmp_path / "a_constraints.geojson"
    fresh_tif = tmp_path / "b.tif"

    _write(old_tif, now - 20 * _DAY)
    _write(old_geojson, now - 20 * _DAY)
    _write(fresh_tif, now - 2 * _DAY)

    deleted = purge_expired_artifacts(str(tmp_path), 14, now=now)

    assert set(deleted) == {str(old_tif), str(old_geojson)}
    assert not old_tif.exists()
    assert not old_geojson.exists()
    assert fresh_tif.exists()


def test_unmanaged_files_never_touched(tmp_path):
    now = 1_000_000_000.0
    keep = tmp_path / "important.txt"          # not a managed suffix
    _write(keep, now - 999 * _DAY)             # very old, but unmanaged

    deleted = purge_expired_artifacts(str(tmp_path), 14, now=now)

    assert deleted == []
    assert keep.exists()


def test_missing_dir_is_noop():
    assert purge_expired_artifacts("/no/such/geohan/dir", 14) == []


def test_zero_or_negative_age_is_noop(tmp_path):
    now = 1_000_000_000.0
    f = tmp_path / "a.tif"
    _write(f, now - 999 * _DAY)

    assert purge_expired_artifacts(str(tmp_path), 0, now=now) == []
    assert f.exists()


def test_subdirectories_not_recursed_or_deleted(tmp_path):
    now = 1_000_000_000.0
    sub = tmp_path / "nested"
    sub.mkdir()
    nested_tif = sub / "c.tif"
    _write(nested_tif, now - 999 * _DAY)
    os.utime(sub, (now - 999 * _DAY, now - 999 * _DAY))

    deleted = purge_expired_artifacts(str(tmp_path), 14, now=now)

    assert deleted == []
    assert sub.is_dir()
    assert nested_tif.exists()


def test_celery_task_returns_count(tmp_path, monkeypatch):
    from app import tasks
    from app.config import settings
    from app.services import cache

    now_old = 1.0  # epoch-ish → guaranteed older than cutoff
    f = tmp_path / "old.tif"
    f.write_bytes(b"x")
    os.utime(f, (now_old, now_old))

    monkeypatch.setattr(settings, "maps_data_dir", str(tmp_path))
    monkeypatch.setattr(settings, "maps_retention_days", 14)
    # Isolate the spatial-cache sweep to an empty dir so it contributes 0
    # (don't touch the real ./cache on the dev box).
    monkeypatch.setattr(cache, "_CACHE_DIR", tmp_path / "cache_empty")
    (tmp_path / "cache_empty").mkdir()

    assert tasks.cleanup_artifacts_task() == 1
    assert not f.exists()


def test_cleanup_task_also_clears_expired_cache(tmp_path, monkeypatch):
    """#4: the daily sweep is the only thing that bounds the upstream
    spatial-cache dir (cache.clear_expired had no caller)."""
    from app import tasks
    from app.config import settings
    from app.services import cache

    cache_dir = tmp_path / "spatial_cache"
    cache_dir.mkdir()
    monkeypatch.setattr(cache, "_CACHE_DIR", cache_dir)
    # Empty maps dir → artefact sweep contributes 0.
    maps_dir = tmp_path / "maps"
    maps_dir.mkdir()
    monkeypatch.setattr(settings, "maps_data_dir", str(maps_dir))

    cache.set("ghi", 1850.0, ttl_days=-1, lat=37.0, lon=32.0)   # expired
    cache.set("grid", 2.5, ttl_days=7, lat=37.0, lon=32.0)      # fresh
    assert cache.get("grid", lat=37.0, lon=32.0) == 2.5

    removed = tasks.cleanup_artifacts_task()

    assert removed == 1                                          # only expired
    assert cache.get("grid", lat=37.0, lon=32.0) == 2.5          # fresh kept
