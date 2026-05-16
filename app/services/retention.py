"""Heatmap artefact retention.

`map_task` writes `<id>.tif` + `<id>_constraints.geojson` into
`settings.maps_data_dir` and never deletes them → unbounded disk growth
and orphaned files (the Redis job expires after 7 days but the raster
stays forever).

Policy (retention-only, no new infra): delete artefact files older than
`max_age_days` by mtime. The durable, *valuable* part (stats, params,
status) lives in `job_records` (Postgres) and is untouched; the heavy
raster is regenerable by re-running the analysis. Tile/GeoTIFF endpoints
already 404 gracefully when the file is gone.

`purge_expired_artifacts` is pure-ish (filesystem side effect only,
injectable `now`) so it unit-tests without Celery or a clock.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)

# Only ever delete files this module's producer creates — never recurse,
# never touch anything unexpected in the directory.
_MANAGED_SUFFIXES = (".tif", "_constraints.geojson", "_layout.geojson", "_layout.json")


def purge_expired_artifacts(
    directory: str,
    max_age_days: int,
    *,
    now: Optional[float] = None,
) -> list[str]:
    """Delete managed artefact files older than ``max_age_days`` (by mtime).

    Returns the list of deleted paths. Missing directory → no-op. A file
    that fails to delete is logged and skipped (best-effort sweep)."""
    base = Path(directory)
    if not base.is_dir():
        return []
    if max_age_days <= 0:
        return []

    now = time.time() if now is None else now
    cutoff = now - max_age_days * 86_400
    deleted: list[str] = []

    for entry in base.iterdir():
        if not entry.is_file():
            continue
        if not entry.name.endswith(_MANAGED_SUFFIXES):
            continue
        try:
            if entry.stat().st_mtime >= cutoff:
                continue
            entry.unlink()
            deleted.append(str(entry))
        except OSError as exc:  # races, perms — don't abort the whole sweep
            _log.warning("retention: could not delete %s: %s", entry, exc)

    if deleted:
        _log.info("retention: purged %d expired artefact(s) from %s",
                  len(deleted), directory)
    return deleted
