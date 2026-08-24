"""SQLAlchemy model package."""

# Import Base and ALL model classes so Alembic's autogenerate sees all tables.
# CRITICAL: Every model module must be imported here. If a module is missing,
# alembic revision --autogenerate will produce an empty migration.
from db.models.base import Base
from db.models.battery_status import EVBatteryStatus
from db.models.charging_session import EVChargingSession
from db.models.data_source_config import DataSourceConfig
from db.models.ice_vehicle import IceVehicle
from db.models.location import EVLocation
from db.models.raw_event import HARawEvent
from db.models.reference import (
    AppSettings,
    EVChargerStall,
    EVChargingNetwork,
    EVLocationLookup,
    EVStatistics,
    GasPriceHistory,
    GasPriceReading,
)
from db.models.repair_backup import RepairBackup
from db.models.trip_metrics import EVTripMetrics
from db.models.vehicle import EVVehicle
from db.models.vehicle_status import EVVehicleStatus

__all__ = [
    "Base",
    "DataSourceConfig",
    "EVChargingSession",
    "EVBatteryStatus",
    "EVTripMetrics",
    "EVLocation",
    "EVVehicleStatus",
    "EVChargingNetwork",
    "EVChargerStall",
    "EVLocationLookup",
    "EVStatistics",
    "EVVehicle",
    "IceVehicle",
    "AppSettings",
    "GasPriceHistory",
    "GasPriceReading",
    "HARawEvent",
    "RepairBackup",
]
