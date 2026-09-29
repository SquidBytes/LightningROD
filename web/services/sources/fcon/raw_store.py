"""Raw-then-parse archive for FordConnect responses.

Every distinct response is kept verbatim until retention expires, so a
mapping bug can be fixed by re-parsing rather than by waiting for new data.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.engine import AsyncSessionLocal
from db.models.fcon_raw_sample import FconRawSample
from db.portable_insert import portable_insert
from web.queries.settings import RAW_ARCHIVE_DEFAULT_RETENTION_DAYS
from web.services.ingestion.raw_archive import RetentionPruner
from web.services.sources.fcon.identity import SOURCE_SYSTEM, device_id_of
from web.services.sources.ford_metrics.parser import parse_time

logger = logging.getLogger("lightningrod.sources.fcon.raw_store")

INGEST_SCHEMA_VERSION = 2
RETENTION_SETTING = "fcon_raw_retention_days"
# Same default as the Home Assistant event archive: ~250 MB on SQLite (~115 MB
# on Postgres) at a typical 100 distinct responses a day, fine for a home server.
DEFAULT_RETENTION_DAYS = RAW_ARCHIVE_DEFAULT_RETENTION_DAYS


def _stamps(entry: Any) -> Any:
    """updateTime(s) of one metric; nested groups like `configurations` recurse."""
    if isinstance(entry, list):
        return [_stamps(item) for item in entry]
    if isinstance(entry, dict):
        if "updateTime" in entry:
            return entry["updateTime"]
        return {k: _stamps(v) for k, v in entry.items()}
    return None


def fingerprint(endpoint: str, payload: dict) -> str:
    """Telemetry: hash of per-metric updateTimes. Other endpoints: hash of the body."""
    if endpoint == "telemetry":
        basis: Any = {k: _stamps(v) for k, v in (payload.get("metrics") or {}).items()}
    else:
        basis = payload
    raw = json.dumps({"endpoint": endpoint, "basis": basis}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


class FconRawArchive(RetentionPruner):
    _model = FconRawSample

    async def store(
        self,
        db: AsyncSession,
        config_id: int,
        endpoint: str,
        payload: dict,
        fetched_at: datetime,
    ) -> bool:
        """Archive a response; False when it is unchanged since last seen."""
        fp = fingerprint(endpoint, payload)
        existing = await db.scalar(
            select(FconRawSample.id).where(
                FconRawSample.config_id == config_id, FconRawSample.fingerprint == fp
            )
        )
        if existing is not None:
            await db.execute(
                update(FconRawSample)
                .where(FconRawSample.id == existing)
                .values(last_seen_at=fetched_at)
            )
            return False
        stmt = portable_insert(FconRawSample, dialect=db.get_bind().dialect).values(
            config_id=config_id,
            device_id=device_id_of(payload),
            endpoint=endpoint,
            payload=payload,
            fingerprint=fp,
            recorded_at=parse_time(payload.get("updateTime")) or fetched_at,
            last_seen_at=fetched_at,
            source_system=SOURCE_SYSTEM,
            ingest_schema_version=INGEST_SCHEMA_VERSION,
        )
        result = await db.execute(
            stmt.on_conflict_do_nothing(index_elements=["config_id", "fingerprint"])
        )
        return bool(getattr(result, "rowcount", 1))

    async def prune(self) -> None:
        from web.services.sources.fcon.retention import get_retention_days

        async with AsyncSessionLocal() as db:
            days = await get_retention_days(db)
        await self._maybe_prune(days)

    def reset(self) -> None:
        self._prune_due_at = 0.0


fcon_raw_archive = FconRawArchive()


def utc_now() -> datetime:
    return datetime.now(UTC)
