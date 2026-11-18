"""Reference models for charging networks, subscriptions, and locations."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import TIMESTAMPTZ, Base


class EVChargingNetwork(Base):
    """Charging network configuration, manually maintained or auto-created."""

    __tablename__ = "ev_charging_networks"

    id: Mapped[int] = mapped_column(primary_key=True)
    network_name: Mapped[str] = mapped_column(String, nullable=False)
    cost_per_kwh: Mapped[Decimal | None] = mapped_column()
    effective_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    is_free: Mapped[bool | None] = mapped_column(Boolean, default=False)
    color: Mapped[str | None] = mapped_column(String(7))  # hex color e.g. '#FF0000'
    is_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    source_system: Mapped[str | None] = mapped_column(String(100), nullable=True)


class EVNetworkSubscription(Base):
    """Subscription period for a charging network with member rate and monthly fee.

    Each row represents a date range during which a subscription was active.
    Multiple periods per network enable tracking historical rate changes.
    """

    __tablename__ = "ev_network_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    network_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ev_charging_networks.id", ondelete="CASCADE"), nullable=False
    )
    member_rate: Mapped[Decimal] = mapped_column(nullable=False)  # $/kWh with subscription
    monthly_fee: Mapped[Decimal] = mapped_column(nullable=False, server_default=text("0"))
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # null = still active
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class EVLocationLookup(Base):
    """Known charging or parking location, manually maintained or auto-created."""

    __tablename__ = "ev_location_lookup"

    id: Mapped[int] = mapped_column(primary_key=True)
    location_name: Mapped[str] = mapped_column(String, nullable=False)
    address: Mapped[str | None] = mapped_column(String, nullable=True)
    latitude: Mapped[float | None] = mapped_column()
    longitude: Mapped[float | None] = mapped_column()
    location_type: Mapped[str | None] = mapped_column(String)
    notes: Mapped[str | None] = mapped_column(Text)
    network_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ev_charging_networks.id", ondelete="SET NULL"), nullable=True
    )
    cost_per_kwh: Mapped[Decimal | None] = mapped_column(nullable=True)
    is_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    source_system: Mapped[str | None] = mapped_column(String(100), nullable=True)


class EVLocationGPSAlias(Base):
    """GPS coordinate alias for a known location.
    Created when locations are merged (source GPS becomes alias on target)
    or when a user confirms a first-time auto-association.
    Used by resolve_location to match future sessions to known locations.
    """

    __tablename__ = "ev_location_gps_aliases"

    id: Mapped[int] = mapped_column(primary_key=True)
    location_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ev_location_lookup.id", ondelete="CASCADE"), nullable=False
    )
    latitude: Mapped[float] = mapped_column(nullable=False)
    longitude: Mapped[float] = mapped_column(nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # 'merge', 'manual', 'auto_confirm'
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, server_default=func.now())


class EVNetworkNameAlias(Base):
    """Alternate name alias for a charging network.
    Created when networks are merged (source name becomes alias on target).
    Used by resolve_network to match future sessions to canonical networks.
    """

    __tablename__ = "ev_network_name_aliases"

    id: Mapped[int] = mapped_column(primary_key=True)
    network_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ev_charging_networks.id", ondelete="CASCADE"), nullable=False
    )
    alias_name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("alias_name", name="uq_ev_network_name_aliases_alias_name"),
    )


class EVChargerStall(Base):
    """Charger stall configuration for a location.

    Each location can have multiple stalls with different specs.
    Used for auto-populating EVSE fields on sessions.
    """

    __tablename__ = "ev_charger_stalls"

    id: Mapped[int] = mapped_column(primary_key=True)
    location_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ev_location_lookup.id", ondelete="CASCADE"), nullable=False
    )
    stall_label: Mapped[str] = mapped_column(String, nullable=False)
    charger_type: Mapped[str | None] = mapped_column(String(10))  # 'L1', 'L2', 'DCFC'
    rated_kw: Mapped[float | None] = mapped_column()
    voltage: Mapped[float | None] = mapped_column()
    amperage: Mapped[float | None] = mapped_column()
    connector_type: Mapped[str | None] = mapped_column(String(20))  # 'CCS', 'CHAdeMO', 'J1772', 'NACS', 'Tesla'
    notes: Mapped[str | None] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class GasPriceHistory(Base):
    """Monthly gas prices with two tracks: station-specific and regional average.

    Each row represents one month's gas prices. The two tracks enable
    range-based savings calculations (low/high bounds).
    """

    __tablename__ = "gas_price_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-12
    station_price: Mapped[Decimal | None] = mapped_column()
    average_price: Mapped[Decimal | None] = mapped_column()
    source: Mapped[str | None] = mapped_column(String(20))  # 'manual' or 'ha_sensor'
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("year", "month", name="uq_gas_price_history_year_month"),
    )


class GasPriceReading(Base):
    """Raw HA sensor readings for gas prices — staging table for monthly averaging.

    Individual readings are stored as they arrive from Home Assistant sensors.
    Periodically averaged into GasPriceHistory entries per month.
    """

    __tablename__ = "gas_price_readings"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[str] = mapped_column(String, nullable=False)
    price: Mapped[Decimal] = mapped_column(nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)


class AppSettings(Base):
    """Generic key-value settings for user-configurable app preferences.

    Used for gasoline comparison params, feature toggles, etc.
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
