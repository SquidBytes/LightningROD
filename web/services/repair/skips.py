"""Persisted review skips: group keys an operation must leave alone."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.repair_skip import RepairSkip

MAX_KEY_LENGTH = 255


async def skipped_keys(db: AsyncSession, operation: str) -> frozenset[str]:
    """Every group key skipped for this operation."""
    stmt = select(RepairSkip.group_key).where(RepairSkip.operation == operation)
    return frozenset((await db.execute(stmt)).scalars().all())


async def count_skips(db: AsyncSession, operation: str) -> int:
    stmt = select(func.count()).where(RepairSkip.operation == operation)
    return (await db.execute(stmt)).scalar_one()


async def add_skips(db: AsyncSession, operation: str, keys: Iterable[str]) -> int:
    """Record new skips (existing and malformed keys ignored); return count added."""
    existing = await skipped_keys(db, operation)
    fresh = {
        key
        for key in keys
        if key and len(key) <= MAX_KEY_LENGTH and key not in existing
    }
    db.add_all(RepairSkip(operation=operation, group_key=key) for key in fresh)
    await db.flush()
    return len(fresh)


async def clear_skips(db: AsyncSession, operation: str) -> int:
    """Forget every skip for this operation; return count removed."""
    result = await db.execute(
        delete(RepairSkip).where(RepairSkip.operation == operation)
    )
    return int(getattr(result, "rowcount", 0) or 0)
