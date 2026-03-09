from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, Numeric, String, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base

TIMESTAMPTZ = TIMESTAMP(timezone=True)


class EVVehicle(Base):
    """Registered vehicle with display metadata and device_id linkage.

    Each vehicle has a unique device_id that links it to charging sessions,
    battery status, and trip metrics. The integer PK is used in URLs to
    keep VINs out of the address bar (VEH-04).
    """

    __tablename__ = "ev_vehicles"

    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    make: Mapped[Optional[str]] = mapped_column(String)
    model: Mapped[Optional[str]] = mapped_column(String)
    year: Mapped[Optional[int]] = mapped_column(Integer)
    trim: Mapped[Optional[str]] = mapped_column(String)
    battery_capacity_kwh: Mapped[Optional[float]] = mapped_column(Numeric)
    vin: Mapped[Optional[str]] = mapped_column(String, unique=True)
    device_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    source_system: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=text("NOW()")
    )
