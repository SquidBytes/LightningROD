from datetime import datetime
from typing import Optional

from sqlalchemy import Index, Numeric, String, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base

# PostgreSQL TIMESTAMPTZ — all timestamps must have timezone info
TIMESTAMPTZ = TIMESTAMP(timezone=True)


class EVBatteryStatus(Base):
    """EV HV and LV battery status snapshots (21 columns).

    Source: 002_create_target_tables.sql, ev_battery_status table.
    """

    __tablename__ = "ev_battery_status"

    # Primary identifier
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(String, nullable=False)

    # Timestamp (TIMESTAMPTZ — no dual column needed)
    recorded_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)

    # HV battery metrics
    hv_battery_soc: Mapped[Optional[float]] = mapped_column(Numeric)
    hv_battery_actual_soc: Mapped[Optional[float]] = mapped_column(Numeric)
    hv_battery_voltage: Mapped[Optional[float]] = mapped_column(Numeric)
    hv_battery_amperage: Mapped[Optional[float]] = mapped_column(Numeric)
    hv_battery_kw: Mapped[Optional[float]] = mapped_column(Numeric)
    hv_battery_capacity: Mapped[Optional[float]] = mapped_column(Numeric)
    hv_battery_range: Mapped[Optional[float]] = mapped_column(Numeric)
    hv_battery_max_range: Mapped[Optional[float]] = mapped_column(Numeric)
    hv_battery_temperature: Mapped[Optional[float]] = mapped_column(Numeric)

    # LV (12V) battery
    lv_battery_level: Mapped[Optional[float]] = mapped_column(Numeric)
    lv_battery_voltage: Mapped[Optional[float]] = mapped_column(Numeric)

    # Motor metrics
    motor_voltage: Mapped[Optional[float]] = mapped_column(Numeric)
    motor_amperage: Mapped[Optional[float]] = mapped_column(Numeric)
    motor_kw: Mapped[Optional[float]] = mapped_column(Numeric)

    # Status
    performance_status: Mapped[Optional[str]] = mapped_column(String)

    # Pipeline metadata
    source_system: Mapped[Optional[str]] = mapped_column(String(100))
    ingested_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=text("NOW()")
    )
    original_timestamp: Mapped[Optional[datetime]] = mapped_column(TIMESTAMPTZ)

    __table_args__ = (
        Index("idx_ev_battery_status_recorded_at", "recorded_at"),
        Index("idx_ev_battery_status_device_id", "device_id"),
        Index("idx_ev_battery_status_source_system", "source_system"),
    )
