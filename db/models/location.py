"""Vehicle GPS location snapshot model."""

from datetime import datetime

from sqlalchemy import Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import TIMESTAMPTZ, Base, IngestMetaMixin


class EVLocation(IngestMetaMixin, Base):
    """GPS location point captured for a vehicle at a timestamp."""

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

    __table_args__ = (
        Index("idx_ev_location_recorded_at", "recorded_at"),
        Index("idx_ev_location_device_id", "device_id"),
        Index("idx_ev_location_source_system", "source_system"),
        Index("idx_ev_location_device_recorded_at", "device_id", "recorded_at"),
    )
