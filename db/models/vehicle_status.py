"""Vehicle status snapshot model."""

from datetime import datetime

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import TIMESTAMPTZ, Base, IngestMetaMixin
from db.types import JSONStorage


class EVVehicleStatus(IngestMetaMixin, Base):
    """Operational, drivetrain, environment, and structured vehicle status."""

    __tablename__ = "ev_vehicle_status"

    # Primary identifier
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(String, nullable=False)

    # Timestamp
    recorded_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)

    # Drivetrain
    odometer: Mapped[float | None] = mapped_column()
    speed: Mapped[float | None] = mapped_column()
    accelerator_position: Mapped[float | None] = mapped_column()

    # Controls
    brake_status: Mapped[str | None] = mapped_column(String)
    gear_position: Mapped[str | None] = mapped_column(String)
    parking_brake: Mapped[str | None] = mapped_column(String)
    ignition_status: Mapped[str | None] = mapped_column(String)
    remote_start_status: Mapped[str | None] = mapped_column(String)

    # Torque
    torque_at_transmission: Mapped[float | None] = mapped_column()

    # Structured status (cross-dialect JSON storage)
    door_lock_status: Mapped[dict | None] = mapped_column(JSONStorage)
    tire_pressure: Mapped[dict | None] = mapped_column(JSONStorage)
    indicators: Mapped[dict | None] = mapped_column(JSONStorage)

    # Dynamics fields from FordPass telemetry.
    brake_torque: Mapped[float | None] = mapped_column()
    wheel_torque_status: Mapped[str | None] = mapped_column(String)
    yaw_rate: Mapped[float | None] = mapped_column()
    acceleration: Mapped[float | None] = mapped_column()
    deep_sleep_status: Mapped[str | None] = mapped_column(String)
    device_connectivity: Mapped[str | None] = mapped_column(String)
    evcc_status: Mapped[str | None] = mapped_column(String)
    seatbelt_status: Mapped[str | None] = mapped_column(String)

    # Environment (time-series, populated from per-sensor HA entities)
    outside_temperature: Mapped[float | None] = mapped_column()
    cabin_temperature: Mapped[float | None] = mapped_column()

    __table_args__ = (
        Index("idx_ev_vehicle_status_recorded_at", "recorded_at"),
        Index("idx_ev_vehicle_status_device_id", "device_id"),
        Index("idx_ev_vehicle_status_source_system", "source_system"),
    )
