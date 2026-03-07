# Database

LightningROD uses PostgreSQL 16 with SQLAlchemy 2.0 in async mode. The schema covers the full ha-fordpass data model plus reference tables for networks, locations, stalls, and settings.

## Schema Overview

### Core Tables

| Table | Purpose |
|-------|---------|
| `ev_charging_session` | Charging events: timing, energy, cost, location, SOC, EVSE data |
| `ev_battery_status` | HV/12V battery snapshots: SOC, voltage, current, temperature |
| `ev_vehicle_status` | Drivetrain, temperatures, tire pressure, door locks |
| `ev_trip_metrics` | Per-trip energy, distance, efficiency, driving scores |
| `ev_location` | GPS snapshots with optional reverse geocoding |

### Reference Tables

| Table | Purpose |
|-------|---------|
| `ev_charging_networks` | Network definitions with cost_per_kwh and color |
| `ev_location_lookup` | Known locations with network FK and optional cost override |
| `ev_charger_stalls` | Charger configurations per location (type, rated kW, connector) |
| `app_settings` | Key-value store for user preferences and toggles |
| `ev_statistics` | Aggregate statistics summary (single row, recomputed) |

### Key Relationships

```
ev_charging_networks
    ├── ev_location_lookup (network_id FK)
    │       └── ev_charger_stalls (location_id FK)
    └── ev_charging_session (network_id FK, stall_id FK)
```

### Charging Session Fields

The `ev_charging_session` table includes:

- **Identity**: `id`, `session_id` (UUID), `device_id`
- **Type/Location**: `charge_type`, `location_name`, `location_type`, `network_id`, `location_id`, `is_free`
- **Power**: `charging_voltage`, `charging_amperage`, `charging_kw`, `max_power`, `min_power`
- **Timestamps**: `session_start_utc`, `session_end_utc`, `estimated_end_utc`, `recorded_at`
- **Duration**: `charge_duration_seconds`, `plugged_in_duration_seconds`
- **Energy/SOC**: `start_soc`, `end_soc`, `energy_kwh`, `miles_added`
- **Cost**: `cost`, `cost_source`, `estimated_cost`, `cost_without_overrides`
- **EVSE**: `evse_voltage`, `evse_amperage`, `evse_kw`, `evse_energy_kwh`, `evse_max_power_kw`, `charger_rated_kw`, `evse_source`, `stall_id`
- **Location data**: `address`, `latitude`, `longitude`
- **Metadata**: `source_system`, `ingested_at`, `original_timestamp`

## Connection Management

```python title="db/engine.py"
--8<-- "db/engine.py"
```

Key settings:

- `pool_pre_ping=True` -- validates connections before use
- `pool_recycle=3600` -- refreshes connections every hour
- `asyncpg` driver for native async PostgreSQL access

## Models

Models live in `db/models/` with one file per domain area. The `__init__.py` imports all model classes so Alembic's autogenerate discovers every table:

```python title="db/models/__init__.py"
--8<-- "db/models/__init__.py"
```

## Migrations

Alembic manages schema versioning with an async-compatible `env.py`.

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
