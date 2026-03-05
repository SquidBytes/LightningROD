import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base

# PostgreSQL TIMESTAMPTZ — all timestamps must have timezone info
TIMESTAMPTZ = TIMESTAMP(timezone=True)


class EVChargingSession(Base):
    """EV charging session records (30 columns).

    Source: 002_create_target_tables.sql, ev_charging_session table.
    """

    __tablename__ = "ev_charging_session"

    # Primary identifier columns
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), default=uuid.uuid4, nullable=False
    )
    device_id: Mapped[str] = mapped_column(String, nullable=False)

    # Session type and location
    charge_type: Mapped[Optional[str]] = mapped_column(String)
    location_name: Mapped[Optional[str]] = mapped_column(String)
    location_type: Mapped[Optional[str]] = mapped_column(String(20))  # 'home', 'work', 'public'
    network_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("ev_charging_networks.id", ondelete="SET NULL"), nullable=True
    )
    is_free: Mapped[Optional[bool]] = mapped_column(Boolean)  # whether session was free charging
    plug_status: Mapped[Optional[str]] = mapped_column(String)
    charging_status: Mapped[Optional[str]] = mapped_column(String)
    station_status: Mapped[Optional[str]] = mapped_column(String)

    # Power metrics
    charging_voltage: Mapped[Optional[float]] = mapped_column(Numeric)
    charging_amperage: Mapped[Optional[float]] = mapped_column(Numeric)
    charging_kw: Mapped[Optional[float]] = mapped_column(Numeric)

    # Timestamps (all TIMESTAMPTZ)
    session_start_utc: Mapped[Optional[datetime]] = mapped_column(TIMESTAMPTZ)
    session_end_utc: Mapped[Optional[datetime]] = mapped_column(TIMESTAMPTZ)
    estimated_end_utc: Mapped[Optional[datetime]] = mapped_column(TIMESTAMPTZ)
    recorded_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMPTZ)

    # Duration columns
    charge_duration_seconds: Mapped[Optional[float]] = mapped_column(Numeric)
    plugged_in_duration_seconds: Mapped[Optional[float]] = mapped_column(Numeric)

    # SOC and energy
    start_soc: Mapped[Optional[float]] = mapped_column(Numeric)
    end_soc: Mapped[Optional[float]] = mapped_column(Numeric)
    energy_kwh: Mapped[Optional[float]] = mapped_column(Numeric)

    # Cost
    cost: Mapped[Optional[float]] = mapped_column(Numeric)
    cost_without_overrides: Mapped[Optional[float]] = mapped_column(Numeric)
    cost_source: Mapped[Optional[str]] = mapped_column(String(20))  # 'imported', 'manual', 'calculated', None
    estimated_cost: Mapped[Optional[float]] = mapped_column(Numeric, nullable=True)

    # Session flags
    is_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Location and power range
    location_id: Mapped[Optional[int]] = mapped_column(Integer)

    # HASS-sourced location data
    address: Mapped[Optional[str]] = mapped_column(Text)
    latitude: Mapped[Optional[float]] = mapped_column(Numeric)
    longitude: Mapped[Optional[float]] = mapped_column(Numeric)
    max_power: Mapped[Optional[float]] = mapped_column(Numeric)
    min_power: Mapped[Optional[float]] = mapped_column(Numeric)
    miles_added: Mapped[Optional[float]] = mapped_column(Numeric)

    # Pipeline metadata
    source_system: Mapped[Optional[str]] = mapped_column(String(100))
    ingested_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=text("NOW()")
    )
    original_timestamp: Mapped[Optional[datetime]] = mapped_column(TIMESTAMPTZ)

    __table_args__ = (
        UniqueConstraint("session_id", name="uq_ev_charging_session_session_id"),
        Index("idx_ev_charging_session_session_start_utc", "session_start_utc"),
        Index("idx_ev_charging_session_device_id", "device_id"),
        Index("idx_ev_charging_session_source_system", "source_system"),
        Index(
            "idx_ev_charging_session_is_complete",
            "is_complete",
            postgresql_where=text("is_complete = true"),
        ),
    )
