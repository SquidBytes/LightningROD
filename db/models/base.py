"""Declarative base class for SQLAlchemy models."""

from datetime import datetime

from sqlalchemy import DateTime, SmallInteger, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# All timestamps carry timezone info.
TIMESTAMPTZ = DateTime(timezone=True)

# Parser versions stamped on ingested rows; each re-parse op compares its own.
HA_INGEST_SCHEMA_VERSION = 2  # adapter-driven ingest with declared source units
FCON_INGEST_SCHEMA_VERSION = 2


class Base(DeclarativeBase):
    pass


class IngestMetaMixin:
    """Pipeline metadata shared by the ingested telemetry tables."""

    source_system: Mapped[str | None] = mapped_column(String(100))
    ingested_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    original_timestamp: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)


class IngestVersionMixin:
    """NULL = older rows with uncertain unit provenance; 2 = declared-unit ingest."""

    ingest_schema_version: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
