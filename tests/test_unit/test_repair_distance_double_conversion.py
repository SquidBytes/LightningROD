"""Distance double-conversion repair: odometer-contradiction census and fix."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from db.models.trip_metrics import EVTripMetrics
from tests.factories.trips import TripFactory
from tests.factories.vehicles import VehicleFactory
from web.services.repair.ops.trip_distance_double_conversion import (
    KM_PER_MILE,
    TripDistanceDoubleConversion,
)

pytestmark = [pytest.mark.unit, pytest.mark.db]

T0 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


async def _seed(db, device: str):
    """One corrupted row, one clean row, one corrupted-looking manual row."""
    corrupted = await TripFactory.create(
        db,
        device_id=device,
        source_system="ha_fordpass",
        distance=196.34,
        odometer_start=1000.0,
        odometer_end=1122.0,  # true delta 122.0 -> ratio ~1.609
        energy_consumed=20.0,
        efficiency=9.82,
        end_time=T0,
    )
    clean = await TripFactory.create(
        db,
        device_id=device,
        source_system="ha_fordpass",
        distance=50.0,
        odometer_start=2000.0,
        odometer_end=2050.2,  # ratio ~0.996
        energy_consumed=9.0,
        end_time=T0,
    )
    manual = await TripFactory.create(
        db,
        device_id=device,
        source_system="manual_entry",
        distance=196.34,
        odometer_start=3000.0,
        odometer_end=3122.0,  # corrupted-looking but not mutable
        energy_consumed=20.0,
        end_time=T0,
    )
    return corrupted, clean, manual


async def test_census_hits_only_odometer_contradicting_mutable_row(db_session):
    device = "DBLCONV_VIN"
    await VehicleFactory.create(db_session, device_id=device)
    corrupted, _, _ = await _seed(db_session, device)

    op = TripDistanceDoubleConversion()
    assert await op.census(db_session) == 1
    assert [r.id for r in await op.affected_rows(db_session)] == [corrupted.id]

    diffs = await op.preview(db_session)
    assert len(diffs) == 1
    assert diffs[0].action == "update"
    assert diffs[0].after["distance"] == pytest.approx(196.34 / KM_PER_MILE)


async def test_apply_divides_and_recomputes_efficiency(db_session):
    device = "DBLCONV_APPLY_VIN"
    await VehicleFactory.create(db_session, device_id=device)
    corrupted, clean, manual = await _seed(db_session, device)

    op = TripDistanceDoubleConversion()
    result = await op.apply(db_session)
    assert result.affected == 1
    assert result.snapshot_rows == 1

    expected_distance = 196.34 / KM_PER_MILE
    assert float(corrupted.distance) == pytest.approx(expected_distance)
    assert float(corrupted.efficiency) == pytest.approx(expected_distance / 20.0)

    # Clean and manual rows untouched.
    assert float(clean.distance) == pytest.approx(50.0)
    assert float(manual.distance) == pytest.approx(196.34)

    # Idempotent: fixed ratio ~1.0 falls outside the band.
    assert await op.census(db_session) == 0
    second = await op.apply(db_session)
    assert second.run_id is None
    assert second.affected == 0
    assert float(corrupted.distance) == pytest.approx(expected_distance)


async def test_apply_without_energy_skips_efficiency(db_session):
    device = "DBLCONV_NOENERGY_VIN"
    await VehicleFactory.create(db_session, device_id=device)
    row = await TripFactory.create(
        db_session,
        device_id=device,
        source_system="ha_fordpass",
        distance=196.34,
        odometer_start=1000.0,
        odometer_end=1122.0,
        energy_consumed=None,
        efficiency=None,
        end_time=T0,
    )

    op = TripDistanceDoubleConversion()
    result = await op.apply(db_session)
    assert result.affected == 1
    assert float(row.distance) == pytest.approx(196.34 / KM_PER_MILE)
    assert row.efficiency is None

    # Sanity: only this device's row exists and was touched.
    rows = (
        (
            await db_session.execute(
                select(EVTripMetrics).where(EVTripMetrics.device_id == device)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
