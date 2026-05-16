"""Durable, user-scoped job record.

Redis (`app.store`) stays the *live* working state the Celery worker writes
during processing — fast and TTL'd (7 gün). This table mirrors job
**ownership** at creation time and the **terminal result** lazily on read,
so:

  * paid artefacts (analiz/heatmap sonucu + PDF girdisi) Redis TTL'i
    aşınca kaybolmaz,
  * `GET /api/v1/analyses` çağıran kullanıcıya scope'lanır (eskiden
    `store.list_all()` global `KEYS` taraması herkesin job'unu döndürüyordu
    — veri sızıntısı),
  * "Geçmiş" gerçek anlamda per-user yapılabilir.

Legacy/admin token'lar (uid claim'i yok) eski yolda kalır; bu tablo
yalnızca DB kullanıcıları için devreye girer. Bu yüzden `user_id`
nullable — admin tarafından açılan job'lar NULL taşır (credit_transactions
admin_bypass deseniyle tutarlı).
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

KIND_ANALYSIS = "analysis"
KIND_MAP = "map"
KIND_BATCH = "batch"

TERMINAL_STATUSES = ("done", "failed")


class JobRecord(Base):
    __tablename__ = "job_records"

    # job_id is generated as str(uuid.uuid4()); Redis keys it as a string and
    # reference_id in credit_transactions is already a string — keep one
    # representation end-to-end to avoid cast friction.
    id: Mapped[str] = mapped_column(String(36), primary_key=True)

    # NULL for legacy/admin-opened jobs (see module docstring).
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    narrative: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_job_records_user_created", "user_id", "created_at"),
    )
