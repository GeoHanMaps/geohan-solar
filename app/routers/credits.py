"""Credit balance + history endpoints.

State-changing routes (charge/refund) live in the analyses/maps/stripe
middleware — this router only exposes the ledger for the authenticated
user to read."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth import get_current_db_user
from app.db import get_session
from app.models.credit_transaction import CreditTransaction
from app.models.user import User
from app.schemas import (
    BalanceResponse,
    CreditHistoryResponse,
    CreditTransactionItem,
)

router = APIRouter(prefix="/api/v1/credits", tags=["credits"])


@router.get("/balance", response_model=BalanceResponse, summary="Kredi bakiyesi")
def balance(user: User = Depends(get_current_db_user)):
    return BalanceResponse(user_id=str(user.id), credits=user.credits)


@router.get("/history", response_model=CreditHistoryResponse,
            summary="Kredi işlem geçmişi")
def history(
    user: User = Depends(get_current_db_user),
    session: Session = Depends(get_session),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    base = (
        session.query(CreditTransaction)
        .filter(CreditTransaction.user_id == user.id)
    )
    total = base.count()
    rows = (
        base.order_by(CreditTransaction.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    items = [
        CreditTransactionItem(
            id=str(tx.id),
            amount=tx.amount,
            balance_after=tx.balance_after,
            reason=tx.reason,
            reference_id=tx.reference_id,
            created_at=tx.created_at.isoformat(),
        )
        for tx in rows
    ]
    return CreditHistoryResponse(items=items, total=total)
