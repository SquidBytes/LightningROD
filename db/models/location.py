from datetime import datetime
from typing import Optional

from sqlalchemy import Index, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base

# PostgreSQL TIMESTAMPTZ — all timestamps must have timezone info
TIMESTAMPTZ = TIMESTAMP(timezone=True)


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
    latitude: Mapped[Optional[float]] = mapped_column(Numeric)
    longitude: Mapped[Optional[float]] = mapped_column(Numeric)
    gps_accuracy: Mapped[Optional[float]] = mapped_column(Numeric)
    altitude: Mapped[Optional[float]] = mapped_column(Numeric)
    compass_direction: Mapped[Optional[str]] = mapped_column(String)

    # Location metadata
    address: Mapped[Optional[str]] = mapped_column(Text)
    location_type: Mapped[Optional[str]] = mapped_column(String)

    # Pipeline metadata
    source_system: Mapped[Optional[str]] = mapped_column(String(100))
    ingested_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=text("NOW()")
    )
    original_timestamp: Mapped[Optional[datetime]] = mapped_column(TIMESTAMPTZ)

    __table_args__ = (
        Index("idx_ev_location_recorded_at", "recorded_at"),
        Index("idx_ev_location_device_id", "device_id"),
        Index("idx_ev_location_source_system", "source_system"),
        Index("idx_ev_location_device_recorded_at", "device_id", "recorded_at"),
    )
