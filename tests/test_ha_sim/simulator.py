"""Home Assistant WebSocket simulator for integration testing.

Implements the HA WebSocket protocol (auth, config, states, subscribe_events)
so that hass_client.py can connect to it as if it were a real HA instance.
Supports event injection for end-to-end ingestion pipeline testing.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime

import websockets
from websockets.asyncio.server import ServerConnection, serve

logger = logging.getLogger("lightningrod.test.ha_sim")


class HASimulator:
    """Standalone asyncio WebSocket server speaking the HA protocol.
    Usage::
    sim = HASimulator
    await sim.start
    print(f"ws://localhost:{sim.port}")
    await sim.inject_event("sensor.fordpass_TESTVIN_soc", {"state": "80"})
    await sim.stop
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 0,
        valid_token: str = "test-token-valid",
    ) -> None:
        self._host = host
        self._port = port  # 0 = OS-assigned free port
        self._valid_token = valid_token
        self._server = None
        self._actual_port: int | None = None

        # Event injection queue and subscribed client tracking
        self._events_queue: asyncio.Queue = asyncio.Queue()
        self._subscribed_clients: list[_SubscribedClient] = []
        self._dispatch_task: asyncio.Task | None = None

        # Pre-configured entity states returned by get_states
        self._entity_states: list[dict] = []

        # HA config returned by get_config
        self._ha_config: dict = {
            "location_name": "Test Home",
            "time_zone": "America/New_York",
            "unit_system": {
                "length": "mi",
                "mass": "lb",
                "temperature": "\u00b0F",
                "volume": "gal",
            },
            "version": "2024.1.0",
            "components": ["fordpass"],
        }

    @property
    def port(self) -> int:
        """Return the actual assigned port (after start)."""
        if self._actual_port is None:
            raise RuntimeError("Simulator not started yet")
        return self._actual_port

    @property
    def ws_url(self) -> str:
        """Return the WebSocket URL for connecting."""
        return f"ws://{self._host}:{self.port}"

    async def start(self) -> None:
        """Start the WebSocket server and event dispatch loop."""
        self._server = await serve(
            self._handle_client,
            self._host,
            self._port,
            ping_interval=None,
            ping_timeout=None,
        )
        # Read the actual port from the server socket
        for sock in self._server.sockets:
            addr = sock.getsockname()
            self._actual_port = addr[1]
            break

        # Start event dispatch loop
        self._dispatch_task = asyncio.create_task(self._dispatch_events())
        logger.info("HA Simulator started on port %d", self._actual_port)

    async def stop(self) -> None:
        """Stop the WebSocket server and cleanup."""
        if self._dispatch_task and not self._dispatch_task.done():
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass

        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        self._subscribed_clients.clear()
        self._actual_port = None
        logger.info("HA Simulator stopped")

    async def inject_event(
        self,
        entity_id: str,
        new_state: dict,
        old_state: dict | None = None,
    ) -> None:
        """Queue a state_changed event to send to all subscribed clients."""
        event = {
            "entity_id": entity_id,
            "new_state": new_state,
            "old_state": old_state or {},
        }
        await self._events_queue.put(event)

    async def run_scenario(
        self,
        events: list[tuple[str, dict]],
        interval: float = 0.5,
    ) -> None:
        """Inject a list of events with configurable delay between them.

        Args:
            events: List of (entity_id, new_state) tuples.
            interval: Seconds between event injections.
        """
        for entity_id, new_state in events:
            await self.inject_event(entity_id, new_state)
            await asyncio.sleep(interval)

    def set_entity_states(self, states: list[dict]) -> None:
        """Pre-configure entity states returned by get_states."""
        self._entity_states = states

    async def _handle_client(self, ws: ServerConnection) -> None:
        """Handle a single WebSocket client connection with HA protocol."""
        try:
            # Step 1: Send auth_required
            await ws.send(json.dumps({
                "type": "auth_required",
                "ha_version": "2024.1.0",
            }))

            # Step 2: Receive auth message
            raw = await ws.recv()
            auth_msg = json.loads(raw)

            if auth_msg.get("type") != "auth":
                await ws.close()
                return

            # Step 3: Validate token
            if auth_msg.get("access_token") != self._valid_token:
                await ws.send(json.dumps({
                    "type": "auth_invalid",
                    "message": "Invalid access token",
                }))
                await ws.close()
                return

            # Step 4: Send auth_ok
            await ws.send(json.dumps({
                "type": "auth_ok",
                "ha_version": "2024.1.0",
            }))

            # Step 5: Enter message loop
            async for raw_msg in ws:
                msg = json.loads(raw_msg)
                msg_type = msg.get("type")
                msg_id = msg.get("id")

                if msg_type == "get_config":
                    await ws.send(json.dumps({
                        "id": msg_id,
                        "type": "result",
                        "success": True,
                        "result": self._ha_config,
                    }))

                elif msg_type == "get_states":
                    await ws.send(json.dumps({
                        "id": msg_id,
                        "type": "result",
                        "success": True,
                        "result": self._entity_states,
                    }))

                elif msg_type == "subscribe_events":
                    await ws.send(json.dumps({
                        "id": msg_id,
                        "type": "result",
                        "success": True,
                        "result": None,
                    }))
                    # Track this client as subscribed with its subscription ID
                    self._subscribed_clients.append(
                        _SubscribedClient(ws=ws, subscription_id=msg_id)
                    )

                elif msg_type == "ping":
                    await ws.send(json.dumps({
                        "id": msg_id,
                        "type": "pong",
                    }))

                else:
                    # Unknown command -- respond with success
                    await ws.send(json.dumps({
                        "id": msg_id,
                        "type": "result",
                        "success": True,
                        "result": None,
                    }))

        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as exc:
            logger.debug("Client handler error: %s", exc)
        finally:
            # Remove from subscribed clients on disconnect
            self._subscribed_clients = [
                sc for sc in self._subscribed_clients if sc.ws != ws
            ]

    async def _dispatch_events(self) -> None:
        """Pull events from the queue and send to all subscribed clients."""
        while True:
            try:
                event_data = await self._events_queue.get()
                entity_id = event_data["entity_id"]
                new_state = event_data["new_state"]
                old_state = event_data.get("old_state", {})

                # Send to all subscribed clients
                dead_clients = []
                for sc in self._subscribed_clients:
                    envelope = {
                        "id": sc.subscription_id,
                        "type": "event",
                        "event": {
                            "event_type": "state_changed",
                            "data": {
                                "entity_id": entity_id,
                                "new_state": new_state,
                                "old_state": old_state,
                            },
                        },
                    }
                    try:
                        await sc.ws.send(json.dumps(envelope))
                    except websockets.exceptions.ConnectionClosed:
                        dead_clients.append(sc)

                # Clean up dead clients
                for dc in dead_clients:
                    self._subscribed_clients.remove(dc)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("Dispatch error: %s", exc)


