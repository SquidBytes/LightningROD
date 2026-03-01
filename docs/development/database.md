# :lucide-database: Database

LightningROD uses PostgreSQL 16 with SQLAlchemy 2.0 in async mode. The schema is designed around the ha-fordpass data model, with 8 tables covering vehicle telemetry even though v1 only populates charging data.

## Schema Overview

### Core Tables

| Table | Columns | Purpose |
|-------|---------|---------|
| `ev_charging_session` | 30 | Charging events: timing, energy, cost, location, SOC |
| `ev_battery_status` | 21 | HV/12V battery snapshots: SOC, voltage, current, temperature |
| `ev_vehicle_status` | 31 | Drivetrain, temperatures, tire pressure, door locks |
| `ev_trip_metrics` | 26 | Per-trip energy, distance, efficiency, driving scores |
| `ev_location` | 13 | GPS snapshots with optional reverse geocoding |

### Reference Tables

| Table | Columns | Purpose |
|-------|---------|---------|
| `ev_charging_networks` | 5 | User-configured network costs per location |
| `ev_location_lookup` | 6 | Known locations for geofence matching |
| `app_settings` | 3 | Key-value store for user preferences and toggles |

!!! info "Why 8 tables when v1 only uses charging data?"
    The schema is designed for the full ha-fordpass data model so it's ready for live ingestion. Adding the adapter later doesn't require schema changes -- only new data flowing into existing tables.

## Connection Management

```python title="db/engine.py"
--8<-- "db/engine.py"
```

Key settings:

- `pool_pre_ping=True` -- validates connections before use
- `pool_recycle=3600` -- refreshes connections every hour
- `asyncpg` driver for native async PostgreSQL access

## Models

Models live in `db/models/` with one file per domain area. The `__init__.py` imports all model classes so Alembic's autogenerate can discover every table:

```python title="db/models/__init__.py"
--8<-- "db/models/__init__.py"
```

### Example: Charging Session

The largest model, with 30 columns covering the full lifecycle of a charging event:

```python title="db/models/charging_session.py"
--8<-- "db/models/charging_session.py"
```

## Migrations

Alembic manages schema versioning with an async-compatible `env.py`.

### Current Migrations

| Migration | Description |
|-----------|-------------|
| `2b6f55486b4d` | Initial schema -- all 8 tables |
| `7086caea2990` | Add `location_type`, `is_free`, `session_id` unique constraint |
| `c9345e830aab` | Phase 4 cost schema -- `is_free` on networks, `cost_source`, `app_settings` table |

### Creating a New Migration

=== "Autogenerate (database running)"

    ```bash
    uv run alembic revision --autogenerate -m "add new column"
    uv run alembic upgrade head
    ```

=== "Manual (no database)"

    ```bash
    uv run alembic revision -m "add new column"
    # Edit the generated file in db/migrations/versions/
    uv run alembic upgrade head
    ```

!!! tip
    When writing manual migrations, look at the existing files in `db/migrations/versions/` for patterns. The Phase 4 migration (`c9345e830aab`) is a good example of a migration that adds columns, creates a new table, and seeds default data.

## Dependency Injection

Each request gets its own database session via FastAPI's dependency system:

```python title="web/dependencies.py"
--8<-- "web/dependencies.py"
```

Route handlers declare the dependency:

```python
@router.get("/sessions")
async def sessions(request: Request, db: AsyncSession = Depends(get_db)):
    ...
```

The session is automatically committed on success and closed after the request.
