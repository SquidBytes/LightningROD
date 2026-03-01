# Import Base and ALL model classes so Alembic's autogenerate sees all tables.
# CRITICAL: Every model module must be imported here. If a module is missing,
# alembic revision --autogenerate will produce an empty migration.
from db.models.base import Base
from db.models.battery_status import EVBatteryStatus
from db.models.charging_session import EVChargingSession
from db.models.location import EVLocation
from db.models.reference import EVChargingNetwork, EVLocationLookup, EVStatistics, AppSettings
from db.models.trip_metrics import EVTripMetrics
from db.models.vehicle_status import EVVehicleStatus

__all__ = [
    "Base",
    "EVChargingSession",
    "EVBatteryStatus",
    "EVTripMetrics",
    "EVLocation",
    "EVVehicleStatus",
    "EVChargingNetwork",
    "EVLocationLookup",
    "EVStatistics",
    "AppSettings",
]
