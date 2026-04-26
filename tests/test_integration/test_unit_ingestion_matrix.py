"""Integration matrix for ingestion and display unit handling.

Runs four fixture scenarios and confirms stored values stay metric-canonical
while display conversion behaves correctly for metric and US views.
"""

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from db.models.battery_status import EVBatteryStatus
from db.models.charging_session import EVChargingSession
from db.models.trip_metrics import EVTripMetrics
from web.services.sources.ha_fordpass.adapter import process_event
from web.unit_system import convert_distance

pytestmark = [pytest.mark.ha_sim, pytest.mark.db]

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "ha_payloads"

# Matrix oracles: what the DB must contain after processing each fixture.
# All stored values are metric-canonical (km / degC / kWh).
MATRIX = {
    "metric_ha_metric_vehicle.json":     {"hv_battery_range": 260, "hv_battery_max_range": 418, "trip_distance": 19, "distance_added": 103},
    "metric_ha_imperial_vehicle.json":   {"hv_battery_range": 418, "hv_battery_max_range": 418, "trip_distance": 19, "distance_added": 103},
    "imperial_ha_metric_vehicle.json":   {"hv_battery_range": 418, "hv_battery_max_range": 418, "trip_distance": 19, "distance_added": 103},
    "imperial_ha_imperial_vehicle.json": {"hv_battery_range": 418, "hv_battery_max_range": 418, "trip_distance": 19, "distance_added": 103},
}

# Display-layer oracles.
# Stored values are km; display values are derived by unit_system.convert_distance
# for the user's distance_unit preference ("us" -> mi, "metric" -> km).
# MI_PER_KM == 0.621371 (web/unit_system.py).
MATRIX_DISPLAY = {
    "metric_ha_metric_vehicle.json": {
        "hv_battery_range_km_display": 260.0,
        "hv_battery_range_mi_display": 161.6,  # 260 * 0.621371
        "hv_battery_max_range_km_display": 418.0,
        "hv_battery_max_range_mi_display": 259.7,  # 418 * 0.621371
        "trip_distance_km_display": 19.0,
        "trip_distance_mi_display": 11.8,  # 19 * 0.621371
        "distance_added_km_display": 103.0,
        "distance_added_mi_display": 64.0,  # 103 * 0.621371
    },
    "metric_ha_imperial_vehicle.json": {
        "hv_battery_range_km_display": 418.0,
        "hv_battery_range_mi_display": 259.7,
        "hv_battery_max_range_km_display": 418.0,
        "hv_battery_max_range_mi_display": 259.7,
        "trip_distance_km_display": 19.0,
        "trip_distance_mi_display": 11.8,
        "distance_added_km_display": 103.0,
        "distance_added_mi_display": 64.0,
    },
    "imperial_ha_metric_vehicle.json": {
        "hv_battery_range_km_display": 418.0,
        "hv_battery_range_mi_display": 259.7,
        "hv_battery_max_range_km_display": 418.0,
        "hv_battery_max_range_mi_display": 259.7,
        "trip_distance_km_display": 19.0,
        "trip_distance_mi_display": 11.8,
        "distance_added_km_display": 103.0,
        "distance_added_mi_display": 64.0,
    },
    "imperial_ha_imperial_vehicle.json": {
        "hv_battery_range_km_display": 418.0,
        "hv_battery_range_mi_display": 259.7,
        "hv_battery_max_range_km_display": 418.0,
        "hv_battery_max_range_mi_display": 259.7,
        "trip_distance_km_display": 19.0,
        "trip_distance_mi_display": 11.8,
        "distance_added_km_display": 103.0,
        "distance_added_mi_display": 64.0,
    },
}


def _ha_config_for_fixture(fixture_name: str) -> dict:
    """Derive the HA ha_config blob the adapter expects from a fixture name.

    Fixture naming convention: `{ha_unit_system}_ha_{vehicle_display}_vehicle.json`.
    The adapter's `ha_unit_system_converted` path reads `ha_config.unit_system`
    to resolve per-event source units for fields ha-fordpass localizes
    (plugDetails.totalDistanceAdded, etc.).
    """
    ha_system = "imperial" if fixture_name.startswith("imperial_ha_") else "metric"
    return {"unit_system": ha_system}


@pytest.mark.parametrize("fixture_name,expected", list(MATRIX.items()))
async def test_matrix_fixture_yields_metric_storage(fixture_name, expected, db_session):
    """For each fixture, run every entity through process_event and assert
    the stored metric-canonical values match the oracle.
    """
    payload = json.loads((FIXTURES_DIR / fixture_name).read_text())
    ha_config = _ha_config_for_fixture(fixture_name)

    for entity_id, state_dict in payload.items():
        await process_event(entity_id, state_dict, db_session, ha_config)
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
        f"{fixture_name}: battery.ingest_schema_version = {battery.ingest_schema_version}, expected 2"
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
        f"{fixture_name}: trip.ingest_schema_version = {trip.ingest_schema_version}, expected 2"
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
        f"{fixture_name}: session.ingest_schema_version = {session.ingest_schema_version}, expected 2"
    )

    # -----------------------------------------------------------------
    # Display-layer assertions
    # -----------------------------------------------------------------
    # Closes the loop from HA payload -> adapter -> DB -> rendered pixel.
    # Same storage values must render correctly for BOTH user preferences.
    disp = MATRIX_DISPLAY[fixture_name]

    # Distance unit "metric" -> km passthrough
    assert convert_distance(battery.hv_battery_range, "metric") == pytest.approx(
        disp["hv_battery_range_km_display"], abs=0.1
    ), f"{fixture_name}: hv_battery_range metric-display mismatch"
    assert convert_distance(battery.hv_battery_max_range, "metric") == pytest.approx(
        disp["hv_battery_max_range_km_display"], abs=0.1
    ), f"{fixture_name}: hv_battery_max_range metric-display mismatch"
    assert convert_distance(trip.distance, "metric") == pytest.approx(
        disp["trip_distance_km_display"], abs=0.1
    ), f"{fixture_name}: trip.distance metric-display mismatch"
    assert convert_distance(session.distance_added, "metric") == pytest.approx(
        disp["distance_added_km_display"], abs=0.1
    ), f"{fixture_name}: distance_added metric-display mismatch"

    # Distance unit "us" -> mi conversion (MI_PER_KM = 0.621371)
    assert convert_distance(battery.hv_battery_range, "us") == pytest.approx(
        disp["hv_battery_range_mi_display"], abs=0.5
    ), f"{fixture_name}: hv_battery_range us-display mismatch"
    assert convert_distance(battery.hv_battery_max_range, "us") == pytest.approx(
        disp["hv_battery_max_range_mi_display"], abs=0.5
    ), f"{fixture_name}: hv_battery_max_range us-display mismatch"
    assert convert_distance(trip.distance, "us") == pytest.approx(
        disp["trip_distance_mi_display"], abs=0.5
    ), f"{fixture_name}: trip.distance us-display mismatch"
    assert convert_distance(session.distance_added, "us") == pytest.approx(
        disp["distance_added_mi_display"], abs=0.5
    ), f"{fixture_name}: distance_added us-display mismatch"
