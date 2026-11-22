"""Charging session model."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import (
    TIMESTAMPTZ,
    Base,
    CentsAmount,
    IngestMetaMixin,
    IngestVersionMixin,
)


class EVChargingSession(IngestMetaMixin, IngestVersionMixin, Base):
    """One EV charging session with cost, location, EVSE, and telemetry fields."""

    __tablename__ = "ev_charging_session"

    # Primary identifier columns
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), default=uuid.uuid4, nullable=False
    )
    device_id: Mapped[str] = mapped_column(String, nullable=False)

    # Session type and location
    charge_type: Mapped[str | None] = mapped_column(String)
    location_name: Mapped[str | None] = mapped_column(String)
    location_type: Mapped[str | None] = mapped_column(String(20))  # 'home', 'work', 'public'
    network_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ev_charging_networks.id", ondelete="SET NULL"), nullable=True
    )
    is_free: Mapped[bool | None] = mapped_column(Boolean)  # whether session was free charging
    plug_status: Mapped[str | None] = mapped_column(String)
    charging_status: Mapped[str | None] = mapped_column(String)
    station_status: Mapped[str | None] = mapped_column(String)

    # Power metrics
    charging_voltage: Mapped[float | None] = mapped_column()
    charging_amperage: Mapped[float | None] = mapped_column()
    charging_kw: Mapped[float | None] = mapped_column()

    # Timestamps (all TIMESTAMPTZ)
    session_start_utc: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    session_end_utc: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    estimated_end_utc: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    recorded_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)

    # Duration columns
    charge_duration_seconds: Mapped[float | None] = mapped_column()
    plugged_in_duration_seconds: Mapped[float | None] = mapped_column()

    # SOC and energy
    start_soc: Mapped[float | None] = mapped_column()
    end_soc: Mapped[float | None] = mapped_column()
    energy_kwh: Mapped[float | None] = mapped_column()

    # Cost
    cost: Mapped[CentsAmount | None] = mapped_column()
    cost_without_overrides: Mapped[CentsAmount | None] = mapped_column()
    cost_source: Mapped[str | None] = mapped_column(String(20))  # 'imported', 'manual', 'calculated', None
    estimated_cost: Mapped[CentsAmount | None] = mapped_column(nullable=True)

    # Session flags
    is_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Location and power range
    location_id: Mapped[int | None] = mapped_column(Integer)

    # HASS-sourced location data
    address: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column()
    longitude: Mapped[float | None] = mapped_column()
    max_power: Mapped[float | None] = mapped_column()
    min_power: Mapped[float | None] = mapped_column()
    distance_added: Mapped[float | None] = mapped_column()  # km

    # EVSE data (charger-side measurements)
    evse_voltage: Mapped[float | None] = mapped_column()
    evse_amperage: Mapped[float | None] = mapped_column()
    evse_kw: Mapped[float | None] = mapped_column()
    evse_energy_kwh: Mapped[float | None] = mapped_column()
    evse_max_power_kw: Mapped[float | None] = mapped_column()
    charger_rated_kw: Mapped[float | None] = mapped_column()
    evse_source: Mapped[str | None] = mapped_column(String(20))  # 'smart_evse', 'energy_monitor', 'manual', 'estimated', 'stall_default'
    stall_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ev_charger_stalls.id", ondelete="SET NULL"), nullable=True
    )

    # Duplicate detection and review
    duplicate_of_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ev_charging_session.id", ondelete="SET NULL"), nullable=True
    )
    needs_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    review_type: Mapped[str | None] = mapped_column(String(20), nullable=True)  # 'duplicate', 'auto_association', NULL

    # Charging-session thermal context
    # °C, NULL when the HA payload does not carry a temp reading. Start/end
    # mirror the same value today because HA exposes single-value snapshots.
    battery_temp_start: Mapped[float | None] = mapped_column(nullable=True)
    battery_temp_end: Mapped[float | None] = mapped_column(nullable=True)
    ambient_temp_start: Mapped[float | None] = mapped_column(nullable=True)
    ambient_temp_end: Mapped[float | None] = mapped_column(nullable=True)

    __table_args__ = (
        UniqueConstraint("session_id", name="uq_ev_charging_session_session_id"),
        Index("idx_ev_charging_session_session_start_utc", "session_start_utc"),
        Index("idx_ev_charging_session_device_id", "device_id"),
        Index("idx_ev_charging_session_source_system", "source_system"),
        Index(
            "idx_ev_charging_session_is_complete",
            "is_complete",
            postgresql_where=text("is_complete = true"),
            sqlite_where=text("is_complete = 1"),
        ),
    )
