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


def _clear_shared_state() -> None:
    """Empty every module-level cache the ingestion path accumulates into."""
    from web.services.ingestion.raw_archive import raw_archive
    from web.services.sources.ha_fordpass import adapter
    from web.services.sources.ha_fordpass import handlers as fordpass_handlers

    raw_archive.reset()
    # Tuple-keyed pending dicts are cleared wholesale: dict.clear() removes
    # every entry regardless of config_id, preserving per-test reset semantics.
    for cache in (
        fordpass_handlers._last_trip_values,
        fordpass_handlers._pending_vehicle_status,
        fordpass_handlers._pending_vehicle_status_ts,
        fordpass_handlers._pending_battery_status,
        fordpass_handlers._pending_battery_status_ts,
        adapter._last_seen_raw,
        # The thermal caches are what handle_energy_transfer stamps onto a
        # charging session. Left populated, one test's reading satisfies the
        # next test's temperature assertion.
        adapter._last_charging_battery_temp,
        adapter._last_outsidetemp,
    ):
        cache.clear()


@pytest.fixture(autouse=True)
def clear_processor_state():
    """Clear shared in-memory processor state before and after each test.

    Prevents cross-test leakage from pending status buffers, the raw
    archive's settings/prune caches, and the adapter's last-value caches.
    """
    _clear_shared_state()
    yield
    _clear_shared_state()
