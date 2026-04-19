"""4-scenario integration matrix: {metric-HA, imperial-HA} x {metric-display, imperial-display}.

D-E6. Each scenario: load fixture -> adapter.process_event -> DB write ->
assert stored values are metric-canonical (km / degC / kWh per D-A1).

Phase 29 Plan 02 Task 3: wired up against the ha_fordpass adapter.
"""

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from db.models.battery_status import EVBatteryStatus
from db.models.charging_session import EVChargingSession
from db.models.trip_metrics import EVTripMetrics
from web.services.sources.ha_fordpass.adapter import process_event

pytestmark = [pytest.mark.ha_sim, pytest.mark.db]

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "ha_payloads"

# Matrix oracles: what the DB must contain after processing each fixture.
# All stored values are km / degC / kWh (metric canonical per D-A1).
MATRIX = {
    "metric_ha_metric_vehicle.json":     {"hv_battery_range": 260, "hv_battery_max_range": 418, "trip_distance": 19, "distance_added": 103},
    "metric_ha_imperial_vehicle.json":   {"hv_battery_range": 418, "hv_battery_max_range": 418, "trip_distance": 19, "distance_added": 103},
    "imperial_ha_metric_vehicle.json":   {"hv_battery_range": 418, "hv_battery_max_range": 418, "trip_distance": 19, "distance_added": 103},
    "imperial_ha_imperial_vehicle.json": {"hv_battery_range": 418, "hv_battery_max_range": 418, "trip_distance": 19, "distance_added": 103},
}


@pytest.mark.parametrize("fixture_name,expected", list(MATRIX.items()))
async def test_matrix_fixture_yields_metric_storage(fixture_name, expected, db_session):
    """For each fixture, run every entity through process_event and assert
    the stored metric-canonical values match the oracle.
    """
    payload = json.loads((FIXTURES_DIR / fixture_name).read_text())

    for entity_id, state_dict in payload.items():
        await process_event(entity_id, state_dict, db_session)
    await db_session.flush()

    # --- ev_battery_status (from sensor.*_metrics) ---
    battery = (
        await db_session.execute(
            select(EVBatteryStatus).order_by(EVBatteryStatus.id.desc()).limit(1)
        )
    ).scalar_one_or_none()
    assert battery is not None, f"No battery row written for {fixture_name}"
    assert battery.hv_battery_range == pytest.approx(expected["hv_battery_range"], abs=0.5), (
        f"{fixture_name}: hv_battery_range stored {battery.hv_battery_range} km, "
        f"expected {expected['hv_battery_range']} km"
    )
    assert battery.hv_battery_max_range == pytest.approx(expected["hv_battery_max_range"], abs=0.5), (
        f"{fixture_name}: hv_battery_max_range stored {battery.hv_battery_max_range} km, "
        f"expected {expected['hv_battery_max_range']} km"
    )
    assert battery.ingest_schema_version == 2, (
        f"{fixture_name}: battery.ingest_schema_version = {battery.ingest_schema_version}, expected 2 (D-D1)"
    )

    # --- ev_trip_metrics (from sensor.*_events.xev-key-off-trip-segment-data) ---
    trip = (
        await db_session.execute(
            select(EVTripMetrics).order_by(EVTripMetrics.id.desc()).limit(1)
        )
    ).scalar_one_or_none()
    assert trip is not None, f"No trip row written for {fixture_name}"
    assert trip.distance == pytest.approx(expected["trip_distance"], abs=0.5), (
        f"{fixture_name}: trip.distance stored {trip.distance} km, "
        f"expected {expected['trip_distance']} km"
    )
    assert trip.ingest_schema_version == 2, (
        f"{fixture_name}: trip.ingest_schema_version = {trip.ingest_schema_version}, expected 2 (D-D1)"
    )

    # --- ev_charging_session (from sensor.*_energytransferlogentry) ---
    session = (
        await db_session.execute(
            select(EVChargingSession).order_by(EVChargingSession.id.desc()).limit(1)
        )
    ).scalar_one_or_none()
    assert session is not None, f"No charging_session row written for {fixture_name}"
    assert session.distance_added == pytest.approx(expected["distance_added"], abs=0.5), (
        f"{fixture_name}: session.distance_added stored {session.distance_added} km, "
        f"expected {expected['distance_added']} km"
    )
    assert session.ingest_schema_version == 2, (
        f"{fixture_name}: session.ingest_schema_version = {session.ingest_schema_version}, expected 2 (D-D1)"
    )
