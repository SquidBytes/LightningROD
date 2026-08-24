"""Append-only archive of raw Home Assistant events."""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, SmallInteger, String, func
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base
from db.types import JSONStorage

TIMESTAMPTZ = DateTime(timezone=True)


class HARawEvent(Base):
    """One Home Assistant state_changed event, stored verbatim."""

    __tablename__ = "ha_raw_events"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Denormalized out of `payload`: JSONStorage has no operator support, so
    # anything filterable has to live in its own column.
    entity_id: Mapped[str] = mapped_column(String, nullable=False)
    device_id: Mapped[str | None] = mapped_column(String)
    slug: Mapped[str | None] = mapped_column(String)
    state: Mapped[str | None] = mapped_column(String)

    # The whole new_state dict, replayable straight back into dispatch_slug.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONStorage, nullable=False)

    # Home Assistant's unit_system at capture time. ha-fordpass localizes
    # several trip attributes into it before emitting them, so the payload
    # alone does not say what its numbers mean.
    ha_unit_system: Mapped[dict[str, Any] | None] = mapped_column(JSONStorage)

    recorded_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )

    # Plain int, not an FK: replay writes use a sentinel config id, and the
    # archive must outlive deletion of the source config row.
    config_id: Mapped[int | None] = mapped_column(Integer)
    source_system: Mapped[str | None] = mapped_column(String(100))

    # Which mapping-code version was live when this event arrived.
    ingest_schema_version: Mapped[int | None] = mapped_column(SmallInteger)

    __table_args__ = (
        Index("idx_ha_raw_events_recorded_at", "recorded_at"),
        Index("idx_ha_raw_events_slug_recorded", "slug", "recorded_at"),
        # Reconnect snapshots re-emit the same states with their original
        # timestamps; the writer dedupes against this on insert.
        Index(
            "uq_ha_raw_events_entity_recorded",
            "entity_id",
            "recorded_at",
            unique=True,
        ),
    )
