"""Database models for vehicle."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base

TIMESTAMPTZ = DateTime(timezone=True)


class EVVehicle(Base):
    """Registered vehicle with display metadata and device_id linkage.

    Each vehicle has a unique device_id that links it to charging sessions,
    battery status, and trip metrics. The integer PK is used in URLs to
    keep VINs out of the address bar (VEH-04).
    """

    __tablename__ = "ev_vehicles"

    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    make: Mapped[str | None] = mapped_column(String)
    model: Mapped[str | None] = mapped_column(String)
    year: Mapped[int | None] = mapped_column(Integer)
    trim_level: Mapped[str | None] = mapped_column(String)
    battery_option: Mapped[str | None] = mapped_column(String)
    # Usable pack capacity (kWh) — what energy_kwh can be meaningfully divided
    # by. This is the "driver-facing" number (e.g. 98 kWh on a Lightning SR).
    battery_capacity_kwh: Mapped[float | None] = mapped_column(Numeric)
    # Gross pack capacity (kWh) — total installed cells. This is what FordPass
    # reports via `maximumBatteryCapacity` and what battery-health/degradation
    # math must compare against (e.g. 108 kWh on a Lightning SR).
    battery_gross_capacity_kwh: Mapped[float | None] = mapped_column(Numeric)
    vin: Mapped[str | None] = mapped_column(String, unique=True)
    device_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    source_system: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # ICE comparison fields — configure what gas vehicle this EV replaces.
    # Stored metric: efficiency in L/100km, tank capacity in liters.
    ice_fuel_efficiency: Mapped[float | None] = mapped_column(Numeric)  # L/100km
    ice_fuel_tank_capacity: Mapped[float | None] = mapped_column(Numeric)  # liters
    ice_label: Mapped[str | None] = mapped_column(String)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=text("NOW()")
    )
