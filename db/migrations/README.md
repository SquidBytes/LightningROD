# Alembic Migrations

This project uses Alembic for schema migrations.

## Squashed initial migration (2026-04-26)

The full pre-squash migration history has been consolidated into a single
dialect-aware initial migration: `versions/p30_squashed_initial.py`.

It runs on both PostgreSQL and SQLite — it uses `sa.DateTime(timezone=True)`,
`sa.Uuid(as_uuid=True)`, `sa.func.now()` server defaults, the `JSONStorage`
TypeDecorator from `db/types.py`, and declares both `postgresql_where=` and
`sqlite_where=` on partial indexes.

### Pulling against an existing PostgreSQL dev database

You must stamp the database to the new revision **before** running
`alembic upgrade head`. Otherwise the squashed migration refuses to run —
it raises a `RuntimeError` when it detects existing tables without a
matching stamp, naming the exact command to fix the situation.

```bash
cd app-public
uv run alembic stamp p30_squashed_initial
# Now you can pull and continue normally
uv run alembic upgrade head    # no-op if already at head
```

### Starting fresh (new dev DB, demo deploys, CI)

No special action required. `alembic upgrade head` creates the schema and
applies the seed rows.

### Why squash?

A single source of schema truth that runs cleanly on both PostgreSQL and
SQLite is easier to maintain than a long chain of dialect-conditional
migrations.

### Backwards compatibility

Per project policy (`CLAUDE.md`), backwards compatibility is NOT required.
The squash's `down_revision` is `None` — it cannot downgrade because the
prior migrations no longer exist. Its `downgrade()` raises
`NotImplementedError` deliberately. To revert to the pre-squash schema,
check out a pre-squash branch and migrate there instead.

## Adding new migrations

Use `uv run alembic revision --autogenerate -m "your message"` as before.
Future migrations chain off the squash normally with
`down_revision = "p30_squashed_initial"` (auto-set by autogenerate).

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
