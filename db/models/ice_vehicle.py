"""Registered ICE comparison vehicle model."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Numeric, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base

TIMESTAMPTZ = DateTime(timezone=True)


class IceVehicle(Base):
    """Registered ICE comparison vehicle.

    Stored metric: efficiency in L/100km, tank capacity in liters. The
    is_default flag mirrors the EV "active vehicle" pattern but lives on
    the row rather than in app_settings — only one row may have
    is_default=True (enforced by partial unique index uq_ice_vehicles_one_default).
    """

    __tablename__ = "ice_vehicles"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String, nullable=False)
    fuel_efficiency_l_per_100km: Mapped[float] = mapped_column(Numeric, nullable=False)
    tank_capacity_l: Mapped[float | None] = mapped_column(Numeric)
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
