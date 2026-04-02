#!/bin/bash
set -e

PGDATA="/var/lib/postgresql/data"
PG_USER="${POSTGRES_USER:-lightningrod}"
PG_DB="${POSTGRES_DB:-lightningrod}"

# --- PostgreSQL Setup ---
if [ ! -f "$PGDATA/PG_VERSION" ]; then
    echo "Initializing PostgreSQL data directory..."
    chown postgres:postgres "$PGDATA"
    su postgres -c "initdb -D $PGDATA"

    # Allow local trust authentication (single-container, no network exposure)
    echo "host all all 127.0.0.1/32 trust" >> "$PGDATA/pg_hba.conf"
    echo "local all all trust" >> "$PGDATA/pg_hba.conf"
fi

echo "Starting PostgreSQL..."
su postgres -c "pg_ctl start -D $PGDATA -l /var/log/postgresql.log -o '-c listen_addresses=localhost'"

echo "Waiting for PostgreSQL..."
until su postgres -c "pg_isready -h 127.0.0.1" > /dev/null 2>&1; do
    sleep 1
done

# Create role and database if they don't exist (first run)
su postgres -c "psql -h 127.0.0.1 -tc \"SELECT 1 FROM pg_roles WHERE rolname='$PG_USER'\"" | grep -q 1 \
    || su postgres -c "psql -h 127.0.0.1 -c \"CREATE ROLE $PG_USER WITH LOGIN PASSWORD '${POSTGRES_PASSWORD:-lightningrod}';\""

su postgres -c "psql -h 127.0.0.1 -tc \"SELECT 1 FROM pg_database WHERE datname='$PG_DB'\"" | grep -q 1 \
    || su postgres -c "psql -h 127.0.0.1 -c \"CREATE DATABASE $PG_DB OWNER $PG_USER;\""

# --- Application Startup ---
echo "Running Alembic migrations..."
uv run alembic upgrade head

echo "Starting LightningROD..."
exec uv run uvicorn web.main:app --host 0.0.0.0 --port 8000
