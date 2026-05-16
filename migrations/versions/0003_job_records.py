"""create job_records

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-15

Sprint 9 — durable, user-scoped job persistence. Redis (app.store) stays the
live working state; this table mirrors ownership at creation and the terminal
result on read so paid artefacts outlive the 7-day Redis TTL and
GET /api/v1/analyses is scoped to the caller (data-leak fix).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_records",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("params", postgresql.JSONB(), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("narrative", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_job_records_user_id", "job_records", ["user_id"])
    op.create_index("ix_job_records_user_created", "job_records", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_job_records_user_created", table_name="job_records")
    op.drop_index("ix_job_records_user_id", table_name="job_records")
    op.drop_table("job_records")
