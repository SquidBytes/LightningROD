"""Database models for battery status."""

from datetime import datetime

from sqlalchemy import DateTime, Index, Numeric, SmallInteger, String, func
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base

# PostgreSQL TIMESTAMPTZ — all timestamps must have timezone info
TIMESTAMPTZ = DateTime(timezone=True)


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
    hv_battery_soc: Mapped[float | None] = mapped_column(Numeric)
    hv_battery_actual_soc: Mapped[float | None] = mapped_column(Numeric)
    hv_battery_voltage: Mapped[float | None] = mapped_column(Numeric)
    hv_battery_amperage: Mapped[float | None] = mapped_column(Numeric)
    hv_battery_kw: Mapped[float | None] = mapped_column(Numeric)
    hv_battery_capacity: Mapped[float | None] = mapped_column(Numeric)
    hv_battery_range: Mapped[float | None] = mapped_column(Numeric)
    hv_battery_max_range: Mapped[float | None] = mapped_column(Numeric)
    hv_battery_temperature: Mapped[float | None] = mapped_column(Numeric)

    # LV (12V) battery
    lv_battery_level: Mapped[float | None] = mapped_column(Numeric)
    lv_battery_voltage: Mapped[float | None] = mapped_column(Numeric)

    # Motor metrics
    motor_voltage: Mapped[float | None] = mapped_column(Numeric)
    motor_amperage: Mapped[float | None] = mapped_column(Numeric)
    motor_kw: Mapped[float | None] = mapped_column(Numeric)

    # Status
    performance_status: Mapped[str | None] = mapped_column(String)

    # Pipeline metadata
    source_system: Mapped[str | None] = mapped_column(String(100))
    ingested_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    original_timestamp: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)

    # Pipeline schema version. NULL = legacy rows from the suspect conversion
    # era around 2026-03-21 (commit abd736b). Value 2 = adapter-driven ingest
    # with declared source units.
    ingest_schema_version: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    __table_args__ = (
        Index("idx_ev_battery_status_recorded_at", "recorded_at"),
        Index("idx_ev_battery_status_device_id", "device_id"),
        Index("idx_ev_battery_status_source_system", "source_system"),
    )