class _SubscribedClient:
    """Tracks a subscribed WebSocket client and its subscription ID."""

    __slots__ = ("ws", "subscription_id")

    def __init__(self, ws: ServerConnection, subscription_id: int):
        self.ws = ws
        self.subscription_id = subscription_id


# ---------------------------------------------------------------------------
# Event helper functions for generating realistic FordPass sensor events
# ---------------------------------------------------------------------------

_DEFAULT_DEVICE_ID = "TESTVIN001"


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(UTC).isoformat()


def make_charging_session_event(
    device_id: str = _DEFAULT_DEVICE_ID,
    energy_kwh: float = 25.5,
    charge_type: str = "DC_FAST",
    network_name: str = "Electrify America",
    start_soc: float = 20.0,
    end_soc: float = 80.0,
    duration_seconds: float = 1800.0,
    latitude: float | None = 38.9072,
    longitude: float | None = -77.0369,
    address: str | None = "123 Test St",
    city: str | None = "Washington",
    state: str | None = "DC",
    max_power_w: float = 150000.0,
    battery_temp_c: float | None = None,
    outside_temp_c: float | None = None,
) -> tuple[str, dict]:
    """Generate an energytransferlogentry event.
    Returns (entity_id, new_state) tuple matching what hass_processor expects.
    ``battery_temp_c`` / ``outside_temp_c`` inject the thermal
    context fields (°C — matches real ha-fordpass payload semantics per
    audit) at the top of the attrs dict. Omit them to test
    the "payload without temp keys" path.
    """
    entity_id = f"sensor.fordpass_{device_id}_energytransferlogentry"
    now = _now_iso()
    attrs: dict = {
        "energyConsumed": energy_kwh,
        "chargerType": charge_type,
        "energyTransferDuration": {
            "begin": now,
            "end": now,
            "totalTime": duration_seconds,
        },
        "plugDetails": {
            "totalPluggedInTime": duration_seconds + 120,
            "totalDistanceAdded": 80.0,
        },
        "stateOfCharge": {
            "firstSOC": start_soc,
            "lastSOC": end_soc,
        },
        "power": {
            "max": max_power_w,
            "min": 5000.0,
            "weightedAverage": max_power_w * 0.7,
        },
        "location": {
            "name": network_name,
            "network": network_name,
            "latitude": latitude,
            "longitude": longitude,
            "address": {
                "address1": address,
                "city": city,
                "state": state,
            },
        },
        "timeStamp": now,
    }
    if battery_temp_c is not None:
        attrs["batteryTemperature"] = battery_temp_c
    if outside_temp_c is not None:
        attrs["outsidetemp"] = outside_temp_c
    new_state = {
        "state": "complete",
        "last_changed": now,
        "last_updated": now,
        "attributes": attrs,
    }
    return entity_id, new_state


