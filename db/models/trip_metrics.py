"""Database models for trip metrics."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Index, Integer, Numeric, SmallInteger, String, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base

# PostgreSQL TIMESTAMPTZ — all timestamps must have timezone info
TIMESTAMPTZ = TIMESTAMP(timezone=True)


class EVTripMetrics(Base):
    """EV trip efficiency, energy, and scoring data (26 columns).

    Source: 002_create_target_tables.sql, ev_trip_metrics table.
    """

    __tablename__ = "ev_trip_metrics"

    # Primary identifier
    id: Mapped[int] = mapped_column(primary_key=True)
    trip_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), default=uuid.uuid4, nullable=False
    )
    device_id: Mapped[str] = mapped_column(String, nullable=False)

    # Timestamps (all TIMESTAMPTZ)
    start_time: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    end_time: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    recorded_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)

    # Distance and time
    distance: Mapped[float | None] = mapped_column(Numeric)
    duration: Mapped[float | None] = mapped_column(Numeric)

    # Energy
    energy_consumed: Mapped[float | None] = mapped_column(Numeric)
    efficiency: Mapped[float | None] = mapped_column(Numeric)
    range_regenerated: Mapped[float | None] = mapped_column(Numeric)

    # Environmental conditions
    ambient_temp: Mapped[float | None] = mapped_column(Numeric)
    cabin_temp: Mapped[float | None] = mapped_column(Numeric)
    outside_air_temp: Mapped[float | None] = mapped_column(Numeric)

    # Driving scores
    driving_score: Mapped[float | None] = mapped_column(Numeric)
    speed_score: Mapped[float | None] = mapped_column(Numeric)
    acceleration_score: Mapped[float | None] = mapped_column(Numeric)
    deceleration_score: Mapped[float | None] = mapped_column(Numeric)

    # Location references
    start_location_id: Mapped[int | None] = mapped_column(Integer)
    end_location_id: Mapped[int | None] = mapped_column(Integer)

    # Efficiency
    electrical_efficiency: Mapped[float | None] = mapped_column(Numeric)

    # Mechanical
    brake_torque: Mapped[float | None] = mapped_column(Numeric)

    # Session flags
    is_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Pipeline metadata
    source_system: Mapped[str | None] = mapped_column(String(100))
    ingested_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=text("NOW()")
    )
    original_timestamp: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)

    # Pipeline schema version. NULL = legacy rows from the suspect conversion
    # era around 2026-03-21 (commit abd736b). Value 2 = adapter-driven ingest
    # with declared source units.
    ingest_schema_version: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    # Pipeline schema version (Phase 29, D-D1). NULL = pre-v2 / suspect era
    # spanning 2026-03-21 onward (commit abd736b double-conversion bug).
    # 2 = ingested via adapter-driven pipeline with declared source units.
    # See .planning/phases/29-unit-ingestion-overhaul/29-CONTEXT.md D-D1/D-D3.
    ingest_schema_version: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    __table_args__ = (
        Index("idx_ev_trip_metrics_start_time", "start_time"),
        Index("idx_ev_trip_metrics_device_id", "device_id"),
        Index("idx_ev_trip_metrics_source_system", "source_system"),
    )
