"""Reporter-scenario regression locks (D-E4).

Three named tests for the exact values reported by the user + ha-fordpass
integration author on 2026-04-19. Failure messages explicitly reference the
2026-03-21 bug (commit abd736b) so future regressions are unmistakable.

MUST fail today — web.services.sources.ha_fordpass.adapter not yet created.
"""

import json
from pathlib import Path

import pytest

from web.services.sources.ha_fordpass.adapter import process_event  # noqa: F401

pytestmark = [pytest.mark.ha_sim, pytest.mark.db]

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "ha_payloads"

REGRESSION_MESSAGE = (
    "REGRESSION: this is the 2026-03-21 double-conversion bug (commit abd736b) "
    "returning. The ha-fordpass adapter multiplied an already-metric attribute "
    "value by 1.609344 on ingestion. Check web/services/units/to_metric.py "
    "source_unit dispatch AND verify the adapter is reading from the metrics/events "
    "entities per D-B1, not elveh attributes per D-B4."
)


async def test_reporter_19km_trip_not_multiplied(db_session):
    """Lock: a 19 km trip event must store as ~19 km trip.distance, NOT ~30.6 km.

    Reporter setup: 2026 F-150 Lightning, metric HA + imperial vehicle display.
    """
    payload = json.loads((FIXTURES_DIR / "metric_ha_imperial_vehicle.json").read_text())
    # TODO(29-02): wire process_event + SELECT trip.distance
    stored_distance_km = None  # replace with actual query result
    pytest.fail(REGRESSION_MESSAGE + "  (test not yet wired; expecting 19.0 km)")


async def test_reporter_64mi_103km_charge_added(db_session):
    """Lock: charge-added 103 km must store as 103 km distance_added, NOT 165.8 km."""
    payload = json.loads((FIXTURES_DIR / "metric_ha_imperial_vehicle.json").read_text())
    # TODO(29-02): wire process_event + SELECT ev_charging_session.distance_added
    stored_km = None
    pytest.fail(REGRESSION_MESSAGE + "  (test not yet wired; expecting 103.0 km)")


async def test_reporter_260mi_418km_max_range(db_session):
    """Lock: max range 418 km must store as 418 km hv_battery_max_range, NOT ~673 km.

    User reported 'Battery-health max-range off by 232 mi (read 492 mi where actual is 260 mi)';
    that is 260*1.609344 = 418 km stored as 418*1.609344 = 673 km, displayed back as 673/1.609344 = 418 mi
    instead of the correct 260 mi.
    """
    payload = json.loads((FIXTURES_DIR / "metric_ha_imperial_vehicle.json").read_text())
    stored_km = None
    pytest.fail(REGRESSION_MESSAGE + "  (test not yet wired; expecting 418.0 km)")
