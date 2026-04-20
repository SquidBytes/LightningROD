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
    """Clear shared in-memory processor state before and after each test.

    This prevents cross-test leakage from pending status buffers and the
    adapter's ``_last_seen_raw`` cache.
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
