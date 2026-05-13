"""Credit ledger service.

Money-equivalent state changes go through these two functions so the balance
on the users row stays in lock-step with the credit_transactions ledger.
SELECT ... FOR UPDATE serialises concurrent charges on the same user; SQLite
ignores the hint (no row-level locks) but Postgres in prod honours it. The
test suite covers the single-writer flow; race-condition coverage lives with
the Postgres-backed integration tests planned for M4.
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.models.credit_transaction import CreditTransaction
from app.models.user import User


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
