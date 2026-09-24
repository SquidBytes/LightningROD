"""Repair-operation framework: mutation guard, diff/result types, base class, dry-run session."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypeVar, overload

from sqlalchemy.ext.asyncio import AsyncSession

# Preservation invariant: repairs only ever mutate rows ingested by these
# source systems — manual/manual_entry/csv_import rows are untouchable.
MUTABLE_SOURCE_SYSTEMS = ("ha_fordpass",)


def mutable_only(model):
    """Filter clause restricting a query to rows repairs may mutate."""
    return model.source_system.in_(MUTABLE_SOURCE_SYSTEMS)


@overload
def _aware(ts: datetime) -> datetime: ...


@overload
def _aware(ts: None) -> None: ...


def _aware(ts: datetime | None) -> datetime | None:
    """UTC-aware copy; SQLite returns naive datetimes.

    Stamps UTC on a naive value only — an offset-aware value is returned
    unchanged, not re-based onto UTC.
    """
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


DEFAULT_PREVIEW_LIMIT = 10

T = TypeVar("T")


@dataclass
class RepairDiff:
    """One row's before/after change from a preview or apply.

    Everything after `action` is optional review context: `identity` names the
    row in human terms, `notes` explains a single field's provenance, and
    `role` labels this row's part in a multi-row group.
    """

    row_id: int | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    action: str  # "update" | "delete" | "insert"
    identity: dict[str, Any] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)
    role: str | None = None


@dataclass
class RepairGroup:
    """The diffs a reviewer must judge together, plus the evidence that paired them.

    `key` names the group stably across requests so a reviewer can select or
    skip it; an empty key means the group cannot be selected on its own.
    """

    diffs: list[RepairDiff]
    label: str | None = None
    context: dict[str, Any] = field(default_factory=dict)
    key: str = ""

    @property
    def actions(self) -> list[str]:
        """Distinct member actions, in member order."""
        return list(dict.fromkeys(diff.action for diff in self.diffs))

    @property
    def fields(self) -> list[str]:
        """Every field any member mentions; identity fields lead."""
        ordered: list[str] = []
        for source in ("identity", "before", "after"):
            for diff in self.diffs:
                for name in getattr(diff, source) or {}:
                    if name not in ordered:
                        ordered.append(name)
        return ordered

    @property
    def rows(self) -> list[dict[str, Any]]:
        """Field-major view of the group: one entry per field, one cell per member."""
        return [
            {"field": name, "cells": [_cell(diff, name) for diff in self.diffs]}
            for name in self.fields
        ]


def _cell(diff: RepairDiff, name: str) -> dict[str, Any]:
    """One field of one diff, with enough flags for a template to render it raw."""
    before = diff.before or {}
    after = diff.after or {}
    has_before, has_after = name in before, name in after
    return {
        "before": before.get(name),
        "after": after.get(name),
        "has_before": has_before,
        "has_after": has_after,
        "identity": diff.identity.get(name),
        "has_identity": name in diff.identity,
        "note": diff.notes.get(name),
    }


@dataclass
class RepairPreview:
    """One page of review groups plus the total the operation would touch."""

    groups: list[RepairGroup]
    total: int
    offset: int = 0
    limit: int = DEFAULT_PREVIEW_LIMIT
    unit: str = "rows"

    @property
    def diffs(self) -> list[RepairDiff]:
        return [diff for group in self.groups for diff in group.diffs]

    @property
    def selectable(self) -> bool:
        """Whether every group on the page can be ticked on its own."""
        return bool(self.groups) and all(group.key for group in self.groups)

    @property
    def unit_label(self) -> str:
        """The unit noun, singular when the total is one."""
        if self.total == 1 and self.unit.endswith("s"):
            return self.unit[:-1]
        return self.unit

    @property
    def first(self) -> int:
        return self.offset + 1 if self.groups else 0

    @property
    def last(self) -> int:
        return self.offset + len(self.groups)

    @property
    def has_prev(self) -> bool:
        return self.offset > 0

    @property
    def has_next(self) -> bool:
        return self.last < self.total

    @property
    def prev_offset(self) -> int:
        return max(self.offset - self.limit, 0)

    @property
    def next_offset(self) -> int:
        return self.offset + self.limit


@dataclass(frozen=True)
class Selection:
    """Which group keys an operation may act on: never skipped ones, only `only` if set."""

    skipped: frozenset[str] = frozenset()
    only: frozenset[str] | None = None

    def allows(self, key: str) -> bool:
        return key not in self.skipped and (self.only is None or key in self.only)

    def pick(self, items: Iterable[T], key: Callable[[T], str]) -> list[T]:
        return [item for item in items if self.allows(key(item))]


@dataclass
class RepairResult:
    """Outcome of a repair apply: run identity plus affected/snapshot counts."""

    operation: str
    run_id: uuid.UUID | None
    affected: int
    snapshot_rows: int
    details: dict[str, Any] = field(default_factory=dict)


class RepairOperation(ABC):
    """A restorable, idempotent repair over one model's rows.

    `keys` on affected_rows/execute/apply limits the run to groups with those
    keys; None means every group. Skipped groups are always excluded.
    """

    slug: str
    display_name: str
    description: str
    model: type
    # Source systems apply() lets this operation mutate.
    guarded_source_systems: tuple[str, ...] = MUTABLE_SOURCE_SYSTEMS

    @abstractmethod
    async def census(self, db: AsyncSession) -> int:
        """Count groups this operation would currently affect."""

    @abstractmethod
    async def preview(
        self,
        db: AsyncSession,
        limit: int = DEFAULT_PREVIEW_LIMIT,
        offset: int = 0,
    ) -> RepairPreview:
        """Return one page of review groups without persisting anything."""

    @abstractmethod
    async def affected_rows(
        self, db: AsyncSession, keys: Iterable[str] | None = None
    ) -> list[Any]:
        """Return the ORM rows the next execute() will mutate."""

    @abstractmethod
    async def execute(self, db: AsyncSession, keys: Iterable[str] | None = None) -> int:
        """Mutate affected rows in-session; return count changed."""

    async def selection(
        self, db: AsyncSession, keys: Iterable[str] | None = None
    ) -> Selection:
        """This operation's skips, narrowed to `keys` when given."""
        from web.services.repair.skips import skipped_keys

        return Selection(
            await skipped_keys(db, self.slug),
            None if keys is None else frozenset(keys),
        )

    def guard(self, rows: Iterable[Any]) -> None:
        """Refuse to touch any row outside guarded_source_systems."""
        for row in rows:
            if getattr(row, "source_system", None) not in self.guarded_source_systems:
                raise ValueError(
                    f"repair '{self.slug}' targeted a non-mutable row "
                    f"(id={row.id}, source_system={row.source_system!r})"
                )

    async def apply(
        self, db: AsyncSession, keys: Iterable[str] | None = None
    ) -> RepairResult:
        """Template method: guard -> snapshot -> execute. Caller commits."""
        from web.services.repair.snapshot import snapshot_rows

        keys = None if keys is None else frozenset(keys)
        rows = await self.affected_rows(db, keys=keys)
        if not rows:
            return RepairResult(self.slug, None, 0, 0)
        self.guard(rows)
        run_id = uuid.uuid4()
        count = await snapshot_rows(db, run_id, self.slug, rows)
        affected = await self.execute(db, keys=keys)
        details = dict(getattr(self, "last_details", {}))
        return RepairResult(self.slug, run_id, affected, count, details=details)


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
