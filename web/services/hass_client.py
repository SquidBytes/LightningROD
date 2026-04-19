"""Home Assistant WebSocket client service.

Connects to a Home Assistant instance via its WebSocket API, authenticates,
fetches config and state snapshots, and subscribes to state_changed events.
Auto-reconnects with exponential backoff on disconnect.
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

import websockets
from websockets.exceptions import (
    ConnectionClosed,
    ConnectionClosedError,
    WebSocketException,
)

logger = logging.getLogger("lightningrod.hass")

# Pattern to extract VIN from FordPass entity IDs like sensor.fordpass_XXXXX_odometer
_FORDPASS_ENTITY_RE = re.compile(r"^sensor\.fordpass_([a-zA-Z0-9]+)_")


class HASSClient:
    """WebSocket client for Home Assistant state_changed event streaming."""

    def __init__(self) -> None:
        self._ws: Optional[Any] = None
        self._running: bool = False
        self._msg_id: int = 0
        self._ha_config: Optional[dict] = None
        self._entity_states: dict[str, dict] = {}
        self._event_handler: Optional[Callable] = None
        self._task: Optional[asyncio.Task] = None
        self.detected_vin: Optional[str] = None
        self._health: dict[str, Any] = {
            "connected": False,
            "last_event_at": None,
            "events_processed": 0,
            "errors": 0,
            "last_error": None,
            "last_successful_write": None,
            "connection_state": "disconnected",
        }

    @property
    def health(self) -> dict[str, Any]:
        """Return a copy of the current health metrics."""
        return dict(self._health)

    def _next_msg_id(self) -> int:
        """Increment and return the next message ID."""
        self._msg_id += 1
        return self._msg_id

    async def start(
        self,
        ha_url: str,
        ha_token: str,
        event_handler: Callable,
    ) -> None:
        """Start the client: connect, authenticate, subscribe, and enter event loop.

        Reconnects automatically on failure (except auth errors).
        """
        self._running = True
        self._event_handler = event_handler
        logger.info("HASS client starting, target: %s", ha_url)

        while self._running:
            try:
                await self._connect_and_subscribe(ha_url, ha_token)
                await self._event_loop()
            except _AuthInvalid:
                logger.error("HA authentication failed -- bad token. Not reconnecting.")
                self._health["last_error"] = "auth_invalid"
                self._health["errors"] += 1
                self._health["connection_state"] = "disconnected"
                self._health["connected"] = False
                self._running = False
                break
            except (
                ConnectionClosed,
                ConnectionClosedError,
                ConnectionError,
                OSError,
                WebSocketException,
                asyncio.TimeoutError,
            ) as exc:
                self._health["connected"] = False
                self._health["connection_state"] = "reconnecting"
                self._health["errors"] += 1
                self._health["last_error"] = str(exc)
                logger.warning("HA connection lost: %s", exc)
                if self._running:
                    await self._reconnect_loop(ha_url, ha_token)
            except asyncio.CancelledError:
                logger.info("HASS client cancelled")
                break
            except Exception as exc:
                self._health["errors"] += 1
                self._health["last_error"] = str(exc)
                logger.exception("Unexpected error in HASS client: %s", exc)
                if self._running:
                    await self._reconnect_loop(ha_url, ha_token)

        await self._close_ws()
        logger.info("HASS client stopped")

    async def stop(self) -> None:
        """Stop the client gracefully."""
        logger.info("HASS client stopping")
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._close_ws()
        self._health["connected"] = False
        self._health["connection_state"] = "disconnected"

    async def _close_ws(self) -> None:
        """Close the websocket connection if open."""
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def _connect_and_subscribe(self, ha_url: str, ha_token: str) -> None:
        """Execute the full HA websocket handshake and subscription sequence."""
        self._health["connection_state"] = "connecting"
        self._msg_id = 0

        # Build websocket URL
        ws_url = ha_url.rstrip("/")
        if ws_url.startswith("http://"):
            ws_url = "ws://" + ws_url[7:]
        elif ws_url.startswith("https://"):
            ws_url = "wss://" + ws_url[8:]
        elif not ws_url.startswith(("ws://", "wss://")):
            ws_url = "ws://" + ws_url
        ws_url = ws_url + "/api/websocket"

        logger.info("Connecting to %s", ws_url)
        self._ws = await websockets.connect(ws_url, max_size=16 * 1024 * 1024)  # 16MB - This can be made larger or set to NONE if we really want it that way... 

        # Step 1: Receive auth_required
        msg = await self._recv_json()
        if msg.get("type") != "auth_required":
            raise ConnectionError(f"Expected auth_required, got: {msg.get('type')}")

        # Step 2: Send auth
        await self._send_json({"type": "auth", "access_token": ha_token})

        # Step 3: Receive auth_ok or auth_invalid
        msg = await self._recv_json()
        if msg.get("type") == "auth_invalid":
            raise _AuthInvalid(msg.get("message", "Authentication failed"))
        if msg.get("type") != "auth_ok":
            raise ConnectionError(f"Expected auth_ok, got: {msg.get('type')}")

        logger.info("HA authentication successful (version: %s)", msg.get("ha_version"))

        # Step 4: get_config
        config_id = self._next_msg_id()
        await self._send_json({"type": "get_config", "id": config_id})
        config_msg = await self._recv_json()
        if config_msg.get("success"):
            self._ha_config = config_msg.get("result", {})
            unit_system = self._ha_config.get("unit_system", {})
            logger.info(
                "HA config loaded: unit_system=%s, location=%s",
                unit_system.get("length", "unknown"),
                self._ha_config.get("location_name", "unknown"),
            )
            # Auto-populate the Home location from HA config (latitude/longitude
            # live on get_config). Non-destructive: only fills in values that
            # haven't been manually set in app_settings.
            await self._autopopulate_home_location_from_config()

        # Step 5: get_states
        states_id = self._next_msg_id()
        await self._send_json({"type": "get_states", "id": states_id})
        states_msg = await self._recv_json()
        if states_msg.get("success"):
            states = states_msg.get("result", [])
            self._entity_states = {s["entity_id"]: s for s in states}
            logger.info("Loaded %d entity states from HA", len(self._entity_states))
            self._detect_vin()

            # Phase 29 D-A4: the elveh/outsidetemp startup probe that used to
            # stash FordPass preferred distance + temperature units on ha_config
            # has been DELETED. Unit handling now lives entirely in
            # web/services/sources/ha_fordpass/adapter.py via FIELD_CONTRACTS +
            # per-event UoM lookup (D-B3).

        # Step 6: Process initial snapshot through event handler
        # This captures current state (e.g. last energytransferlogentry) as DB records
        if self._event_handler and self._entity_states:
            snapshot_count = 0
            for entity_id, state_obj in self._entity_states.items():
                if not entity_id.startswith("sensor.fordpass_"):
                    continue
                try:
                    await self._event_handler(entity_id, {}, state_obj, self._ha_config or {})
                    snapshot_count += 1
                except Exception as exc:
                    logger.error("Snapshot processing error for %s: %s", entity_id, exc)
            logger.info("Processed %d FordPass entities from initial snapshot", snapshot_count)

        # Step 7: subscribe to state_changed
        sub_id = self._next_msg_id()
        await self._send_json({
            "type": "subscribe_events",
            "id": sub_id,
            "event_type": "state_changed",
        })
        sub_msg = await self._recv_json()
        if sub_msg.get("success"):
            logger.info("Subscribed to state_changed events (subscription id=%d)", sub_id)
        else:
            logger.warning("Failed to subscribe to events: %s", sub_msg)

        self._health["connected"] = True
        self._health["connection_state"] = "connected"
        self._health["last_error"] = None  
        logger.info("HASS client fully connected and subscribed")

    async def _event_loop(self) -> None:
        """Read messages from websocket, dispatch state_changed events to handler."""
        while self._running and self._ws is not None:
            msg = await self._recv_json()
            msg_type = msg.get("type")

            if msg_type == "event":
                event_data = msg.get("event", {})
                if event_data.get("event_type") == "state_changed":
                    data = event_data.get("data", {})
                    entity_id = data.get("entity_id", "")
                    old_state = data.get("old_state", {})
                    new_state = data.get("new_state", {})

                    # Update local state cache
                    if new_state:
                        self._entity_states[entity_id] = new_state

                    self._health["events_processed"] += 1
                    self._health["last_event_at"] = datetime.now(timezone.utc).isoformat()

                    # Dispatch to handler
                    if self._event_handler is not None:
                        try:
                            await self._event_handler(
                                entity_id, old_state, new_state, self._ha_config or {}
                            )
                        except Exception as exc:
                            logger.error(
                                "Event handler error for %s: %s", entity_id, exc
                            )

            elif msg_type == "result":
                # Response to a command -- ignore for now
                pass
            elif msg_type == "pong":
                pass
            else:
                logger.debug("Unhandled message type: %s", msg_type)

    async def _reconnect_loop(self, ha_url: str, ha_token: str) -> None:
        """Exponential backoff reconnection: 1s, 2s, 4s, ... max 60s."""
        delay = 1
        max_delay = 60
        while self._running:
            logger.info("Reconnecting in %ds...", delay)
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return
            if not self._running:
                return
            try:
                await self._close_ws()
                await self._connect_and_subscribe(ha_url, ha_token)
                logger.info("Reconnected successfully")
                return
            except _AuthInvalid:
                raise
            except Exception as exc:
                self._health["errors"] += 1
                self._health["last_error"] = str(exc)
                logger.warning("Reconnect failed: %s", exc)
                delay = min(delay * 2, max_delay)

    def _detect_vin(self) -> None:
        """Scan entity IDs for FordPass pattern to auto-detect VIN."""
        for entity_id in self._entity_states:
            match = _FORDPASS_ENTITY_RE.match(entity_id)
            if match:
                self.detected_vin = match.group(1)
                logger.info("Auto-detected VIN from entity: %s", entity_id)
                return
        logger.debug("No FordPass entities found for VIN detection")

    async def _send_json(self, data: dict) -> None:
        """Send a JSON message over the websocket."""
        await self._ws.send(json.dumps(data))

    async def _recv_json(self) -> dict:
        """Receive and parse a JSON message from the websocket."""
        raw = await self._ws.recv()
        return json.loads(raw)


    async def _autopopulate_home_location_from_config(self) -> None:
        """Fill home_latitude/home_longitude/home_location_name from HA config.

        Runs on every successful connect. Non-destructive: only writes an
        app_settings key when the current value is empty. Users who manually
        override these via the settings form are not stomped on.
        """
        await self._apply_home_location_from_config(force=False)

    async def sync_home_location_from_config(self) -> dict:
        """Force-overwrite the home location from HA config.

        Returns a dict with the values written (or empty dict if HA config
        didn't carry coordinates). Used by the 'Use HA location' button on
        the settings page.
        """
        return await self._apply_home_location_from_config(force=True)

    async def _apply_home_location_from_config(self, *, force: bool) -> dict:
        if not self._ha_config:
            return {}

        lat = self._ha_config.get("latitude")
        lon = self._ha_config.get("longitude")
        name = self._ha_config.get("location_name")
        if lat is None or lon is None:
            return {}

        from db.engine import AsyncSessionLocal
        from web.queries.settings import get_app_settings_dict, set_app_setting

        applied: dict = {}
        async with AsyncSessionLocal() as db:
            existing = await get_app_settings_dict(
                db,
                ["home_latitude", "home_longitude", "home_location_name"],
            )

            pairs = [
                ("home_latitude", str(lat)),
                ("home_longitude", str(lon)),
                ("home_location_name", name or "Home"),
            ]
            for key, value in pairs:
                current = (existing.get(key) or "").strip()
                if force or not current:
                    await set_app_setting(db, key, value)
                    applied[key] = value

        if applied:
            logger.info(
                "Home location %s from HA config: %s",
                "overwritten" if force else "auto-populated",
                applied,
            )
        return applied

    async def _ha_rest_headers(self) -> Optional[dict]:
        """Load ha_url and ha_token from settings and return request headers+base.

        Returns (ha_url, headers) tuple or None when credentials are missing.
        """
        from db.engine import AsyncSessionLocal
        from web.queries.settings import get_app_settings_dict

        async with AsyncSessionLocal() as db:
            cfg = await get_app_settings_dict(db, ["ha_url", "ha_token"])

        ha_url = cfg.get("ha_url", "").rstrip("/")
        ha_token = cfg.get("ha_token", "")
        if not ha_url or not ha_token:
            return None
        return (ha_url, {"Authorization": f"Bearer {ha_token}"})

    async def fetch_entity_state(self, entity_id: str) -> Optional[dict]:
        """Fetch the current state object for a single HA entity via REST.

        Returns the state dict as HA would return from /api/states/<entity_id>,
        or None on connection/credential failure or 404. Used by the gas sensor
        "check data" button on the HASS settings page to verify that a sensor
        is wired up correctly before ingesting.
        """
        import httpx

        auth = await self._ha_rest_headers()
        if auth is None:
            return None
        ha_url, headers = auth

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{ha_url}/api/states/{entity_id}", headers=headers
                )
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.warning("fetch_entity_state(%s) failed: %s", entity_id, exc)
            return None

    async def _fetch_entity_history(
        self,
        entity_id: str,
        start_time_iso: Optional[str] = None,
    ) -> list[dict]:
        """Fetch HA history for a single entity_id.

        When start_time_iso is None, queries from 10 years ago (effectively
        "everything HA will return"). HA's recorder retention is typically
        10 days by default but users with long-term storage can have years.

        Returns the list of state dicts, or [] on failure / empty history.
        """
        import httpx

        auth = await self._ha_rest_headers()
        if auth is None:
            logger.warning("Backfill: missing ha_url or ha_token")
            return []
        ha_url, headers = auth

        if start_time_iso is None:
            # Default to ~10 years ago — HA will cap at its own recorder retention.
            start_time_iso = (
                datetime.now(timezone.utc) - timedelta(days=365 * 10)
            ).isoformat()

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.get(
                    f"{ha_url}/api/history/period/{start_time_iso}",
                    params={"filter_entity_id": entity_id, "minimal_response": "false"},
                    headers=headers,
                )
                resp.raise_for_status()
                history_data = resp.json()
        except Exception as exc:
            logger.error("Backfill: failed to fetch history for %s: %s", entity_id, exc)
            return []

        if not history_data or not history_data[0]:
            return []

        # history_data is [[state, state, ...]] — one list per filter_entity_id
        return history_data[0]

    async def backfill_history(self, days: Optional[int] = None) -> dict:
        """Backfill historical data from HA REST for both charging and gas sensors.

        Pulls past state changes for:
          * the energytransferlogentry entity (charging sessions)
          * any gas sensors configured in app_settings (station / average)

        When `days` is None the start time is set to 10 years ago, letting HA
        return as much history as its recorder retains. Pass an int to cap.

        Duplicate prevention:
          * Charging sessions: reuses the existing +/-30 min +/- 10% energy fuzzy
            match inside handle_energy_transfer (no changes needed here).
          * Gas readings: uses store_gas_price_reading_if_new which checks
            (entity_id, recorded_at) before inserting.

        Returns:
            {
              "sessions": {"processed": N, "errors": N},
              "gas": {entity_id: {"inserted": N, "skipped": N, "errors": N}},
            }
        """
        if not self._ha_config:
            return {
                "error": "Not connected to HA",
                "sessions": {"processed": 0, "errors": 0},
                "gas": {},
            }

        start_time_iso: Optional[str] = None
        if days is not None:
            start_time_iso = (
                datetime.now(timezone.utc) - timedelta(days=days)
            ).isoformat()

        result: dict = {
            "sessions": {"processed": 0, "errors": 0},
            "gas": {},
        }

        # --- 1. Charging sessions ---
        vin = self.detected_vin or "unknown"
        energy_entity = f"sensor.fordpass_{vin}_energytransferlogentry"
        logger.info(
            "Backfill: fetching charging session history for %s (start=%s)",
            energy_entity, start_time_iso or "all",
        )
        session_states = await self._fetch_entity_history(energy_entity, start_time_iso)
        for state_obj in session_states:
            if not state_obj.get("attributes"):
                continue
            try:
                await self._event_handler(
                    energy_entity, {}, state_obj, self._ha_config or {}
                )
                result["sessions"]["processed"] += 1
            except Exception as exc:
                logger.error("Backfill: session state error: %s", exc)
                result["sessions"]["errors"] += 1

        # --- 2. Gas sensors ---
        gas_entities = await self._load_gas_sensor_entity_ids()
        for entity_id in gas_entities:
            counts = {"inserted": 0, "skipped": 0, "errors": 0}
            result["gas"][entity_id] = counts
            logger.info(
                "Backfill: fetching gas sensor history for %s (start=%s)",
                entity_id, start_time_iso or "all",
            )
            gas_states = await self._fetch_entity_history(entity_id, start_time_iso)
            for state_obj in gas_states:
                try:
                    inserted = await self._ingest_gas_history_state(entity_id, state_obj)
                    if inserted:
                        counts["inserted"] += 1
                    else:
                        counts["skipped"] += 1
                except Exception as exc:
                    logger.error(
                        "Backfill: gas reading error for %s: %s", entity_id, exc
                    )
                    counts["errors"] += 1

            # After inserting readings, refresh monthly averages in history table.
            if counts["inserted"] > 0:
                await self._refresh_gas_monthly_history(entity_id)

        logger.info(
            "Backfill complete: sessions=%s, gas=%s",
            result["sessions"], result["gas"],
        )
        return result

    async def _load_gas_sensor_entity_ids(self) -> list[str]:
        """Return the list of configured gas sensor entity IDs (station + average)."""
        from db.engine import AsyncSessionLocal
        from web.queries.settings import get_app_settings_dict

        async with AsyncSessionLocal() as db:
            cfg = await get_app_settings_dict(
                db,
                ["gas_sensor_station_entity_id", "gas_sensor_average_entity_id"],
            )

        entities = []
        for key in ("gas_sensor_station_entity_id", "gas_sensor_average_entity_id"):
            val = (cfg.get(key) or "").strip()
            if val:
                entities.append(val)
        return entities

    async def _ingest_gas_history_state(self, entity_id: str, state_obj: dict) -> bool:
        """Insert a single historical gas sensor state as a GasPriceReading.

        Returns True if a new row was inserted, False if skipped (duplicate,
        invalid value, or unavailable state).
        """
        state_val = state_obj.get("state")
        if state_val is None or state_val in ("unknown", "unavailable", ""):
            return False
        try:
            price = float(state_val)
        except (TypeError, ValueError):
            return False
        if price <= 0:
            return False

        # HA history states carry last_changed / last_updated as ISO strings.
        ts_raw = state_obj.get("last_changed") or state_obj.get("last_updated")
        if not ts_raw:
            return False
        try:
            if isinstance(ts_raw, str):
                if ts_raw.endswith("Z"):
                    ts_raw = ts_raw[:-1] + "+00:00"
                recorded_at = datetime.fromisoformat(ts_raw)
            else:
                recorded_at = ts_raw
        except (TypeError, ValueError):
            return False

        from db.engine import AsyncSessionLocal
        from web.queries.gas_prices import store_gas_price_reading_if_new

        async with AsyncSessionLocal() as db:
            return await store_gas_price_reading_if_new(
                db, entity_id, price, recorded_at
            )

    async def _refresh_gas_monthly_history(self, entity_id: str) -> None:
        """Recompute monthly averages for a gas sensor and upsert into history."""
        from db.engine import AsyncSessionLocal
        from web.queries.gas_prices import (
            compute_monthly_averages,
            upsert_gas_price,
        )
        from web.queries.settings import get_app_settings_dict

        async with AsyncSessionLocal() as db:
            settings = await get_app_settings_dict(
                db,
                ["gas_sensor_station_entity_id", "gas_sensor_average_entity_id"],
            )
            station_entity = settings.get("gas_sensor_station_entity_id") or ""
            average_entity = settings.get("gas_sensor_average_entity_id") or ""

            months = await compute_monthly_averages(db, entity_id)
            for (year, month), avg in months.items():
                if entity_id == station_entity:
                    await upsert_gas_price(
                        db, year, month, station_price=avg, source="ha_sensor"
                    )
                elif entity_id == average_entity:
                    await upsert_gas_price(
                        db, year, month, average_price=avg, source="ha_sensor"
                    )


class _AuthInvalid(Exception):
    """Internal exception for HA auth_invalid responses."""
    pass


# Module-level singleton -- accessed by routes for status
hass_service = HASSClient()


async def _noop_handler(
    entity_id: str, old_state: dict, new_state: dict, ha_config: dict
) -> None:
    """Placeholder event handler. Replaced by sensor processor in Plan 03."""
    pass


async def start_hass_service() -> None:
    """Start the HASS service if ha_url, ha_token, and ha_auto_connect are configured.

    Reads settings from app_settings table and launches the client as a background task.
    """
    from db.engine import AsyncSessionLocal
    from web.queries.settings import get_app_settings_dict

    async with AsyncSessionLocal() as db:
        cfg = await get_app_settings_dict(
            db, ["ha_url", "ha_token", "ha_auto_connect"]
        )

    ha_url = cfg.get("ha_url", "").strip()
    ha_token = cfg.get("ha_token", "").strip()
    ha_auto_connect = cfg.get("ha_auto_connect", "").strip().lower()

    if ha_auto_connect != "true":
        logger.info("HASS auto-connect disabled, skipping service start")
        return

    if not ha_url or not ha_token:
        logger.warning("HASS auto-connect enabled but ha_url or ha_token not set, skipping")
        return

    from web.services.hass_processor import process_state_change

    logger.info("Starting HASS service (auto-connect enabled)")
    task = asyncio.create_task(
        hass_service.start(ha_url, ha_token, process_state_change),
        name="hass-client",
    )
    hass_service._task = task
