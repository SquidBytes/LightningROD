"""Polling runtime for one FordConnect vehicle.

Cadence is fixed, not user-tunable: a GET never wakes the vehicle, so
polling faster only re-reads the same cached payload while spending from a
rate bucket shared by every vehicle on the same Ford app.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from db.engine import AsyncSessionLocal
from db.models.data_source_config import DataSourceConfig
from web.services.sources.config_store import merge_into_row
from web.services.sources.fcon.client import (
    AuthExpired,
    FordClient,
    FordError,
    RateLimited,
)
from web.services.sources.fcon.config import FconConfig
from web.services.sources.fcon.tokens import ConsentRequired, TokenManager

logger = logging.getLogger("lightningrod.ingestion.fcon")

AWAKE_INTERVAL = 60
PARKED_INTERVAL = 300
# wallbox and health alerts change rarely; they ride every Nth tick.
EXTRA_EVERY = 30
EXTRA_ENDPOINTS = ("wallbox", "vehicle-health-alerts")
SCHEDULES_EVERY = timedelta(days=1)

SampleSink = Callable[[int, str, Any], Awaitable[None]]
Clock = Callable[[], datetime]


def metric_value(payload: dict, name: str) -> Any:
    entry = (payload.get("metrics") or {}).get(name)
    return entry.get("value") if isinstance(entry, dict) else entry


def vehicle_is_awake(payload: dict) -> bool:
    """Driving or doing anything at the plug — the TCU pushes ~every minute then."""
    ignition = str(metric_value(payload, "ignitionStatus") or "").upper()
    plug = str(metric_value(payload, "xevPlugChargerStatus") or "").upper()
    display = str(metric_value(payload, "xevBatteryChargeDisplayStatus") or "").upper()
    return (
        ignition in ("ON", "RUN", "START")
        or plug in ("CHARGING", "CHARGINGAC", "CONNECTED")
        or display == "IN_PROGRESS"
    )


def parse_update_time(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def age_text(then: datetime, now: datetime) -> str:
    seconds = max(0, int((now - then).total_seconds()))
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


async def persist_token_fields(config_id: int, fields: dict[str, Any]) -> None:
    async with AsyncSessionLocal() as db:
        row = await db.get(DataSourceConfig, config_id)
        if row is not None:
            await merge_into_row(db, row.source_name, fields, row=row)


class FconPollingRuntime:
    """One vehicle's poll loop; implements `IngestionRuntime`."""

    def __init__(
        self,
        config_id: int,
        config: FconConfig,
        sink: SampleSink,
        *,
        source_name: str = "fcon",
        instance_label: str = "default",
        client_factory: Callable[[], FordClient] = FordClient,
        persist: Callable[[int, dict[str, Any]], Awaitable[None]] = persist_token_fields,
        now: Clock = lambda: datetime.now(UTC),
    ) -> None:
        self.config_id = config_id
        self.source_name = source_name
        self.instance_label = instance_label
        self._config = config
        self._sink = sink
        self._client_factory = client_factory
        self._persist = persist
        self._now = now
        self._stop = asyncio.Event()
        self._ticks = 0
        self._schedules_at: datetime | None = None
        self._garage_done = False
        self._newest_reading: datetime | None = None
        self._health: dict[str, Any] = {
            "connected": False,
            "connection_state": "starting" if config.connected else "needs_consent",
            "last_event_at": None,
            "events_processed": 0,
            "errors": 0,
            "last_error": None,
            "last_successful_write": None,
            "rate_limited_until": None,
            "next_poll_at": None,
        }

    @property
    def health(self) -> dict[str, Any]:
        out = dict(self._health)
        out["newest_reading_at"] = (
            self._newest_reading.isoformat() if self._newest_reading else None
        )
        out["newest_reading_age"] = (
            age_text(self._newest_reading, self._now()) if self._newest_reading else None
        )
        return out

    async def start(self) -> None:
        if not self._config.connected:
            logger.info("fcon config_id=%d has no consent; not polling", self.config_id)
            return
        async with self._client_factory() as client:
            tokens = TokenManager(
                self._config,
                client,
                lambda fields: self._persist(self.config_id, fields),
                now=self._now,
            )
            while not self._stop.is_set():
                delay = await self.tick(client, tokens)
                if delay is None:
                    break
                self._health["next_poll_at"] = (self._now() + timedelta(seconds=delay)).isoformat()
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                except TimeoutError:
                    pass
        self._health["connected"] = False

    async def stop(self) -> None:
        self._stop.set()
        self._health["connection_state"] = "stopped"
        self._health["connected"] = False

    def _endpoints_due(self) -> list[str]:
        due = []
        if not self._garage_done:
            due.append("garage")
        now = self._now()
        if self._schedules_at is None or now - self._schedules_at >= SCHEDULES_EVERY:
            due.append("charge-schedules")
        due.append("telemetry")
        if self._ticks % EXTRA_EVERY == 0:
            due.extend(EXTRA_ENDPOINTS)
        return due

    async def tick(self, client: FordClient, tokens: TokenManager) -> int | None:
        """Poll everything due; seconds until the next tick, None to stop for good."""
        due = self._endpoints_due()
        self._ticks += 1
        awake = False
        for endpoint in due:
            try:
                payload = await client.get(endpoint, await tokens.access_token())
            except ConsentRequired:
                self._health.update(connected=False, connection_state="needs_consent")
                logger.warning("fcon config_id=%d needs a new consent", self.config_id)
                return None
            except RateLimited as exc:
                # Skip the rest of the tick rather than retry: a retry spends
                # from the very bucket it is waiting on.
                until = self._now() + timedelta(seconds=exc.retry_after)
                self._health.update(
                    connection_state="rate_limited", rate_limited_until=until.isoformat()
                )
                return max(exc.retry_after, AWAKE_INTERVAL)
            except AuthExpired as exc:
                self._note_error(endpoint, exc)
                tokens.invalidate()
                return AWAKE_INTERVAL
            except FordError as exc:
                self._note_error(endpoint, exc)
                continue
            await self._deliver(endpoint, payload)
            if endpoint == "garage":
                self._garage_done = True
            elif endpoint == "charge-schedules":
                self._schedules_at = self._now()
            elif endpoint == "telemetry" and isinstance(payload, dict):
                awake = vehicle_is_awake(payload)
                reading = parse_update_time(payload.get("updateTime"))
                if reading is not None:
                    self._newest_reading = reading
        if self._health["connection_state"] in ("starting", "rate_limited"):
            self._health.update(connection_state="polling", rate_limited_until=None)
        return AWAKE_INTERVAL if awake else PARKED_INTERVAL

    async def _deliver(self, endpoint: str, payload: Any) -> None:
        now = self._now().isoformat()
        self._health.update(connected=True, last_event_at=now)
        self._health["events_processed"] += 1
        try:
            await self._sink(self.config_id, endpoint, payload)
            self._health["last_successful_write"] = now
        except Exception as exc:
            logger.exception("fcon sample for %s failed to store", endpoint)
            self._note_error(endpoint, exc)

    def _note_error(self, endpoint: str, exc: Exception) -> None:
        # Messages never carry tokens: FordError bodies are Ford's replies.
        self._health["errors"] += 1
        self._health["last_error"] = f"{endpoint}: {type(exc).__name__}: {exc}"
