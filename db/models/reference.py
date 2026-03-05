from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base

# PostgreSQL TIMESTAMPTZ — all timestamps must have timezone info
TIMESTAMPTZ = TIMESTAMP(timezone=True)


class EVChargingNetwork(Base):
    """Static charging network configuration (5 columns).

    Source: 003_create_reference_tables.sql, ev_charging_networks table.
    Not a pipeline target — manually maintained.
    """

    __tablename__ = "ev_charging_networks"

    id: Mapped[int] = mapped_column(primary_key=True)
    network_name: Mapped[str] = mapped_column(String, nullable=False)
    cost_per_kwh: Mapped[Optional[float]] = mapped_column(Numeric)
    effective_date: Mapped[Optional[date]] = mapped_column(Date)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    is_free: Mapped[Optional[bool]] = mapped_column(Boolean, default=False)
    color: Mapped[Optional[str]] = mapped_column(String(7))  # hex color e.g. '#FF0000'


class EVLocationLookup(Base):
    """Known location definitions for EV charging and parking (6 columns).

    Source: 003_create_reference_tables.sql, ev_location_lookup table.
    Not a pipeline target — manually maintained.
    """

    __tablename__ = "ev_location_lookup"

    id: Mapped[int] = mapped_column(primary_key=True)
    location_name: Mapped[str] = mapped_column(String, nullable=False)
    address: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    latitude: Mapped[Optional[float]] = mapped_column(Numeric)
    longitude: Mapped[Optional[float]] = mapped_column(Numeric)
    location_type: Mapped[Optional[str]] = mapped_column(String)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    network_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("ev_charging_networks.id", ondelete="SET NULL"), nullable=True
    )
    cost_per_kwh: Mapped[Optional[float]] = mapped_column(Numeric, nullable=True)


class EVStatistics(Base):
    """Aggregate statistics summary (11 columns).

    Source: 003_create_reference_tables.sql, ev_statistics table.
    Single row, recomputed after pipeline runs.
    """

    __tablename__ = "ev_statistics"

    id: Mapped[int] = mapped_column(primary_key=True)
    computed_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMPTZ, server_default=text("NOW()")
    )
    total_sessions: Mapped[Optional[int]] = mapped_column(Integer)
    total_energy_kwh: Mapped[Optional[float]] = mapped_column(Numeric)
    total_cost: Mapped[Optional[float]] = mapped_column(Numeric)
    total_miles_added: Mapped[Optional[float]] = mapped_column(Numeric)
    avg_session_duration_seconds: Mapped[Optional[float]] = mapped_column(Numeric)
    avg_energy_per_session_kwh: Mapped[Optional[float]] = mapped_column(Numeric)
    avg_cost_per_kwh: Mapped[Optional[float]] = mapped_column(Numeric)
    avg_miles_per_kwh: Mapped[Optional[float]] = mapped_column(Numeric)
    notes: Mapped[Optional[str]] = mapped_column(Text)


class AppSettings(Base):
    """Generic key-value settings for user-configurable app preferences.

    Used for gasoline comparison params, feature toggles, etc.
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[Optional[str]] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=text("NOW()")
    )
