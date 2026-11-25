"""Trip metrics model."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Index,
    Integer,
    String,
    Uuid,
    and_,
)
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import TIMESTAMPTZ, Base, IngestMetaMixin, IngestVersionMixin

# Distances under this read as 0.0 in either unit: the vehicle was on but not driven.
STATIONARY_MAX_KM = 0.05


class EVTripMetrics(IngestMetaMixin, IngestVersionMixin, Base):
    """EV trip efficiency, energy, location, and driving-score data."""

    __tablename__ = "ev_trip_metrics"

    # Primary identifier
    id: Mapped[int] = mapped_column(primary_key=True)
    # Default removed — adapter computes uuid5(NS, device_id|tripUpdateTime)
    # before insert; manual-trip route computes its own uuid5 over user fields.
    # DB-level UNIQUE constraint on trip_id is added by the p34 migration.
    trip_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False
    )
    device_id: Mapped[str] = mapped_column(String, nullable=False)

    # Timestamps (all TIMESTAMPTZ)
    start_time: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    end_time: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    recorded_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)

    # Distance and time
    distance: Mapped[float | None] = mapped_column()
    duration: Mapped[float | None] = mapped_column()
    odometer_start: Mapped[float | None] = mapped_column()
    odometer_end: Mapped[float | None] = mapped_column()

    # Energy
    energy_consumed: Mapped[float | None] = mapped_column()
    efficiency: Mapped[float | None] = mapped_column()
    range_regenerated: Mapped[float | None] = mapped_column()

    # Environmental conditions
    ambient_temp: Mapped[float | None] = mapped_column()
    cabin_temp: Mapped[float | None] = mapped_column()
    outside_air_temp: Mapped[float | None] = mapped_column()

    # Ford: estimated electrical charge recovered through regeneration during the trip, in %.
    charge_regenerated: Mapped[float | None] = mapped_column()

    # Coaching scores
    speed_score: Mapped[float | None] = mapped_column()
    acceleration_score: Mapped[float | None] = mapped_column()
    deceleration_score: Mapped[float | None] = mapped_column()

    # Location references
    start_location_id: Mapped[int | None] = mapped_column(Integer)
    end_location_id: Mapped[int | None] = mapped_column(Integer)

    # Efficiency
    electrical_efficiency: Mapped[float | None] = mapped_column()

    # Mechanical
    brake_torque: Mapped[float | None] = mapped_column()

    # Session flags
    is_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    @hybrid_property
    def is_stationary(self) -> bool:
        """Vehicle was switched on but not driven (distance recorded as ~0)."""
        return self.distance is not None and float(self.distance) < STATIONARY_MAX_KM

    @is_stationary.inplace.expression
    @classmethod
    def _is_stationary_expression(cls):
        return and_(cls.distance.is_not(None), cls.distance < STATIONARY_MAX_KM)

    __table_args__ = (
        Index("idx_ev_trip_metrics_start_time", "start_time"),
        Index("idx_ev_trip_metrics_device_id", "device_id"),
        Index("idx_ev_trip_metrics_source_system", "source_system"),
    )
