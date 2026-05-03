"""HA WebSocket runtime — connects, authenticates, subscribes to state_changed,
and fans out events through the supervisor-managed dispatch chain.

Refactor of the legacy `web/services/hass_client.py:HASSClient`. Every
method body is preserved with the following deltas:

  - Constructor takes ``config_id``, ``ha_url``, ``ha_token`` (plus optional
    ``source_name`` / ``instance_label`` so the supervisor can resolve the
    runtime by name).
  - ``start()`` matches the IngestionRuntime Protocol — no positional args.
    Credentials come from the constructor; the event handler defaults to
    ``self._dispatch`` (gas-price first, slug second) so production code
    does not have to wire a callback. Tests can override ``_event_handler``
    to capture events at the WebSocket frame boundary.
  - ``_dispatch`` is a method on the runtime (per the multi-instance
    runtime decision) so ``config_id`` is ergonomic — no callback threading.
  - The legacy ``_load_credentials`` and ``data_source_configs`` fallback
    shims are gone; the supervisor reads the config row natively.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import websockets
from websockets.exceptions import (
    ConnectionClosed,
    ConnectionClosedError,
    WebSocketException,
)

logger = logging.getLogger("lightningrod.hass")


class _AuthInvalid(Exception):
    """Internal exception for HA auth_invalid responses."""

    pass


class HAWebSocketRuntime:
    """WebSocket runtime for Home Assistant ``state_changed`` event streaming.

    One instance per enabled ``data_source_configs`` row where
    ``source_name='ha_fordpass'``. The supervisor owns lifecycle and
    spawns/stops runtimes as the config rows toggle.
    """

    def __init__(
        self,
        config_id: int,
        ha_url: str,
        ha_token: str,
        *,
        source_name: str = "ha_fordpass",
        instance_label: str = "default",
    ) -> None:
        self.config_id = config_id
        self.source_name = source_name
        self.instance_label = instance_label
        self._ha_url = ha_url
        self._ha_token = ha_token
        self._ws: Any | None = None
        self._running: bool = False
        self._msg_id: int = 0
        self._ha_config: dict | None = None
        self._entity_states: dict[str, dict] = {}
        # Default event handler is the runtime's own dispatch method; tests can
        # override this attribute with a captured-events callback to inspect
        # the WebSocket frame stream without going through DB writes.
        self._event_handler: Callable | None = None
        self._task: asyncio.Task | None = None
        self.detected_vin: str | None = None
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

    async def start(self) -> None:
        """Connect, authenticate, subscribe, and enter the event loop.

        Reconnects automatically on failure (except auth errors). Credentials
        and the dispatch entry point are owned by the runtime; callers do not
        thread them through.
        """
        self._running = True
        ha_url = self._ha_url
        ha_token = self._ha_token
        logger.info("HA runtime starting (config_id=%d, target=%s)", self.config_id, ha_url)

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
                TimeoutError,
                ConnectionClosed,
                ConnectionClosedError,
                ConnectionError,
                OSError,
                WebSocketException,
            ) as exc:
                self._health["connected"] = False
                self._health["connection_state"] = "reconnecting"
                self._health["errors"] += 1
                self._health["last_error"] = str(exc)
                logger.warning("HA connection lost: %s", exc)
                if self._running:
                    await self._reconnect_loop(ha_url, ha_token)
            except asyncio.CancelledError:
                logger.info("HA runtime cancelled")
                break
            except Exception as exc:
                self._health["errors"] += 1
                self._health["last_error"] = str(exc)
                logger.exception("Unexpected error in HA runtime: %s", exc)
                if self._running:
                    await self._reconnect_loop(ha_url, ha_token)

        await self._close_ws()
        logger.info("HA runtime stopped (config_id=%d)", self.config_id)

    async def stop(self) -> None:
        """Stop the runtime gracefully."""
        logger.info("HA runtime stopping (config_id=%d)", self.config_id)
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
        self._ws = await websockets.connect(ws_url, max_size=16 * 1024 * 1024)

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

        # Step 6: Process initial snapshot through the event handler.
        # Captures current state (e.g. last energytransferlogentry) as DB records.
        handler = self._event_handler or self._default_event_handler
        if self._entity_states:
            snapshot_count = 0
            for entity_id, state_obj in self._entity_states.items():
                if not entity_id.startswith("sensor.fordpass_"):
                    continue
                try:
                    await handler(entity_id, {}, state_obj, self._ha_config or {})
                    snapshot_count += 1
                except Exception as exc:
                    logger.error("Snapshot processing error for %s: %s", entity_id, exc)
            logger.info("Processed %d FordPass entities from initial snapshot", snapshot_count)

        # Step 7: subscribe to state_changed
        sub_id = self._next_msg_id()
        await self._send_json(
            {
                "type": "subscribe_events",
                "id": sub_id,
                "event_type": "state_changed",
            }
        )
        sub_msg = await self._recv_json()
        if sub_msg.get("success"):
            logger.info("Subscribed to state_changed events (subscription id=%d)", sub_id)
        else:
            logger.warning("Failed to subscribe to events: %s", sub_msg)

        self._health["connected"] = True
        self._health["connection_state"] = "connected"
        self._health["last_error"] = None
        logger.info("HA runtime fully connected and subscribed (config_id=%d)", self.config_id)

    async def _event_loop(self) -> None:
        """Read messages from websocket, dispatch state_changed events to the handler."""
        handler = self._event_handler or self._default_event_handler
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

                    if new_state:
                        self._entity_states[entity_id] = new_state

                    self._health["events_processed"] += 1
                    self._health["last_event_at"] = datetime.now(UTC).isoformat()

                    try:
                        await handler(entity_id, old_state, new_state, self._ha_config or {})
                    except Exception as exc:
                        logger.error(
                            "Event handler error for %s: %s", entity_id, exc
                        )

            elif msg_type == "result":
                pass
            elif msg_type == "pong":
                pass
            else:
                logger.debug("Unhandled message type: %s", msg_type)

    async def _default_event_handler(
        self,
        entity_id: str,
        old_state: dict,
        new_state: dict,
        ha_config: dict,
    ) -> None:
        """Internal default handler — bridges the legacy 4-arg shape to ``_dispatch``.

        Production code paths drive every event through ``_dispatch`` so the
        gas-price / slug fan-out is the only ingestion path. Tests that need
        to capture frame-level events can override ``self._event_handler``.
        """
        await self._dispatch(entity_id, new_state)

    async def _dispatch(self, entity_id: str, new_state: dict) -> None:
        """Per-event fan-out: try the gas-price adapter first, then ha_fordpass slug.

        Replaces the legacy free-function dispatcher. The two-session-per-event
        pattern is preserved (one for the gas-price branch, one for the slug
        branch); single-session optimization is deliberately deferred.
        """
        from db.engine import AsyncSessionLocal
        from web.services.sources.ha_fordpass.dispatch import dispatch_slug
        from web.services.sources.ha_gas_price.adapter import try_handle_event

        # Gas-price branch — match by configured entity_id, not slug pattern.
        async with AsyncSessionLocal() as db:
            try:
                handled = await try_handle_event(entity_id, new_state, db)
                if handled:
                    await db.commit()
                    return
            except Exception as e:
                await db.rollback()
                logger.error(
                    "Gas-price dispatch error for %s: %s",
                    entity_id,
                    e,
                    exc_info=True,
                )

        # Slug-based ha_fordpass dispatch.
        async with AsyncSessionLocal() as db:
            try:
                await dispatch_slug(
                    entity_id,
                    new_state,
                    self._ha_config or {},
                    db,
                    config_id=self.config_id,
                )
                await db.commit()
            except Exception as e:
                await db.rollback()
                logger.error(
                    "ha_fordpass dispatch error for %s: %s",
                    entity_id,
                    e,
                    exc_info=True,
                )

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
        # Deferred import — handlers transitively imports _helpers which
        # imports the ingestion package; a top-level import here would create
        # a cycle at module-load time.
        from web.services.sources.ha_fordpass.handlers import _FORDPASS_ENTITY_RE

        for entity_id in self._entity_states:
            match = _FORDPASS_ENTITY_RE.match(entity_id)
            if match:
                self.detected_vin = match.group(1)
                logger.info("Auto-detected VIN from entity: %s", entity_id)
                return
        logger.debug("No FordPass entities found for VIN detection")

    async def _send_json(self, data: dict) -> None:
        """Send a JSON message over the websocket."""
        assert self._ws is not None
        await self._ws.send(json.dumps(data))

    async def _recv_json(self) -> dict:
        """Receive and parse a JSON message from the websocket."""
        assert self._ws is not None
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

    async def _ha_rest_headers(self) -> tuple[str, dict[str, str]] | None:
        """Return ``(ha_url, headers)`` for REST calls, or None if missing.

        Credentials are owned by the runtime instance — there is no fallback
        to ``app_settings`` or ``data_source_configs`` here. The supervisor
        reads the config row at start time and feeds the values into the
        constructor; if a token rotates, the supervisor is restarted via
        ``restart_runtime`` (Settings POST handler).
        """
        ha_url = (self._ha_url or "").rstrip("/")
        ha_token = self._ha_token or ""
        if not ha_url or not ha_token:
            return None
        return (ha_url, {"Authorization": f"Bearer {ha_token}"})

    async def fetch_entity_state(self, entity_id: str) -> dict | None:
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
        start_time_iso: str | None = None,
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
            start_time_iso = (
                datetime.now(UTC) - timedelta(days=365 * 10)
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

        return history_data[0]

    async def backfill_history(self, days: int | None = None) -> dict:
        """Backfill historical data from HA REST for both charging and gas sensors.

        Pulls past state changes for:
          * the energytransferlogentry entity (charging sessions)
          * any gas sensors configured in app_settings (station / average)

        When ``days`` is None the start time is set to 10 years ago, letting
        HA return as much history as its recorder retains. Pass an int to cap.

        Duplicate prevention:
          * Charging sessions: reuses the existing +/-30 min +/- 10% energy
            fuzzy match inside handle_energy_transfer (no changes needed here).
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

        start_time_iso: str | None = None
        if days is not None:
            start_time_iso = (
                datetime.now(UTC) - timedelta(days=days)
            ).isoformat()

        result: dict = {
            "sessions": {"processed": 0, "errors": 0},
            "gas": {},
        }

        handler = self._event_handler or self._default_event_handler

        # --- 1. Charging sessions ---
        vin = self.detected_vin or "unknown"
        energy_entity = f"sensor.fordpass_{vin}_energytransferlogentry"
        logger.info(
            "Backfill: fetching charging session history for %s (start=%s)",
            energy_entity,
            start_time_iso or "all",
        )
        session_states = await self._fetch_entity_history(energy_entity, start_time_iso)
        for state_obj in session_states:
            if not state_obj.get("attributes"):
                continue
            try:
                await handler(energy_entity, {}, state_obj, self._ha_config or {})
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
                entity_id,
                start_time_iso or "all",
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

            if counts["inserted"] > 0:
                await self._refresh_gas_monthly_history(entity_id)

        logger.info(
            "Backfill complete: sessions=%s, gas=%s",
            result["sessions"],
            result["gas"],
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
