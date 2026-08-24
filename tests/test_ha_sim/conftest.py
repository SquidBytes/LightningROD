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

    This prevents cross-test leakage from pending status buffers, the raw
    archive's settings/prune caches, and the adapter's ``_last_seen_raw``
    cache. Tuple-keyed pending dicts in ha_fordpass.handlers are cleared
    unconditionally — dict.clear() removes every entry regardless of
    config_id, preserving per-test reset semantics.
    """
    from web.services.ingestion.raw_archive import raw_archive
    from web.services.sources.ha_fordpass import handlers as fordpass_handlers

    raw_archive.reset()
    fordpass_handlers._last_trip_values.clear()
    fordpass_handlers._pending_vehicle_status.clear()
    fordpass_handlers._pending_vehicle_status_ts.clear()
    fordpass_handlers._pending_battery_status.clear()
    fordpass_handlers._pending_battery_status_ts.clear()

    try:
        from web.services.sources.ha_fordpass.adapter import _last_seen_raw
        _last_seen_raw.clear()
    except ImportError:  # pragma: no cover — adapter absent only in legacy test paths
        pass

    yield
    # Also clear after test for good measure
    raw_archive.reset()
    fordpass_handlers._last_trip_values.clear()
    fordpass_handlers._pending_vehicle_status.clear()
    fordpass_handlers._pending_vehicle_status_ts.clear()
    fordpass_handlers._pending_battery_status.clear()
    fordpass_handlers._pending_battery_status_ts.clear()

    try:
        from web.services.sources.ha_fordpass.adapter import _last_seen_raw
        _last_seen_raw.clear()
    except ImportError:  # pragma: no cover
        pass
