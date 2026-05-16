"""User-scoped durable job records.

`app.store` (Redis) stays the live working state the Celery worker writes
during processing — fast, 7-day TTL. This module mirrors job **ownership**
at creation and the **terminal result** lazily on read into Postgres so
paid artefacts outlive the Redis TTL and reads are scoped to the caller.

Design boundary (kept deliberately tight, low-risk):

  * **Legacy/admin token** (no `uid` claim) → behaves exactly as before:
    `app.store` is the only source of truth, no DB row required or written.
    This preserves the (admin-token-heavy) existing test suite and keeps
    admin a god/ops actor.
  * **DB user token** (`uid` claim) → ownership is enforced via the
    `job_records` row and the terminal result is promoted into it so it
    survives the Redis TTL.

`identify()` is the single place the token→actor decision is made so the
rule can't drift between routers.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import store
from app.models.credit_transaction import (
    REASON_ANALYSIS,
    REASON_HEATMAP,
    REASON_REFUND,
    CreditTransaction,
)
from app.models.job_record import TERMINAL_STATUSES, JobRecord
from app.services.credits import add_credits

_log = logging.getLogger(__name__)

# Reasons under which a paid job's credit was originally debited; a refund
# returns exactly that magnitude (no hardcoded per-kind cost coupling).
_CHARGE_REASONS = (REASON_ANALYSIS, REASON_HEATMAP)


def identify(token_payload: dict) -> tuple[Optional[uuid.UUID], bool]:
    """Map a decoded JWT to ``(user_id, is_admin)``.

    * ``uid`` claim absent           → ``(None, True)``  legacy/admin god-view
    * ``uid`` claim present & valid  → ``(UUID, False)`` DB user
    * ``uid`` claim present & broken → ``(None, False)`` denied (no god-view
      fallback — a malformed uid must never widen access)
    """
    raw = token_payload.get("uid")
    if raw is None:
        return None, True
    try:
        return uuid.UUID(raw), False
    except (ValueError, TypeError):
        return None, False


def record_create(
    session: Session,
    *,
    job_id: str,
    kind: str,
    name: Optional[str],
    params: Optional[dict],
    user_id: Optional[uuid.UUID],
) -> None:
    """Insert the durable pending row. Called inside the create endpoint's
    existing transaction (same commit as the credit charge) so the row and
    the debit succeed or fail together."""
    session.add(
        JobRecord(
            id=job_id,
            user_id=user_id,
            kind=kind,
            status="pending",
            name=name,
            params=params,
        )
    )
    session.flush()


def record_list(
    session: Session,
    *,
    user_id: uuid.UUID,
    kind: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[JobRecord]:
    stmt = select(JobRecord).where(JobRecord.user_id == user_id)
    if kind is not None:
        stmt = stmt.where(JobRecord.kind == kind)
    stmt = stmt.order_by(JobRecord.created_at.desc()).limit(limit).offset(offset)
    return list(session.execute(stmt).scalars())


def _maybe_refund(session: Session, rec: JobRecord) -> None:
    """A job that failed *after* its credit was debited gets the credit back.

    Idempotent — skipped if a refund row already exists for this job, or if
    nothing was actually charged (admin bypass writes an amount=0 audit row,
    no negative ledger row → nothing to refund). Refunds the exact magnitude
    of the original charge so cost changes can't desync the two paths."""
    if rec.user_id is None:
        return
    already = (
        session.query(CreditTransaction)
        .filter(CreditTransaction.reason == REASON_REFUND,
                CreditTransaction.reference_id == rec.id)
        .first()
    )
    if already is not None:
        return
    charge = (
        session.query(CreditTransaction)
        .filter(CreditTransaction.reference_id == rec.id,
                CreditTransaction.user_id == rec.user_id,
                CreditTransaction.reason.in_(_CHARGE_REASONS),
                CreditTransaction.amount < 0)
        .first()
    )
    if charge is None:
        return
    try:
        add_credits(session, user_id=rec.user_id, amount=-charge.amount,
                    reason=REASON_REFUND, reference_id=rec.id)
        _log.info("refund job=%s user=%s amount=%d (failed job)",
                  rec.id, rec.user_id, -charge.amount)
    except LookupError:
        # User deleted (FK cascade) before the failed job was read — refund
        # is moot; don't 500 the read path over it.
        _log.warning("refund skipped, user gone job=%s", rec.id)


def _promote(session: Session, *, rec: JobRecord, live: dict) -> None:
    """Copy the live Redis state into the durable row (idempotent).

    Status always tracked so history reflects progress; result/error/
    narrative only captured once terminal. A row already terminal is left
    untouched — never overwrite a persisted result with a (possibly
    TTL-expired/empty) live dict. On the failed transition the original
    credit is refunded (once)."""
    if rec.status in TERMINAL_STATUSES:
        return
    status = live.get("status", rec.status)
    rec.status = status
    if status in TERMINAL_STATUSES:
        rec.result = live.get("result")
        rec.error = live.get("error")
        rec.narrative = live.get("narrative")
    session.flush()
    if status == "failed":
        _maybe_refund(session, rec)


def to_job_dict(rec: JobRecord) -> dict:
    """Reconstruct the `app.store`-shaped dict from the durable row, used
    when Redis has expired. Keys mirror what the GET endpoints read."""
    return {
        "status": rec.status,
        "result": rec.result,
        "error": rec.error,
        "name": rec.name,
        "narrative": rec.narrative,
    }


def load_authorized(
    session: Session,
    *,
    job_id: str,
    token_payload: dict,
) -> Optional[dict]:
    """Return the effective job dict (store shape) or ``None`` if the caller
    may not see it / it does not exist. Drop-in for ``store.get(job_id)`` in
    the GET endpoints — they keep their existing ``if not job: 404``.

    Admin: pure store passthrough (unchanged behaviour). DB user: ownership
    enforced via the durable row; terminal Redis state promoted into it; on
    Redis miss the durable row is served instead."""
    uid, is_admin = identify(token_payload)
    live = store.get(job_id)

    if is_admin:
        return live  # legacy god-view; caller raises its own 404 on None

    rec = session.get(JobRecord, job_id)
    # No existence leak: a non-owner gets the same 404 as a missing job.
    if rec is None or rec.user_id != uid:
        return None

    if live is not None:
        _promote(session, rec=rec, live=live)
        session.commit()
        return live
    return to_job_dict(rec)
