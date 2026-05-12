"""SQLAlchemy engine + session factory.

Engine lazy-built on first call; if DATABASE_URL is empty, get_engine()
raises so callers can decide to skip DB-dependent paths. This keeps
existing non-DB endpoints (analyses, maps, batch) working even when
the DB layer isn't configured.
"""
from __future__ import annotations

from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        if not settings.database_url:
            raise RuntimeError(
                "DATABASE_URL is not set; DB-backed features are disabled."
            )
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            future=True,
        )
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    return _engine


def get_session() -> Iterator[Session]:
    """FastAPI dependency: yields a Session, closes it on request end."""
    get_engine()
    assert _SessionLocal is not None
    with _SessionLocal() as session:
        yield session
