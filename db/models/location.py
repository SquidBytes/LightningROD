"""Database models for location."""

from datetime import datetime

from sqlalchemy import DateTime, Index, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base

# PostgreSQL TIMESTAMPTZ — all timestamps must have timezone info
TIMESTAMPTZ = DateTime(timezone=True)


class EVLocation(Base):
    """GPS location time series (13 columns).

    Source: 002_create_target_tables.sql, ev_location table.
    """

    __tablename__ = "ev_location"

    # Primary identifier
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(String, nullable=False)

    # Timestamp (TIMESTAMPTZ — no dual column needed)
    recorded_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)

    # GPS data
    latitude: Mapped[float | None] = mapped_column(Numeric)
    longitude: Mapped[float | None] = mapped_column(Numeric)
    gps_accuracy: Mapped[float | None] = mapped_column(Numeric)
    altitude: Mapped[float | None] = mapped_column(Numeric)
    compass_direction: Mapped[str | None] = mapped_column(String)

    # Location metadata
    address: Mapped[str | None] = mapped_column(Text)
    location_type: Mapped[str | None] = mapped_column(String)

    # Pipeline metadata
    source_system: Mapped[str | None] = mapped_column(String(100))
    ingested_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    original_timestamp: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)

    __table_args__ = (
        Index("idx_ev_location_recorded_at", "recorded_at"),
        Index("idx_ev_location_device_id", "device_id"),
        Index("idx_ev_location_source_system", "source_system"),
        Index("idx_ev_location_device_recorded_at", "device_id", "recorded_at"),
    )
