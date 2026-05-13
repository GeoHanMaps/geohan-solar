"""allow NULL user_id on credit_transactions for system-actor audit rows

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-13

Sprint 9 M4 — admin bypass writes amount=0 audit rows with user_id NULL.
The reason column ("admin_bypass", later "stripe_webhook") identifies the
non-user actor.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "credit_transactions",
        "user_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=True,
    )


def downgrade() -> None:
    # Backfill any audit rows to a placeholder before re-tightening would be
    # required in practice; for now we simply re-impose NOT NULL and let
    # downgrade fail loudly if audit rows exist.
    op.alter_column(
        "credit_transactions",
        "user_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=False,
    )
