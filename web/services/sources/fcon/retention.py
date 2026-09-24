"""How long raw FordConnect responses are kept, and what that costs on disk."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.fcon_raw_sample import FconRawSample
from web.queries.settings import clamp_retention_days, get_app_setting, set_app_setting
from web.services.sources.fcon.raw_store import (
    DEFAULT_RETENTION_DAYS,
    RETENTION_SETTING,
)

# Fresh-install assumptions, from the captured payloads.
TYPICAL_SAMPLE_BYTES = 28_000
TYPICAL_SAMPLES_PER_DAY = 100
# JSONB stores these payloads at roughly 45% of SQLite's JSON text.
POSTGRES_FACTOR = 0.45
OBSERVE_WINDOW = timedelta(days=7)
MIN_OBSERVED_ROWS = 50
MIN_OBSERVED_SPAN = timedelta(days=1)
_SIZE_SAMPLE = 50
# Retention 0 (forever) is quoted per year.
_FOREVER_DAYS = 365


@dataclass(frozen=True)
class Estimate:
    bytes: int
    samples_per_day: float
    mean_bytes: int
    observed: bool
    per_year: bool

    @property
    def size(self) -> str:
        return human_size(self.bytes)


async def get_retention_days(db: AsyncSession) -> int:
    raw = await get_app_setting(db, RETENTION_SETTING, str(DEFAULT_RETENTION_DAYS))
    try:
        return clamp_retention_days(int(raw or DEFAULT_RETENTION_DAYS))
    except ValueError:
        return DEFAULT_RETENTION_DAYS


async def set_retention_days(db: AsyncSession, days: int) -> int:
    days = clamp_retention_days(days)
    await set_app_setting(db, RETENTION_SETTING, str(days))
    return days


async def _observed_rate(db: AsyncSession) -> tuple[float, int] | None:
    """(distinct samples/day, mean payload bytes) from this install's own rows."""
    since = datetime.now(UTC) - OBSERVE_WINDOW
    count, first, last = (
        await db.execute(
            select(
                func.count(FconRawSample.id),
                func.min(FconRawSample.ingested_at),
                func.max(FconRawSample.ingested_at),
            ).where(FconRawSample.ingested_at >= since)
        )
    ).one()
    if not count or count < MIN_OBSERVED_ROWS or first is None or last is None:
        return None
    span = last - first
    if span < MIN_OBSERVED_SPAN:
        return None
    stmt = select(FconRawSample.payload).order_by(FconRawSample.id.desc()).limit(_SIZE_SAMPLE)
    payloads: list[dict] = list((await db.execute(stmt)).scalars().all())
    mean = sum(len(json.dumps(p, separators=(",", ":"))) for p in payloads) // len(payloads)
    return count / (span.total_seconds() / 86400), mean


async def estimate(db: AsyncSession, days: int) -> Estimate:
    """Archive size at `days` of retention, on this install's database backend."""
    observed = await _observed_rate(db)
    per_day, mean = observed or (TYPICAL_SAMPLES_PER_DAY, TYPICAL_SAMPLE_BYTES)
    factor = POSTGRES_FACTOR if db.get_bind().dialect.name == "postgresql" else 1.0
    span = days if days > 0 else _FOREVER_DAYS
    return Estimate(
        bytes=int(per_day * mean * factor * span),
        samples_per_day=per_day,
        mean_bytes=mean,
        observed=observed is not None,
        per_year=days <= 0,
    )


def human_size(n: int) -> str:
    if n < 1_000_000:
        return f"{n // 1000} KB"
    if n < 1_000_000_000:
        return f"{n / 1_000_000:.0f} MB"
    return f"{n / 1_000_000_000:.1f} GB"