def make_trip_event(
    device_id: str = _DEFAULT_DEVICE_ID,
    distance_miles: float = 15.5,
    duration_minutes: float = 25.0,
    efficiency: float = 3.2,
    energy_consumed: float = 4.8,
    driving_score: float = 85.0,
) -> tuple[str, dict]:
    """Generate an elveh event with trip attributes.

    Returns (entity_id, new_state) matching hass_processor's elveh branch.
    """
    entity_id = f"sensor.fordpass_{device_id}_elveh"
    now = _now_iso()
    new_state = {
        "state": str(round(distance_miles * 3.0, 1)),  # Approx range in miles
        "last_changed": now,
        "last_updated": now,
        "attributes": {
            # Production HA emits unit_of_measurement for the elveh state.
            # Detection resolver requires a signal; no more silent "mi" default.
            "unit_of_measurement": "mi",
            "batteryVoltage": 390.0,
            "batteryAmperage": 5.0,
            "batterykW": 1.95,
            "maximumBatteryCapacity": 91.0,
            "batteryActualCharge": 75.0,
            "motorVoltage": 350.0,
            "motorAmperage": 3.0,
            "motorkW": 1.05,
            "maximumBatteryRange": 250.0,
            # Trip attributes
            "tripDistanceTraveled": distance_miles,
            "tripDuration": duration_minutes,
            "tripEnergyConsumed": energy_consumed,
            "tripEfficiency": efficiency,
            "tripDrivingScore": driving_score,
            "tripSpeed": 80.0,
            "tripAcceleration": 75.0,
            "tripDeceleration": 70.0,
            "tripAmbientTemp": 72.0,
            "tripOutsideAirAmbientTemp": 68.0,
            "tripCabinTemp": 70.0,
            "tripRangeRegenerated": 2.5,
            "tripElectricalEfficiency": 3.1,
        },
    }
    return entity_id, new_state


