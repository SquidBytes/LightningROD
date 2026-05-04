"""SQLite-backend assertions for the p33 ice_vehicles + unit-policy migration.

Wave 0 stubs — bodies filled in by Plan 02 (migration file).
Verifies the post-migration shape of:
  - ice_vehicles table (created, partial unique index on is_default)
  - ev_vehicles.ice_fuel_efficiency / ice_fuel_tank_capacity / ice_label DROPPED
  - gas_price_history.station_price + average_price multiplied by GAL_PER_LITER
  - gas_price_readings.price multiplied by GAL_PER_LITER
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

try:
    from db.models.ice_vehicle import IceVehicle  # noqa: F401
    HAS_ICE_VEHICLES = True
except ImportError:
    HAS_ICE_VEHICLES = False


@pytest.mark.db
@pytest.mark.skipif(not HAS_ICE_VEHICLES, reason="Wave 1: Plan 02 lands ice_vehicles table")
async def test_p33_ice_vehicles_table_exists(db_session: AsyncSession):
    """ice_vehicles table queryable after migration upgrade."""
    pytest.skip("Wave 1: Plan 02 creates ice_vehicles table")


@pytest.mark.db
@pytest.mark.skipif(not HAS_ICE_VEHICLES, reason="Wave 1: Plan 02 drops legacy columns")
async def test_p33_no_ice_columns_on_ev_vehicles(db_session: AsyncSession):
    """Legacy ICE columns removed from ev_vehicles per SET-03/04."""
    pytest.skip("Wave 1: Plan 02 drops ev_vehicles.ice_* columns")


@pytest.mark.db
@pytest.mark.skipif(not HAS_ICE_VEHICLES, reason="Wave 1: Plan 02 lands partial unique index")
async def test_p33_partial_unique_index_on_default(db_session: AsyncSession):
    """At most one row may be is_default=True (partial unique index)."""
    pytest.skip("Wave 1: Plan 02 creates uq_ice_vehicles_one_default partial unique index")


@pytest.mark.db
async def test_p33_gas_price_history_stored_metric(db_session: AsyncSession):
    """UNIT-02: gas_price_history.station_price + average_price stored in $/L after migration.

    Sanity: insert a known $/L value, read it back, assert no double-conversion.
    The fixup multiplier is a one-shot data migration on existing rows; assertion here
    proves the ROUND-TRIP shape post-migration (insert L value, read L value).
    """
    pytest.skip("Wave 3: Plan 05 verifies metric storage round-trip after migration runs")


@pytest.mark.db
async def test_p33_gas_price_readings_stored_metric(db_session: AsyncSession):
    """UNIT-02: gas_price_readings.price stored in $/L after migration."""
    pytest.skip("Wave 3: Plan 05 verifies readings metric storage round-trip")
