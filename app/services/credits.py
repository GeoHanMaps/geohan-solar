"""Credit ledger service.

Money-equivalent state changes go through these two functions so the balance
on the users row stays in lock-step with the credit_transactions ledger.
SELECT ... FOR UPDATE serialises concurrent charges on the same user; SQLite
ignores the hint (no row-level locks) but Postgres in prod honours it.

`require_credit` is the cost-middleware entrypoint used by paid endpoints
(/analyses, /maps). It folds three jobs into one call site: identify the
caller (DB user or legacy admin), charge the configured cost or write an
admin-bypass audit row, and translate domain errors into HTTP statuses.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.models.credit_transaction import (
    REASON_ADMIN_BYPASS,
    CreditTransaction,
)
from app.models.user import User


_log = logging.getLogger(__name__)


class InsufficientCreditsError(Exception):
    """Raised when a charge would take the user's balance below zero."""

    def __init__(self, *, required: int, available: int) -> None:
        super().__init__(
            f"Insufficient credits: required {required}, available {available}"
        )
        self.required = required
        self.available = available


def _load_user_for_update(session: Session, user_id: uuid.UUID) -> User:
    user = (
        session.query(User)
        .filter(User.id == user_id)
        .with_for_update()
        .one_or_none()
    )
    if user is None:
        raise LookupError(f"User {user_id} not found")
    return user


def charge_credits(
    session: Session,
    *,
    user_id: uuid.UUID,
    amount: int,
    reason: str,
    reference_id: Optional[str] = None,
) -> CreditTransaction:
    """Decrement the user's balance by `amount` (must be positive) and
    record a matching ledger row. Caller is responsible for the surrounding
    commit/rollback — keep the session boundary tight so the row lock is
    released promptly."""
    if amount <= 0:
        raise ValueError("charge amount must be positive")

    user = _load_user_for_update(session, user_id)
    if user.credits < amount:
        raise InsufficientCreditsError(required=amount, available=user.credits)

    user.credits -= amount
    session.flush()

    tx = CreditTransaction(
        user_id=user.id,
        amount=-amount,
        balance_after=user.credits,
        reason=reason,
        reference_id=reference_id,
    )
    session.add(tx)
    session.flush()
    return tx


def add_credits(
    session: Session,
    *,
    user_id: uuid.UUID,
    amount: int,
    reason: str,
    reference_id: Optional[str] = None,
) -> CreditTransaction:
    """Increment the user's balance and record a positive ledger row.
    Used for purchases, refunds, and admin adjustments."""
    if amount <= 0:
        raise ValueError("credit amount must be positive")

    user = _load_user_for_update(session, user_id)
    user.credits += amount
    session.flush()

    tx = CreditTransaction(
        user_id=user.id,
        amount=amount,
        balance_after=user.credits,
        reason=reason,
        reference_id=reference_id,
    )
    session.add(tx)
    session.flush()
    return tx


def require_credit(
    session: Session,
    token_payload: dict,
    *,
    cost: int,
    reason: str,
    reference_id: str,
) -> None:
    """Cost middleware used by /analyses and /maps. Decoded JWT payload is
    passed in (caller already validated it). Behaviour:

    - Token has `uid` claim → load that DB user, charge `cost`. 402 on
      insufficient balance.
    - Token has no `uid` but sub matches the env-configured admin → write
      an audit row (amount=0, user_id NULL, reason="admin_bypass") and log
      a warning. No balance change. This is the only path where admin gets
      unlimited usage; the warning + persisted audit row means a leaked
      admin token surfaces in `/credits/history` (system rows) and Grafana
      log scrape almost immediately.
    - Anything else → 401.
    """
    uid = token_payload.get("uid")
    sub = token_payload.get("sub", "")

    if uid is None:
        if sub != settings.api_username:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Geçersiz token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        _log.warning(
            "admin_bypass action=%s ref=%s cost_skipped=%d",
            reason, reference_id, cost,
        )
        session.add(CreditTransaction(
            user_id=None,
            amount=0,
            balance_after=0,
            reason=REASON_ADMIN_BYPASS,
            reference_id=f"{reason}:{reference_id}",
        ))
        session.flush()
        return

    try:
        uid_obj = uuid.UUID(uid)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Geçersiz kullanıcı kimliği",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        charge_credits(session, user_id=uid_obj, amount=cost,
                       reason=reason, reference_id=reference_id)
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Kullanıcı bulunamadı",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except InsufficientCreditsError as exc:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"Yetersiz kredi: {exc.available} mevcut, {exc.required} gerekli. "
                f"Kredi satın almak için /credits paneline geçin."
            ),
        )
