"""Repair-operation framework: mutation guard, diff/result types, base class, dry-run session."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

# Preservation invariant: repairs only ever mutate rows ingested by these
# source systems — manual/manual_entry/csv_import rows are untouchable.
MUTABLE_SOURCE_SYSTEMS = ("ha_fordpass",)


def mutable_only(model):
    """Filter clause restricting a query to rows repairs may mutate."""
    return model.source_system.in_(MUTABLE_SOURCE_SYSTEMS)


@dataclass
class RepairDiff:
    """One row's before/after change from a preview or apply."""

    row_id: int | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    action: str  # "update" | "delete" | "insert"


@dataclass
class RepairResult:
    """Outcome of a repair apply: run identity plus affected/snapshot counts."""

    operation: str
    run_id: uuid.UUID | None
    affected: int
    snapshot_rows: int
    details: dict[str, Any] = field(default_factory=dict)


class RepairOperation(ABC):
    """A restorable, idempotent repair over one model's rows."""

    slug: str
    display_name: str
    description: str
    model: type
    # Set on operations that can recover rows the census cannot see — replays
    # insert trips that were never ingested — so Apply stays live at census 0.
    runs_when_clean: bool = False

    @abstractmethod
    async def census(self, db: AsyncSession) -> int:
        """Count rows this operation would currently affect."""

    @abstractmethod
    async def preview(self, db: AsyncSession, limit: int = 10) -> list[RepairDiff]:
        """Return sample diffs without persisting anything."""

    @abstractmethod
    async def affected_rows(self, db: AsyncSession) -> list[Any]:
        """Return the ORM rows the next execute() will mutate."""

    @abstractmethod
    async def execute(self, db: AsyncSession) -> int:
        """Mutate affected rows in-session; return count changed."""

    async def apply(self, db: AsyncSession) -> RepairResult:
        """Template method: guard -> snapshot -> execute. Caller commits."""
        from web.services.repair.snapshot import snapshot_rows

        rows = await self.affected_rows(db)
        if not rows:
            return RepairResult(self.slug, None, 0, 0)
        for row in rows:
            if getattr(row, "source_system", None) not in MUTABLE_SOURCE_SYSTEMS:
                raise ValueError(
                    f"repair '{self.slug}' targeted a non-mutable row "
                    f"(id={row.id}, source_system={row.source_system!r})"
                )
        run_id = uuid.uuid4()
        count = await snapshot_rows(db, run_id, self.slug, rows)
        affected = await self.execute(db)
        return RepairResult(self.slug, run_id, affected, count)


@asynccontextmanager
async def rollback_session(engine=None):
    """Yield a session whose commits become savepoints; everything rolls back on exit."""
    if engine is None:
        # Lazy import so module import never triggers engine creation.
        from db.engine import engine as app_engine

        engine = app_engine
    async with engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()
