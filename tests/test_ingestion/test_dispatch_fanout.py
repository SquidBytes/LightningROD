"""HAWebSocketRuntime._dispatch fan-out tests.

Asserts that the gas-price adapter is tried first; on a False return, the
slug-based ha_fordpass dispatch runs second; an unknown entity_id is
silently ignored when both branches noop. The config_id propagation
contract (smuggled into ``ha_config["_config_id"]`` by ``dispatch_slug``)
is locked here so the multi-instance pending-state keying stays correct
under N>1 runtimes.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from web.services.ingestion.ha_websocket import HAWebSocketRuntime

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_dispatch_routes_gas_price_first():
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
async def test_dispatch_falls_through_to_slug_when_gas_returns_false():
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
async def test_dispatch_swallows_gas_branch_exceptions_and_continues():
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
async def test_dispatch_swallows_slug_branch_exceptions():
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
