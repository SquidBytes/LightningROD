#!/bin/bash
set -e

# Resolve SQLite path from DATABASE_URL when sqlite+aiosqlite scheme.
# DATABASE_URL=sqlite+aiosqlite:////data/demo.db -> /data/demo.db
# SQLAlchemy URL form: 4 slashes for absolute path, 3 for relative.
SQLITE_PATH=""
case "$DATABASE_URL" in
    sqlite+aiosqlite:///*)
        SQLITE_PATH="${DATABASE_URL#sqlite+aiosqlite:///}"
        # Strip leading slashes so '////data/demo.db' -> '/data/demo.db' and
        # 'relative/path.db' picks up a leading slash for mkdir below.
        # Short-circuit :memory: URLs — in-memory DBs are fundamentally
        # incompatible with the file-marker seed pattern; treat as non-file
        # backend so mkdir / marker logic is skipped.
        case "$SQLITE_PATH" in
            :memory:|/:memory:)
                SQLITE_PATH=""
                ;;
            /*) ;;
            *)  SQLITE_PATH="/$SQLITE_PATH" ;;
        esac
        ;;
esac

# Ensure data directory exists (parent of the .db file).
if [ -n "$SQLITE_PATH" ]; then
    mkdir -p "$(dirname "$SQLITE_PATH")"
fi

# If DEMO_MODE=true and no marker file is present, re-seed at boot.
# Marker keeps it idempotent across uvicorn restarts within the same
# container life.
SEED_MARKER="${SQLITE_PATH}.seeded"

if [ -n "$SQLITE_PATH" ] && [ "$DEMO_MODE" = "true" ] && [ ! -f "$SEED_MARKER" ]; then
    echo "Demo mode: seeding fresh SQLite database at $SQLITE_PATH"
    # Defense-in-depth: drop stale main + WAL/SHM sidecars before re-seed.
    # Defensive cleanup keeps any persistent-volume deployment safe from
    # partial-write corruption from a prior unclean exit.
    rm -f "$SQLITE_PATH" "${SQLITE_PATH}-wal" "${SQLITE_PATH}-shm"

    echo "  Running migrations..."
    uv run alembic upgrade head

    # Single seed entry point — scripts/seed/main.py orchestrates every module
    # in FK order in one transaction. `|| echo` keeps the container alive even
    # if a single seeder warns (e.g. missing optional CSV in demo).
    echo "  Running scripts.seed.main --all..."
    uv run python -m scripts.seed.main --all || echo "  (scripts.seed.main warned — continuing)"

    touch "$SEED_MARKER"
    echo "Demo seed complete."
else
    echo "Running Alembic migrations (non-demo or already seeded)..."
    uv run alembic upgrade head
fi

echo "Starting LightningROD..."
exec uv run uvicorn web.main:app --host 0.0.0.0 --port 8000
