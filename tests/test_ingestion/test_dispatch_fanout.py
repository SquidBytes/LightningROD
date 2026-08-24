"""HAWebSocketRuntime._dispatch fan-out tests.

Asserts that the raw-event archive is written first; the gas-price adapter
is tried next; on a False return, the slug-based ha_fordpass dispatch runs
last; an unknown entity_id is silently ignored when both branches noop. The
config_id propagation contract (smuggled into ``ha_config["_config_id"]`` by
``dispatch_slug``) is locked here so the multi-instance pending-state keying
stays correct under N>1 runtimes.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from web.services.ingestion.ha_websocket import HAWebSocketRuntime

pytestmark = pytest.mark.unit


@pytest.fixture
def archive_store():
    """Stub the archive writer — the real one would issue an INSERT."""
    with patch(
        "web.services.ingestion.raw_archive.raw_archive.store", new=AsyncMock()
    ) as store_mock:
        yield store_mock


@pytest.mark.asyncio
async def test_dispatch_routes_gas_price_first(archive_store):
    """When ``try_handle_event`` returns True, ``dispatch_slug`` is NOT called."""
    rt = HAWebSocketRuntime(config_id=1, ha_url="http://x", ha_token="t")
    rt._ha_config = {"unit_system": {"length": "mi"}}

    with (
        patch(
            "web.services.sources.ha_gas_price.adapter.try_handle_event",
            new=AsyncMock(return_value=True),
        ) as gas_mock,
        patch(
            "web.services.sources.ha_fordpass.dispatch.dispatch_slug",
            new=AsyncMock(),
        ) as slug_mock,
    ):
        await rt._dispatch("sensor.gas_price_station", {"state": "3.50"})
        gas_mock.assert_awaited_once()
        slug_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_falls_through_to_slug_when_gas_returns_false(archive_store):
    """When ``try_handle_event`` returns False, ``dispatch_slug`` IS called with config_id."""
    rt = HAWebSocketRuntime(config_id=42, ha_url="http://x", ha_token="t")
    rt._ha_config = {"unit_system": {"length": "mi"}}

    with (
        patch(
            "web.services.sources.ha_gas_price.adapter.try_handle_event",
            new=AsyncMock(return_value=False),
        ) as gas_mock,
        patch(
            "web.services.sources.ha_fordpass.dispatch.dispatch_slug",
            new=AsyncMock(),
        ) as slug_mock,
    ):
        await rt._dispatch("sensor.fordpass_VIN_battery", {"state": "85"})
        gas_mock.assert_awaited_once()
        slug_mock.assert_awaited_once()
        # config_id is propagated through dispatch_slug — locks the multi-instance
        # pending-state keying contract.
        _, kwargs = slug_mock.call_args
        assert kwargs.get("config_id") == 42


@pytest.mark.asyncio
async def test_dispatch_swallows_gas_branch_exceptions_and_continues(archive_store):
    """A gas-branch exception triggers rollback + slug fallthrough (event not lost)."""
    rt = HAWebSocketRuntime(config_id=1, ha_url="http://x", ha_token="t")
    rt._ha_config = {"unit_system": {}}

    with (
        patch(
            "web.services.sources.ha_gas_price.adapter.try_handle_event",
            new=AsyncMock(side_effect=RuntimeError("kaboom")),
        ) as gas_mock,
        patch(
            "web.services.sources.ha_fordpass.dispatch.dispatch_slug",
            new=AsyncMock(),
        ) as slug_mock,
    ):
        # Should not propagate the exception out.
        await rt._dispatch("sensor.fordpass_X_y", {"state": "1"})
        gas_mock.assert_awaited_once()
        slug_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_swallows_slug_branch_exceptions(archive_store):
    """A slug-branch exception is caught + logged; runtime keeps consuming events."""
    rt = HAWebSocketRuntime(config_id=1, ha_url="http://x", ha_token="t")
    rt._ha_config = {}

    with (
        patch(
            "web.services.sources.ha_gas_price.adapter.try_handle_event",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "web.services.sources.ha_fordpass.dispatch.dispatch_slug",
            new=AsyncMock(side_effect=RuntimeError("slug-fail")),
        ),
    ):
        # Should not raise.
        await rt._dispatch("sensor.fordpass_X_y", {"state": "1"})


@pytest.mark.asyncio
async def test_dispatch_archives_every_event_with_config_id(archive_store):
    """Each ``_dispatch`` archives the raw event under the runtime's config_id."""
    rt = HAWebSocketRuntime(config_id=7, ha_url="http://x", ha_token="t")

    with (
        patch(
            "web.services.sources.ha_gas_price.adapter.try_handle_event",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "web.services.sources.ha_fordpass.dispatch.dispatch_slug", new=AsyncMock()
        ),
    ):
        await rt._dispatch("sensor.fordpass_VIN_soc", {"state": "85"})

    archive_store.assert_awaited_once()
    args, kwargs = archive_store.call_args
    assert args[0] == "sensor.fordpass_VIN_soc"
    assert kwargs.get("config_id") == 7


