from datetime import datetime
from typing import Optional

from sqlalchemy import Index, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base

# PostgreSQL TIMESTAMPTZ — all timestamps must have timezone info
TIMESTAMPTZ = TIMESTAMP(timezone=True)


class EVVehicleStatus(Base):
    """Vehicle operational status snapshots (31 columns).

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
    odometer: Mapped[Optional[float]] = mapped_column(Numeric)
    speed: Mapped[Optional[float]] = mapped_column(Numeric)
    accelerator_position: Mapped[Optional[float]] = mapped_column(Numeric)

    # Controls
    brake_status: Mapped[Optional[str]] = mapped_column(String)
    gear_position: Mapped[Optional[str]] = mapped_column(String)
    parking_brake: Mapped[Optional[str]] = mapped_column(String)
    ignition_status: Mapped[Optional[str]] = mapped_column(String)
    remote_start_status: Mapped[Optional[str]] = mapped_column(String)

    # Temperatures and torque
    coolant_temp: Mapped[Optional[float]] = mapped_column(Numeric)
    torque_at_transmission: Mapped[Optional[float]] = mapped_column(Numeric)

    # Structured status (JSONB)
    door_lock_status: Mapped[Optional[dict]] = mapped_column(JSONB)
    tire_pressure: Mapped[Optional[dict]] = mapped_column(JSONB)
    indicators: Mapped[Optional[dict]] = mapped_column(JSONB)

    # Dynamics fields (new — from updated FordPass ha-fordpass integration, 2026-02)
    brake_torque: Mapped[Optional[float]] = mapped_column(Numeric)
    wheel_torque_status: Mapped[Optional[str]] = mapped_column(String)
    yaw_rate: Mapped[Optional[float]] = mapped_column(Numeric)
    acceleration: Mapped[Optional[float]] = mapped_column(Numeric)
    engine_speed: Mapped[Optional[float]] = mapped_column(Numeric)
    outside_temperature: Mapped[Optional[float]] = mapped_column(Numeric)
    cabin_temperature: Mapped[Optional[float]] = mapped_column(Numeric)
    deep_sleep_status: Mapped[Optional[str]] = mapped_column(String)
    device_connectivity: Mapped[Optional[str]] = mapped_column(String)
    evcc_status: Mapped[Optional[str]] = mapped_column(String)
    seatbelt_status: Mapped[Optional[str]] = mapped_column(String)
    remote_start_countdown: Mapped[Optional[float]] = mapped_column(Numeric)

    # Pipeline metadata
    source_system: Mapped[Optional[str]] = mapped_column(String(100))
    ingested_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=text("NOW()")
    )
    original_timestamp: Mapped[Optional[datetime]] = mapped_column(TIMESTAMPTZ)

    __table_args__ = (
        Index("idx_ev_vehicle_status_recorded_at", "recorded_at"),
        Index("idx_ev_vehicle_status_device_id", "device_id"),
        Index("idx_ev_vehicle_status_source_system", "source_system"),
    )
