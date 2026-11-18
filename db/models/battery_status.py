"""Battery telemetry snapshot model."""

from datetime import datetime

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import TIMESTAMPTZ, Base, IngestMetaMixin, IngestVersionMixin


class EVBatteryStatus(IngestMetaMixin, IngestVersionMixin, Base):
    """HV/LV battery telemetry captured over time."""

    __tablename__ = "ev_battery_status"

    # Primary identifier
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(String, nullable=False)

    # Timestamp (TIMESTAMPTZ — no dual column needed)
    recorded_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)

    # HV battery metrics
    hv_battery_soc: Mapped[float | None] = mapped_column()
    hv_battery_actual_soc: Mapped[float | None] = mapped_column()
    hv_battery_voltage: Mapped[float | None] = mapped_column()
    hv_battery_amperage: Mapped[float | None] = mapped_column()
    hv_battery_kw: Mapped[float | None] = mapped_column()
    hv_battery_capacity: Mapped[float | None] = mapped_column()
    hv_battery_range: Mapped[float | None] = mapped_column()
    hv_battery_max_range: Mapped[float | None] = mapped_column()
    hv_battery_temperature: Mapped[float | None] = mapped_column()

    # LV (12V) battery
    lv_battery_level: Mapped[float | None] = mapped_column()
    lv_battery_voltage: Mapped[float | None] = mapped_column()
    # Ford's batteryLoadStatus, e.g. NORMAL_OPERATION.
    lv_load_status: Mapped[str | None] = mapped_column(String)

    # Motor metrics
    motor_voltage: Mapped[float | None] = mapped_column()
    motor_amperage: Mapped[float | None] = mapped_column()
    motor_kw: Mapped[float | None] = mapped_column()

    # Status
    performance_status: Mapped[str | None] = mapped_column(String)

    __table_args__ = (
        Index("idx_ev_battery_status_recorded_at", "recorded_at"),
        Index("idx_ev_battery_status_device_id", "device_id"),
        Index("idx_ev_battery_status_source_system", "source_system"),
    )