@pytest.mark.asyncio
async def test_dispatch_archives_before_the_gas_branch(archive_store):
    """The raw write happens before any typed branch can roll its session back."""
    calls: list[str] = []
    archive_store.side_effect = lambda *a, **k: calls.append("archive")

    rt = HAWebSocketRuntime(config_id=1, ha_url="http://x", ha_token="t")

    with (
        patch(
            "web.services.sources.ha_gas_price.adapter.try_handle_event",
            new=AsyncMock(side_effect=lambda *a, **k: calls.append("gas") or False),
        ),
        patch(
            "web.services.sources.ha_fordpass.dispatch.dispatch_slug",
            new=AsyncMock(side_effect=lambda *a, **k: calls.append("slug")),
        ),
    ):
        await rt._dispatch("sensor.fordpass_VIN_soc", {"state": "85"})

    assert calls == ["archive", "gas", "slug"]


@pytest.mark.asyncio
async def test_dispatch_continues_when_the_archive_itself_raises(archive_store):
    """Belt and braces: even a store that escapes its own guard is contained.

    `_dispatch` runs on the ingestion event loop, so an exception escaping
    here would halt gas, vehicle, battery and trip writes for every event
    that follows.
    """
    archive_store.side_effect = RuntimeError("archive exploded")
    rt = HAWebSocketRuntime(config_id=1, ha_url="http://x", ha_token="t")

    with (
        patch(
            "web.services.sources.ha_gas_price.adapter.try_handle_event",
            new=AsyncMock(return_value=False),
        ) as gas_mock,
        patch(
            "web.services.sources.ha_fordpass.dispatch.dispatch_slug", new=AsyncMock()
        ) as slug_mock,
    ):
        await rt._dispatch("sensor.fordpass_VIN_soc", {"state": "85"})

    gas_mock.assert_awaited_once()
    slug_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_continues_when_the_archive_write_fails():
    """A failing archive write is swallowed; gas and slug dispatch still run.

    Runs the real writer against a session factory that refuses to open, so
    what's under test is the writer's own guard rather than a stub.
    """
    rt = HAWebSocketRuntime(config_id=1, ha_url="http://x", ha_token="t")

    def _no_connection():
        raise RuntimeError("pool exhausted")

    with (
        patch(
            "web.services.ingestion.raw_archive.AsyncSessionLocal", new=_no_connection
        ),
        patch(
            "web.services.sources.ha_gas_price.adapter.try_handle_event",
            new=AsyncMock(return_value=False),
        ) as gas_mock,
        patch(
            "web.services.sources.ha_fordpass.dispatch.dispatch_slug", new=AsyncMock()
        ) as slug_mock,
    ):
        await rt._dispatch("sensor.fordpass_VIN_soc", {"state": "85"})

    gas_mock.assert_awaited_once()
    slug_mock.assert_awaited_once()
