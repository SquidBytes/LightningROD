"""Verbatim archive of FordConnect API responses."""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, SmallInteger, String, func
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base
from db.types import JSONStorage

TIMESTAMPTZ = DateTime(timezone=True)


class FconRawSample(Base):
    """One distinct response from one FordConnect endpoint."""

    __tablename__ = "fcon_raw_samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Plain int, not an FK: the archive must outlive its config row.
    config_id: Mapped[int] = mapped_column(Integer, nullable=False)
    device_id: Mapped[str | None] = mapped_column(String)
    endpoint: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONStorage, nullable=False)
    # Changes only when the vehicle reports, so an unchanged poll is a no-op.
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    # The payload's own updateTime when it has one, else fetch time.
    recorded_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    source_system: Mapped[str | None] = mapped_column(String(100))
    ingest_schema_version: Mapped[int | None] = mapped_column(SmallInteger)

    __table_args__ = (
        Index("idx_fcon_raw_samples_recorded_at", "recorded_at"),
        Index("idx_fcon_raw_samples_endpoint_recorded", "endpoint", "recorded_at"),
        Index(
            "uq_fcon_raw_samples_config_fingerprint",
            "config_id",
            "fingerprint",
            unique=True,
        ),
    )
