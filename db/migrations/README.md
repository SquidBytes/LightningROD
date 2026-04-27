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

## Render demo deployment

The public demo runs as a Render Web Service against a fresh SQLite
database seeded at container start. Restart = reset; no scheduled cron.

### Render service settings

| Setting | Value |
|---------|-------|
| Service type | Web Service |
| Tier | Free (ephemeral filesystem = automatic reset on restart) |
| Build context | `app-public/` |
| Dockerfile path | `app-public/docker/Dockerfile` |
| Build command | (handled by Dockerfile multi-stage build) |
| Start command | (uses Dockerfile ENTRYPOINT — `docker/entrypoint.sh`) |
| Persistent disk | **None.** The ephemeral filesystem IS the reset mechanism. |
| Health check path | `/healthz` |

### Environment variables

```
DEMO_MODE=true
DATABASE_URL=sqlite+aiosqlite:////data/demo.db
LIGHTNINGROD_VERSION=demo
```

### What the entrypoint does on each cold start

1. Resolves the SQLite path from `DATABASE_URL` (`/data/demo.db`).
2. Ensures `/data/` exists (the Dockerfile pre-creates it).
3. If `DEMO_MODE=true` AND no `/data/demo.db.seeded` marker:
   - Removes any stale `demo.db` + `demo.db-wal` + `demo.db-shm` sidecars.
   - Runs `alembic upgrade head` (creates schema from the squashed migration).
   - Runs `python -m scripts.seed.main --all` (orchestrates every seed
     module in FK order in one transaction; warns and continues on
     individual seeder errors so a missing optional CSV does not crash
     the container).
   - Touches the marker file to make the seed idempotent within
     the container's life.
4. Otherwise (non-demo OR already seeded), runs `alembic upgrade head`
   only.
5. Starts uvicorn.

### Cold-start expectations

Render free-tier services sleep after ~15 minutes of inactivity. First
visit after sleep takes ~30 seconds (container cold-start + entrypoint
seed pipeline). If the cold-start UX matters later, options include:
(a) baking the seeded SQLite file as a Docker layer (loses freshness),
(b) cron-pinging `/healthz` to keep the service warm (paid tier only),
(c) upgrading to paid tier with a baked image.

### If you ever enable persistent disk on Render

The free-tier ephemeral filesystem makes `*-wal` / `*-shm` cleanup
trivial — they vanish with the container. If you upgrade to a paid
disk tier, you MUST:

- Mount `/data/` as a single persistent volume (so `.db` and its
  sidecars travel together).
- Run `PRAGMA wal_checkpoint(TRUNCATE)` on graceful shutdown to merge
  the WAL into the main file before snapshot.
- Reconsider the seed-on-start pattern — at that point you probably
  want a seeded image instead.

### Demo write-protection

The `DemoModeMiddleware` (`web/middleware/demo_mode.py`) blocks DELETE,
PUT, and PATCH with a 403 JSON response when `DEMO_MODE=true`. The demo
banner partial (`web/templates/partials/demo_banner.html`) renders only
when `demo_mode` is true and links to the public repo
(`aminorjourney/LightningROD`).
