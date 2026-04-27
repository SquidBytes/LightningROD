# Alembic Migrations

This project uses Alembic for schema migrations.

## Phase 30 squash (2026-04-26)

All migration history prior to Phase 30 has been squashed into a single
dialect-aware initial migration: `versions/p30_squashed_initial.py`.

The squashed migration is portable across PostgreSQL and SQLite — it uses
`sa.DateTime(timezone=True)`, `sa.Uuid(as_uuid=True)`, `sa.func.now()`
server defaults, the `JSONStorage` TypeDecorator from `db/types.py`, and
declares both `postgresql_where=` and `sqlite_where=` on partial indexes.

### If you are pulling Phase 30 against an EXISTING PostgreSQL dev DB

You must stamp the database to the new revision **before** running
`alembic upgrade head`. Otherwise, the squashed migration will refuse to
run — it raises a clear `RuntimeError` when it detects existing tables
without a matching stamp, naming the exact command to fix the situation.

```bash
cd app-public
uv run alembic stamp p30_squashed_initial
# Now you can pull and continue normally
uv run alembic upgrade head    # no-op if already at head
```

### If you are starting fresh (new dev DB, demo deploys, CI)

No special action required. `alembic upgrade head` will create the
schema and apply the seed rows from the squash.

### Why squash?

Decision D-05 of Phase 30 chose a single source of schema truth that
runs cleanly on both PostgreSQL and SQLite. Cross-dialect migrations
are easier to maintain as one file than as 21 files with conditional
dialect logic.

### What about backwards compatibility?

Per project policy (CLAUDE.md), backwards compatibility is NOT required.
The `down_revision` of the squash is `None` — the squash cannot
downgrade back through prior migrations because they no longer exist.
Its `downgrade()` raises `NotImplementedError` deliberately. To revert
to a pre-Phase-30 schema, check out the v0.3 branch and migrate there
instead.

## Adding new migrations after Phase 30

Use `uv run alembic revision --autogenerate -m "your message"` as
before. The squash is the new initial; future migrations chain off it
normally with `down_revision = "p30_squashed_initial"` (auto-set by
autogenerate).

### Cross-dialect rules for new migrations

- Use `sa.DateTime(timezone=True)` (NOT `postgresql.TIMESTAMP`).
- Use `sa.Uuid(as_uuid=True)` (NOT `postgresql.UUID`).
- Use `sa.func.now()` server defaults (NOT `sa.text('NOW()')`).
- Use `db.types.JSONStorage` for JSON columns (NOT `postgresql.JSONB`).
- For partial indexes, declare BOTH `postgresql_where=` and `sqlite_where=`.
  Remember SQLite stores Boolean as `0/1`, so the predicate text must
  differ between dialects (e.g. `is_complete = true` vs `is_complete = 1`).
- For upserts in scripts/queries, use `db.portable_insert.portable_insert`
  (NOT `from sqlalchemy.dialects.postgresql import insert as pg_insert`).
- Keep the revision id ≤ 32 characters to fit `alembic_version.version_num
  VARCHAR(32)`.