def make_events_trip_event(
    device_id: str = _DEFAULT_DEVICE_ID,
    distance_km: float = 24.9,
    energy_wh: float = 7200.0,
    duration_seconds: float = 1500.0,
    ambient_temp_c: float = 18.0,
    cabin_temp_c: float = 21.0,
    outside_air_temp_c: float = 17.0,
) -> tuple[str, dict]:
    """Generate a sensor.fordpass_{vin}_events event with trip segment data.

    Returns (entity_id, new_state) matching adapter._handle_events_entity.
    distance_km and energy_wh use the canonical units that the FIELD_CONTRACTS
    specify (km passthrough, Wh -> kWh conversion).
    """
    entity_id = f"sensor.fordpass_{device_id}_events"
    now = _now_iso()
    new_state = {
        "state": "key_off",
        "last_changed": now,
        "last_updated": now,
        "attributes": {
            "xev-key-off-trip-segment-data": {
                "distance_traveled": distance_km,
                "energy_consumed": energy_wh,
                "trip_duration": duration_seconds,
                # canonical field names per FIELD_CONTRACTS
                "ambient_temperature": ambient_temp_c,
                "cabin_temperature": cabin_temp_c,
                "outside_air_ambient_temperature": outside_air_temp_c,
                # also include the keys the _handle_events_entity code reads
                # (lookup keys don't match contracts — kept for completeness)
                "ambient_temp": ambient_temp_c,
                "cabin_temp": cabin_temp_c,
                "outside_air_temp": outside_air_temp_c,
            }
        },
    }
    return entity_id, new_state


def make_battery_event(
    device_id: str = _DEFAULT_DEVICE_ID,
    soc: float = 80.0,
    battery_range_miles: float = 200.0,
    hv_voltage: float = 390.0,
    hv_amperage: float = 5.0,
    hv_kw: float = 1.95,
) -> tuple[str, dict]:
    """Generate a battery status event (via elveh entity with battery attrs).

    Returns (entity_id, new_state) tuple.
    """
    entity_id = f"sensor.fordpass_{device_id}_soc"
    now = _now_iso()
    new_state = {
        "state": str(soc),
        "last_changed": now,
        "last_updated": now,
        "attributes": {
            "batteryRange": battery_range_miles,
        },
    }
    return entity_id, new_state


def make_gps_event(
    device_id: str = _DEFAULT_DEVICE_ID,
    lat: float = 38.9072,
    lon: float = -77.0369,
    accuracy: float = 10.0,
) -> tuple[str, dict]:
    """Generate a GPS location event.

    Returns (entity_id, new_state) matching hass_processor's gps handler.
    The GPS handler reads attrs.value.location for lat/lon.
    """
    entity_id = f"sensor.fordpass_{device_id}_gps"
    now = _now_iso()
    new_state = {
        "state": "home",
        "last_changed": now,
        "last_updated": now,
        "attributes": {
            "value": {
                "location": {
                    "lat": lat,
                    "lon": lon,
                    "accuracy": accuracy,
                },
            },
        },
    }
    return entity_id, new_state


def make_temperature_event(
    device_id: str = _DEFAULT_DEVICE_ID,
    temp_f: float = 72.0,
) -> tuple[str, dict]:
    """Generate a cabin temperature event.

    Returns (entity_id, new_state) tuple.
    """
    entity_id = f"sensor.fordpass_{device_id}_cabintemperature"
    now = _now_iso()
    new_state = {
        "state": str(temp_f),
        "last_changed": now,
        "last_updated": now,
        "attributes": {},
    }
    return entity_id, new_state


def make_lastrefresh_event(
    device_id: str = _DEFAULT_DEVICE_ID,
) -> tuple[str, dict]:
    """Generate a lastrefresh event to trigger vehicle/battery status flush."""
    entity_id = f"sensor.fordpass_{device_id}_lastrefresh"
    now = _now_iso()
    new_state = {
        "state": now,
        "last_changed": now,
        "last_updated": now,
        "attributes": {},
    }
    return entity_id, new_state
