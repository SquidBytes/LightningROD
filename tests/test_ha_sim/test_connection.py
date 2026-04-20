"""Connection, auth, and event delivery tests for the HA WebSocket simulator.
Tests use raw websockets.connect to verify protocol compliance directly,
ensuring the simulator implements the exact same protocol that hass_client.py
expects.
"""

import asyncio
import json

import pytest
import websockets

from tests.test_ha_sim.simulator import HASimulator


pytestmark = pytest.mark.ha_sim


@pytest.mark.asyncio
async def test_auth_success(ha_simulator: HASimulator):
    """Connect with valid token, verify auth_ok and subscribe_events succeeds."""
    async with websockets.connect(ha_simulator.ws_url) as ws:
        # Step 1: Receive auth_required
        msg = json.loads(await ws.recv())
        assert msg["type"] == "auth_required"
        assert msg["ha_version"] == "2024.1.0"

        # Step 2: Send auth with valid token
        await ws.send(json.dumps({
            "type": "auth",
            "access_token": "test-token-valid",
        }))

        # Step 3: Receive auth_ok
        msg = json.loads(await ws.recv())
        assert msg["type"] == "auth_ok"

        # Step 4: get_config
        await ws.send(json.dumps({"type": "get_config", "id": 1}))
        msg = json.loads(await ws.recv())
        assert msg["id"] == 1
        assert msg["success"] is True
        assert "location_name" in msg["result"]

        # Step 5: get_states
        await ws.send(json.dumps({"type": "get_states", "id": 2}))
        msg = json.loads(await ws.recv())
        assert msg["id"] == 2
        assert msg["success"] is True
        assert isinstance(msg["result"], list)

        # Step 6: subscribe_events
        await ws.send(json.dumps({
            "type": "subscribe_events",
            "id": 3,
            "event_type": "state_changed",
        }))
        msg = json.loads(await ws.recv())
        assert msg["id"] == 3
        assert msg["success"] is True


@pytest.mark.asyncio
async def test_auth_failure(ha_simulator: HASimulator):
    """Connect with invalid token, verify auth_invalid and connection closes."""
    async with websockets.connect(ha_simulator.ws_url) as ws:
        # Receive auth_required
        msg = json.loads(await ws.recv())
        assert msg["type"] == "auth_required"

        # Send auth with bad token
        await ws.send(json.dumps({
            "type": "auth",
            "access_token": "bad-token",
        }))

        # Receive auth_invalid
        msg = json.loads(await ws.recv())
        assert msg["type"] == "auth_invalid"
        assert "Invalid" in msg["message"]

        # Connection should close
        with pytest.raises(websockets.exceptions.ConnectionClosed):
            await ws.recv()


@pytest.mark.asyncio
async def test_event_delivery(ha_simulator: HASimulator):
    """Connect, subscribe, inject event, verify client receives it correctly."""
    async with websockets.connect(ha_simulator.ws_url) as ws:
        # Complete auth handshake
        await ws.recv()  # auth_required
        await ws.send(json.dumps({
            "type": "auth",
            "access_token": "test-token-valid",
        }))
        await ws.recv()  # auth_ok

        # Subscribe to events
        await ws.send(json.dumps({
            "type": "subscribe_events",
            "id": 1,
            "event_type": "state_changed",
        }))
        sub_resp = json.loads(await ws.recv())
        assert sub_resp["success"] is True

        # Small delay to ensure subscription is registered
        await asyncio.sleep(0.1)

        # Inject event via simulator
        test_entity = "sensor.fordpass_TESTVIN_soc"
        test_state = {
            "state": "85",
            "attributes": {"batteryRange": 220.0},
        }
        await ha_simulator.inject_event(test_entity, test_state)

        # Receive event with timeout
        raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
        event_msg = json.loads(raw)

        assert event_msg["type"] == "event"
        assert event_msg["id"] == 1  # Matches subscription ID
        event_data = event_msg["event"]
        assert event_data["event_type"] == "state_changed"
        assert event_data["data"]["entity_id"] == test_entity
        assert event_data["data"]["new_state"]["state"] == "85"


@pytest.mark.asyncio
async def test_hass_client_connects_to_simulator():
    """Verify HASSClient can connect to the simulator and complete full handshake.

    Tests the integration between hass_client.py and the simulator by running
    the full connect -> auth -> config -> states -> subscribe sequence, then
    verifying health state and disconnecting cleanly.
    """
    from web.services.hass_client import HASSClient

    events_received = []

    async def event_handler(entity_id, old_state, new_state, ha_config):
        events_received.append(entity_id)

    sim = HASimulator(port=0)
    await sim.start()

    client = HASSClient()
    client_task = asyncio.create_task(
        client.start(sim.ws_url, "test-token-valid", event_handler)
    )

    try:
        # Wait for connection (up to 3s)
        for _ in range(30):
            await asyncio.sleep(0.1)
            if client.health["connected"]:
                break

        assert client.health["connected"], "HASSClient failed to connect to simulator"
        assert client.health["connection_state"] == "connected"

        # Inject an event and verify client receives it via handler
        await asyncio.sleep(0.1)
        await sim.inject_event(
            "sensor.fordpass_TESTVIN_soc",
            {"state": "85", "attributes": {"batteryRange": 220}},
        )
        await asyncio.sleep(0.5)

        assert client.health["events_processed"] >= 1, "No events processed"

    finally:
        await client.stop()
        client_task.cancel()
        try:
            await client_task
        except asyncio.CancelledError:
            pass
        await sim.stop()

    assert client.health["connection_state"] == "disconnected"


@pytest.mark.asyncio
async def test_hass_client_auth_rejected():
    """Verify HASSClient stops when given an invalid token (no reconnect loop)."""
    from web.services.hass_client import HASSClient

    sim = HASimulator(port=0)
    await sim.start()

    client = HASSClient()

    async def noop_handler(entity_id, old_state, new_state, ha_config):
        pass

    # Start with bad token -- should fail auth and stop (no infinite reconnect)
    try:
        await asyncio.wait_for(
            client.start(sim.ws_url, "bad-token", noop_handler),
            timeout=5.0,
        )
    except asyncio.TimeoutError:
        pytest.fail("HASSClient did not stop after auth rejection within 5s")

    assert not client.health["connected"]
    assert client.health["last_error"] == "auth_invalid"

    await sim.stop()
