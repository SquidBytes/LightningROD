"""Database models for vehicle status."""

from datetime import datetime

from sqlalchemy import DateTime, Index, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base
from db.types import JSONStorage

# PostgreSQL TIMESTAMPTZ — all timestamps must have timezone info
TIMESTAMPTZ = DateTime(timezone=True)


class EVVehicleStatus(Base):
    """Vehicle operational status snapshots (28 columns).

    Source: 002_create_target_tables.sql, ev_vehicle_status table.
    Includes 12 dynamics fields from updated FordPass ha-fordpass integration (2026-02).
    """

    __tablename__ = "ev_vehicle_status"

    # Primary identifier
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(String, nullable=False)

    # Timestamp
    recorded_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)

    # Drivetrain
    odometer: Mapped[float | None] = mapped_column(Numeric)
    speed: Mapped[float | None] = mapped_column(Numeric)
    accelerator_position: Mapped[float | None] = mapped_column(Numeric)

    # Controls
    brake_status: Mapped[str | None] = mapped_column(String)
    gear_position: Mapped[str | None] = mapped_column(String)
    parking_brake: Mapped[str | None] = mapped_column(String)
    ignition_status: Mapped[str | None] = mapped_column(String)
    remote_start_status: Mapped[str | None] = mapped_column(String)

    # Torque
    torque_at_transmission: Mapped[float | None] = mapped_column(Numeric)

    # Structured status (cross-dialect JSON storage)
    door_lock_status: Mapped[dict | None] = mapped_column(JSONStorage)
    tire_pressure: Mapped[dict | None] = mapped_column(JSONStorage)
    indicators: Mapped[dict | None] = mapped_column(JSONStorage)

    # Dynamics fields (new — from updated FordPass ha-fordpass integration, 2026-02)
    brake_torque: Mapped[float | None] = mapped_column(Numeric)
    wheel_torque_status: Mapped[str | None] = mapped_column(String)
    yaw_rate: Mapped[float | None] = mapped_column(Numeric)
    acceleration: Mapped[float | None] = mapped_column(Numeric)
    deep_sleep_status: Mapped[str | None] = mapped_column(String)
    device_connectivity: Mapped[str | None] = mapped_column(String)
    evcc_status: Mapped[str | None] = mapped_column(String)
    seatbelt_status: Mapped[str | None] = mapped_column(String)

    # Environment (time-series, populated from per-sensor HA entities)
    outside_temperature: Mapped[float | None] = mapped_column(Numeric)
    cabin_temperature: Mapped[float | None] = mapped_column(Numeric)

    # Pipeline metadata
    source_system: Mapped[str | None] = mapped_column(String(100))
    ingested_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    original_timestamp: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)

    __table_args__ = (
        Index("idx_ev_vehicle_status_recorded_at", "recorded_at"),
        Index("idx_ev_vehicle_status_device_id", "device_id"),
        Index("idx_ev_vehicle_status_source_system", "source_system"),
    )
