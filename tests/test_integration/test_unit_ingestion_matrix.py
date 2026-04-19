"""4-scenario integration matrix: {metric-HA, imperial-HA} × {metric-display, imperial-display}.

D-E6. Each scenario: load fixture -> adapter.process_event -> DB write ->
assert stored values are metric and correct. MUST fail today.
"""

import json
from pathlib import Path

import pytest

from web.services.sources.ha_fordpass.adapter import process_event  # noqa: F401

pytestmark = [pytest.mark.ha_sim, pytest.mark.db]

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "ha_payloads"

# Matrix oracles: what the DB must contain after processing each fixture.
# All stored values are km / °C / kWh (metric canonical per D-A1).
MATRIX = {
    "metric_ha_metric_vehicle.json":     {"hv_battery_range": 260, "hv_battery_max_range": 418, "trip_distance": 19, "distance_added": 103},
    "metric_ha_imperial_vehicle.json":   {"hv_battery_range": 418, "hv_battery_max_range": 418, "trip_distance": 19, "distance_added": 103},
    "imperial_ha_metric_vehicle.json":   {"hv_battery_range": 418, "hv_battery_max_range": 418, "trip_distance": 19, "distance_added": 103},
    "imperial_ha_imperial_vehicle.json": {"hv_battery_range": 418, "hv_battery_max_range": 418, "trip_distance": 19, "distance_added": 103},
}


@pytest.mark.parametrize("fixture_name,expected", list(MATRIX.items()))
async def test_matrix_fixture_yields_metric_storage(fixture_name, expected, db_session):
    """Skeleton — 29-02 Task 3 wires this up against the real adapter + DB."""
    payload = json.loads((FIXTURES_DIR / fixture_name).read_text())
    # TODO(29-02): iterate payload entities, call process_event(entity_id, state, db_session),
    # then SELECT and assert each expected value with pytest.approx.
    pytest.fail(f"Not yet wired up — {fixture_name} expects {expected}")
