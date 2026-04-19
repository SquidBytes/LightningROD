"""Reporter-scenario regression locks (D-E4).

Three named tests for the exact values reported by the user + ha-fordpass
integration author on 2026-04-19. Failure messages explicitly reference the
2026-03-21 bug (commit abd736b) so future regressions are unmistakable.

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
from web.unit_system import convert_distance

pytestmark = [pytest.mark.ha_sim, pytest.mark.db]

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "ha_payloads"

REGRESSION_MESSAGE = (
    "REGRESSION: this is the 2026-03-21 double-conversion bug (commit abd736b) "
    "returning. The ha-fordpass adapter multiplied an already-metric attribute "
    "value by 1.609344 on ingestion. Check web/services/units/to_metric.py "
    "source_unit dispatch AND verify the adapter is reading from the metrics/events "
    "entities per D-B1, not elveh attributes per D-B4."
)


async def _run_fixture(payload: dict, db_session) -> None:
    """Feed every entity in the payload through the adapter."""
    for entity_id, state_dict in payload.items():
        await process_event(entity_id, state_dict, db_session)
    await db_session.flush()


async def test_reporter_19km_trip_not_multiplied(db_session):
    """Lock: a 19 km trip event must store as ~19 km trip.distance, NOT ~30.6 km.

    Reporter setup: 2026 F-150 Lightning, metric HA + imperial vehicle display.
    """
    payload = json.loads((FIXTURES_DIR / "metric_ha_imperial_vehicle.json").read_text())
    await _run_fixture(payload, db_session)

    trip = (
        await db_session.execute(
            select(EVTripMetrics).order_by(EVTripMetrics.id.desc()).limit(1)
        )
    ).scalar_one_or_none()
    assert trip is not None, REGRESSION_MESSAGE + "  (no trip row written — adapter did not process _events entity)"
    assert trip.distance == pytest.approx(19.0, abs=0.5), (
        REGRESSION_MESSAGE + f"  got {trip.distance} km (expected 19.0)"
    )

    # Display-layer lock (Plan 29-04 Task 2, D-E6)
    km_display = convert_distance(trip.distance, "metric")
    mi_display = convert_distance(trip.distance, "us")
    assert km_display == pytest.approx(19.0, abs=0.1), (
        REGRESSION_MESSAGE + f"  km display {km_display}, expected 19.0"
    )
    assert mi_display == pytest.approx(11.8, abs=0.2), (
        REGRESSION_MESSAGE
        + f"  mi display {mi_display}, expected ~11.8 (19 km * 0.621371). "
        "If this shows ~30.6 the 2026-03-21 bug is back."
    )


async def test_reporter_64mi_103km_charge_added(db_session):
    """Lock: charge-added 103 km must store as 103 km distance_added, NOT 165.8 km."""
    payload = json.loads((FIXTURES_DIR / "metric_ha_imperial_vehicle.json").read_text())
    await _run_fixture(payload, db_session)

    session = (
        await db_session.execute(
            select(EVChargingSession).order_by(EVChargingSession.id.desc()).limit(1)
        )
    ).scalar_one_or_none()
    assert session is not None, REGRESSION_MESSAGE + "  (no charging session row written)"
    assert session.distance_added == pytest.approx(103.0, abs=0.5), (
        REGRESSION_MESSAGE + f"  got {session.distance_added} km (expected 103.0)"
    )

    # Display-layer lock (Plan 29-04 Task 2, D-E6) — 64 mi corresponds to 103 km
    km_display = convert_distance(session.distance_added, "metric")
    mi_display = convert_distance(session.distance_added, "us")
    assert km_display == pytest.approx(103.0, abs=0.1), (
        REGRESSION_MESSAGE + f"  km display {km_display}, expected 103.0"
    )
    assert mi_display == pytest.approx(64.0, abs=0.5), (
        REGRESSION_MESSAGE
        + f"  mi display {mi_display}, expected ~64.0 (103 km * 0.621371). "
        "If this shows ~101.9 the reporter scenario is regressing."
    )


async def test_reporter_260mi_418km_max_range(db_session):
    """Lock: max range 418 km must store as 418 km hv_battery_max_range, NOT ~673 km.

    User reported 'Battery-health max-range off by 232 mi (read 492 mi where actual is 260 mi)';
    that is 260*1.609344 = 418 km stored as 418*1.609344 = 673 km, displayed back as 673/1.609344 = 418 mi
    instead of the correct 260 mi.
    """
    payload = json.loads((FIXTURES_DIR / "metric_ha_imperial_vehicle.json").read_text())
    await _run_fixture(payload, db_session)

    battery = (
        await db_session.execute(
            select(EVBatteryStatus).order_by(EVBatteryStatus.id.desc()).limit(1)
        )
    ).scalar_one_or_none()
    assert battery is not None, REGRESSION_MESSAGE + "  (no battery status row written)"
    assert battery.hv_battery_max_range == pytest.approx(418.0, abs=0.5), (
        REGRESSION_MESSAGE + f"  got {battery.hv_battery_max_range} km (expected 418.0)"
    )

    # Display-layer lock (Plan 29-04 Task 2, D-E6) — 260 mi corresponds to 418 km
    km_display = convert_distance(battery.hv_battery_max_range, "metric")
    mi_display = convert_distance(battery.hv_battery_max_range, "us")
    assert km_display == pytest.approx(418.0, abs=0.1), (
        REGRESSION_MESSAGE + f"  km display {km_display}, expected 418.0"
    )
    assert mi_display == pytest.approx(260.0, abs=0.5), (
        REGRESSION_MESSAGE
        + f"  mi display {mi_display}, expected ~260.0 (418 km * 0.621371). "
        "Reporter saw 492 mi where correct is 260 mi."
    )
