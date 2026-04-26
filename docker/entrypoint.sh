#!/bin/bash
set -e

echo "Running Alembic migrations..."
uv run alembic upgrade head

if [ "$DEMO_MODE" = "true" ]; then
    echo "DEMO_MODE=true — running demo seed (this wipes/refreshes demo data on every start)..."
    uv run python -m scripts.seed.main --all
else
    echo "DEMO_MODE not set — skipping demo seed."
fi

echo "Starting LightningROD..."
exec uv run uvicorn web.main:app --host 0.0.0.0 --port 8000
