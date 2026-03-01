#!/bin/bash
set -e

echo "Running Alembic migrations..."
uv run alembic upgrade head

echo "Starting LightningROD..."
exec uv run uvicorn web.main:app --host 0.0.0.0 --port 8000
