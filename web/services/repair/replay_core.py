"""Replay archived Home Assistant events through the ingestion entry point.

The loop that reads `ha_raw_events` and feeds each event to `dispatch_slug`
lives here once, shared by the archive repair operation and the
replay-and-diff harness.
"""

from __future__ import annotations

import logging
import traceback
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.raw_event import HARawEvent
from web.services.repair.base import _aware

logger = logging.getLogger("lightningrod.repair.replay_core")

# Pending-state caches in handlers.py are keyed (config_id, device_id). Replay
# uses a sentinel so its entries never collide with a live runtime's.
SENTINEL_CONFIG_ID = -1

# ha-fordpass localizes these entities' attributes into Home Assistant's unit
# system before emitting them, so without the archived unit system their
# numbers cannot be read. The events payload is always raw metric.
UNIT_SENSITIVE_SUFFIXES = ("metrics", "elveh")

FETCH_BATCH = 1000

Dispatch = Callable[["ArchivedEvent", dict[str, Any], AsyncSession], Awaitable[None]]


@dataclass(frozen=True)
class ArchivedEvent:
    """One archived state and the config it must be read under (None if unknown)."""

    recorded_at: datetime
    entity_id: str
    payload: dict[str, Any]
    ha_config: dict[str, Any] | None

    @classmethod
    def from_row(cls, row: HARawEvent) -> ArchivedEvent:
        return cls(
            _aware(row.recorded_at),
            row.entity_id,
            row.payload,
            {"unit_system": row.ha_unit_system} if row.ha_unit_system else None,
        )


@dataclass
class ReplayFailure:
    """Failed dispatches of one slug with one exception class; `example` is device-free."""

    slug: str
    exception: str
    count: int = 0
    example: str = ""


@dataclass
class ReplayStats:
    replayed: int = 0
    skipped_unknown_units: int = 0
    errors: int = 0
    failures: list[ReplayFailure] = field(default_factory=list)

    def record_failure(self, slug: str, exception: str, example: str) -> None:
        self.errors += 1
        for failure in self.failures:
            if (failure.slug, failure.exception) == (slug, exception):
                failure.count += 1
                return
        self.failures.append(ReplayFailure(slug, exception, 1, example))


def short_device(device_id: str | None) -> str:
    """Last four characters of a device id, for logs and reports."""
    text = device_id or ""
    return f"…{text[-4:]}" if len(text) > 4 else text


def _redact(text: str, device_id: str | None) -> str:
    # get_device_id yields "unknown" for ids it cannot read; nothing to hide then.
    if not device_id or device_id == "unknown":
        return text
    return text.replace(device_id, short_device(device_id))


def needs_unit_system(entity_id: str) -> bool:
    return entity_id.endswith(tuple(f"_{s}" for s in UNIT_SENSITIVE_SUFFIXES))


async def fetch_archive(
    db: AsyncSession,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    device_id: str | None = None,
    slugs: Iterable[str] | None = None,
) -> AsyncIterator[ArchivedEvent]:
    """Archived events ascending by (recorded_at, id), streamed a batch at a time."""
    filters = []
    if since is not None:
        filters.append(HARawEvent.recorded_at >= since)
    if until is not None:
        filters.append(HARawEvent.recorded_at <= until)
    if device_id is not None:
        filters.append(HARawEvent.device_id == device_id)
    if slugs is not None:
        filters.append(HARawEvent.slug.in_(tuple(slugs)))

    cursor: tuple[datetime, int] | None = None
    while True:
        stmt = (
            select(HARawEvent)
            .where(*filters)
            .order_by(HARawEvent.recorded_at, HARawEvent.id)
            .limit(FETCH_BATCH)
        )
        if cursor is not None:
            stmt = stmt.where(
                or_(
                    HARawEvent.recorded_at > cursor[0],
                    and_(
                        HARawEvent.recorded_at == cursor[0], HARawEvent.id > cursor[1]
                    ),
                )
            )
        rows = (await db.execute(stmt)).scalars().all()
        for row in rows:
            yield ArchivedEvent.from_row(row)
        if len(rows) < FETCH_BATCH:
            return
        cursor = (rows[-1].recorded_at, rows[-1].id)


async def _as_async(
    events: Iterable[ArchivedEvent] | AsyncIterator[ArchivedEvent],
) -> AsyncIterator[ArchivedEvent]:
    if hasattr(events, "__aiter__"):
        async for event in events:  # type: ignore[union-attr]
            yield event
    else:
        for event in events:  # type: ignore[union-attr]
            yield event


