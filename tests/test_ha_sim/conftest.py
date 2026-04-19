"""Fixtures for HA simulator tests."""

import pytest
import pytest_asyncio

from tests.test_ha_sim.simulator import HASimulator


@pytest_asyncio.fixture
async def ha_simulator():
    """Create and start an HA simulator, yield it, then stop."""
    sim = HASimulator()
    await sim.start()
    yield sim
    await sim.stop()


@pytest.fixture(autouse=True)
def clear_processor_state():
    """Clear hass_processor + ha_fordpass adapter module-level state dicts.

    Prevents cross-test contamination from:
    - Accumulated pending status fields and last-seen trip values
      (Pitfall 6 from RESEARCH.md).
    - The ha_fordpass adapter's `_last_seen_raw` cache (D-C3), which
      would otherwise leak state across parametrized matrix tests and
      could mask regressions by showing stale correct values.

    Plan 29-04 Task 2 Step 4: _last_seen_raw clear extended into the
    existing fixture (sibling fixture deferred — single autouse is
    simpler and the cache lives next to the dicts we already clear).
    """
    from web.services import hass_processor

    hass_processor._last_trip_values.clear()
    hass_processor._pending_vehicle_status.clear()
    hass_processor._pending_vehicle_status_ts.clear()
    hass_processor._pending_battery_status.clear()
    hass_processor._pending_battery_status_ts.clear()

    try:
        from web.services.sources.ha_fordpass.adapter import _last_seen_raw
        _last_seen_raw.clear()
    except ImportError:  # pragma: no cover — adapter absent only in legacy test paths
        pass

    yield
    # Also clear after test for good measure
    hass_processor._last_trip_values.clear()
    hass_processor._pending_vehicle_status.clear()
    hass_processor._pending_vehicle_status_ts.clear()
    hass_processor._pending_battery_status.clear()
    hass_processor._pending_battery_status_ts.clear()

    try:
        from web.services.sources.ha_fordpass.adapter import _last_seen_raw
        _last_seen_raw.clear()
    except ImportError:  # pragma: no cover
        pass
