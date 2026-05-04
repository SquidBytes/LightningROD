"""SQLite-backend assertions for the p33 ice_vehicles + unit-policy migration.

Verifies the post-migration shape of:
  - ice_vehicles table (created, partial unique index on is_default)
  - ev_vehicles.ice_fuel_efficiency / ice_fuel_tank_capacity / ice_label DROPPED
  - gas_price_history.station_price + average_price stored as $/L
  - gas_price_readings.price stored as $/L
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.ice_vehicle import IceVehicle
from db.models.reference import GasPriceHistory, GasPriceReading


@pytest.mark.db
async def test_p33_ice_vehicles_table_exists(db_session: AsyncSession):
    """ice_vehicles table queryable after migration upgrade."""
    result = await db_session.execute(select(IceVehicle))
    rows = list(result.scalars().all())
    # Fresh-install test DB: table exists and is queryable. Seed-data state is
    # backend-dependent; assert by type rather than emptiness so per-test
    # transaction isolation + any seed migrations stay compatible.
    assert isinstance(rows, list)


@pytest.mark.db
async def test_p33_no_ice_columns_on_ev_vehicles(db_session: AsyncSession):
    """Legacy ICE columns removed from ev_vehicles per SET-03/04."""
    result = await db_session.execute(text("PRAGMA table_info(ev_vehicles)"))
    columns = {row[1] for row in result.all()}
    assert "ice_fuel_efficiency" not in columns
    assert "ice_fuel_tank_capacity" not in columns
    assert "ice_label" not in columns


@pytest.mark.db
async def test_p33_partial_unique_index_on_default(db_session: AsyncSession):
    """At most one row may be is_default=True (partial unique index)."""
    # Demote any pre-existing defaults so the conflict is solely from rows we add.
    existing = (await db_session.execute(select(IceVehicle))).scalars().all()
    for r in existing:
        r.is_default = False
    await db_session.flush()

    db_session.add(
        IceVehicle(
            label="Default A",
            fuel_efficiency_l_per_100km=8.0,
            is_default=True,
        )
    )
    await db_session.flush()

    db_session.add(
        IceVehicle(
            label="Default B",
            fuel_efficiency_l_per_100km=9.0,
            is_default=True,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.db
async def test_p33_gas_price_history_stored_metric(db_session: AsyncSession):
    """UNIT-02: gas_price_history.station_price + average_price stored in $/L after migration.

    Sanity: insert a known $/L value, read it back, assert no double-conversion.
    The fixup multiplier is a one-shot data migration on existing rows; this
    assertion proves the ROUND-TRIP shape post-migration (insert L value, read L value).
    """
    # Use a year/month unlikely to collide with other tests — gas_prices.py
    # tests commit rows via upsert_gas_price which bypasses transaction rollback.
    row = GasPriceHistory(
        year=2099,
        month=12,
        station_price=1.058,  # $/L (≈ $4.00 / gal)
        average_price=1.111,  # $/L (≈ $4.20 / gal)
        source="manual",
    )
    db_session.add(row)
    await db_session.flush()

    fetched = (
        await db_session.execute(
            select(GasPriceHistory).where(
                GasPriceHistory.year == 2099, GasPriceHistory.month == 12
            )
        )
    ).scalar_one()
    assert float(fetched.station_price) == pytest.approx(1.058, rel=1e-3)
    assert float(fetched.average_price) == pytest.approx(1.111, rel=1e-3)


@pytest.mark.db
async def test_p33_gas_price_readings_stored_metric(db_session: AsyncSession):
    """UNIT-02: gas_price_readings.price stored in $/L after migration."""
    reading = GasPriceReading(
        entity_id="sensor.gas_price_test",
        price=1.058,  # $/L
        recorded_at=datetime(2025, 1, 15, 12, 0, 0, tzinfo=UTC),
    )
    db_session.add(reading)
    await db_session.flush()

    fetched = (
        await db_session.execute(
            select(GasPriceReading).where(
                GasPriceReading.entity_id == "sensor.gas_price_test"
            )
        )
    ).scalar_one()
    assert float(fetched.price) == pytest.approx(1.058, rel=1e-3)