async def replay_events(
    events: Iterable[ArchivedEvent] | AsyncIterator[ArchivedEvent],
    db: AsyncSession,
    *,
    fallback_config: dict[str, Any] | None = None,
    config_id: int = SENTINEL_CONFIG_ID,
    dispatch: Dispatch | None = None,
    before_each: Callable[[ArchivedEvent], None] | None = None,
    log_prefix: str = "replay",
) -> ReplayStats:
    """Feed each event to the ingestion entry point; never raises past one event.

    An event is read under its own archived config, else `fallback_config`.
    One that needs a unit system and has none is skipped and counted: guessing
    metric would store miles as kilometres. `dispatch` replaces `dispatch_slug`
    for callers that need a narrower write path.
    """
    # Lazy: the repair registry imports this package and must never pull in
    # the HA stack.
    from web.services.sources.ha_fordpass.dispatch import dispatch_slug
    from web.services.sources.ha_fordpass.handlers import extract_slug, get_device_id

    stats = ReplayStats()
    fallback = dict(fallback_config or {})
    async for event in _as_async(events):
        if before_each is not None:
            before_each(event)
        ha_config = event.ha_config or fallback
        if not ha_config.get("unit_system") and needs_unit_system(event.entity_id):
            if not stats.skipped_unknown_units:
                logger.warning(
                    "%s: no unit system for %s; skipping states whose values "
                    "depend on it",
                    log_prefix,
                    event.entity_id,
                )
            stats.skipped_unknown_units += 1
            continue
        try:
            if dispatch is None:
                await dispatch_slug(
                    event.entity_id, event.payload, ha_config, db, config_id=config_id
                )
            else:
                await dispatch(event, ha_config, db)
            stats.replayed += 1
        except Exception as exc:
            # Entity ids carry the full device id; logs and reports show the
            # slug and the device suffix only, so the traceback is redacted too.
            slug = extract_slug(event.entity_id) or "?"
            device_id = get_device_id(event.entity_id, ha_config)
            message = _redact(f"{type(exc).__name__}: {exc}", device_id)
            logger.error(
                "%s: %s failed for %s: %s\n%s",
                log_prefix,
                slug,
                short_device(device_id),
                message,
                _redact("".join(traceback.format_exception(exc)).rstrip(), device_id),
            )
            stats.record_failure(slug, type(exc).__name__, message)
    return stats


def _pending_caches() -> list[dict[tuple[int, str], Any]]:
    from web.services.sources.ha_fordpass import handlers as fp

    return [
        fp._pending_vehicle_status,
        fp._pending_vehicle_status_ts,
        fp._pending_battery_status,
        fp._pending_battery_status_ts,
        fp._last_trip_values,
    ]


async def flush_pending_status(
    db: AsyncSession, config_id: int = SENTINEL_CONFIG_ID
) -> int:
    """Write the status fields still batched for `config_id`; returns devices flushed.

    Live ingestion flushes on `lastrefresh` or a 30 s wall-clock timeout, so a
    replay that finishes faster than that would otherwise drop the last batch.
    """
    from web.services.sources.ha_fordpass import handlers as fp

    devices = {
        key[1]
        for cache in (fp._pending_vehicle_status, fp._pending_battery_status)
        for key in cache
        if key[0] == config_id
    }
    for device_id in sorted(devices):
        await fp._flush_vehicle_status(device_id, db, config_id=config_id)
        await fp._flush_battery_status(device_id, db, config_id=config_id)
    return len(devices)


def purge_pending_state(config_id: int = SENTINEL_CONFIG_ID) -> None:
    """Drop replay's batched status and last-trip entries without writing them."""
    for cache in _pending_caches():
        for key in [k for k in cache if k[0] == config_id]:
            cache.pop(key, None)


async def replay_archive(
    read_sessions: Callable[[], Any],
    db: AsyncSession,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    device_id: str | None = None,
    slugs: Iterable[str] | None = None,
    config_id: int = SENTINEL_CONFIG_ID,
    fallback_config: dict[str, Any] | None = None,
) -> ReplayStats:
    """Replay an archive window into `db`: read, dispatch each event, flush the rest.

    `read_sessions` is a session factory for the archive (it may be the same
    database as `db` or another one). The caller commits `db`.
    """
    async with read_sessions() as archive:
        stats = await replay_events(
            fetch_archive(
                archive, since=since, until=until, device_id=device_id, slugs=slugs
            ),
            db,
            fallback_config=fallback_config,
            config_id=config_id,
            log_prefix="archive-replay",
        )
    await flush_pending_status(db, config_id)
    purge_pending_state(config_id)
    return stats
