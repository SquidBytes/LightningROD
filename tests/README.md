# LightningROD Testing Guide

## Quick Start

```bash
# Run all tests (starts test DB automatically)
./run-tests.sh

# Run only unit tests (no DB needed)
pytest -m unit

# Run a specific test category
./run-tests.sh -m query
./run-tests.sh -m ha_sim
./run-tests.sh -m db
```

## Architecture Overview

```
tests/
├── conftest.py                  # DB engine, migrations, transaction rollback
├── factories/                   # Deterministic test data generators
│   ├── __init__.py              # BaseFactory (seeded RNG, counter)
│   ├── vehicles.py              # VehicleFactory
│   ├── sessions.py              # ChargingSessionFactory
│   ├── battery.py               # BatteryStatusFactory
│   ├── trips.py                 # TripFactory
│   ├── energy.py                # StatisticsFactory
│   ├── locations.py             # LocationLookupFactory, LocationFactory
│   └── networks.py              # NetworkFactory, SubscriptionFactory
├── test_api/                    # API integration tests (httpx + FastAPI)
│   ├── conftest.py              # AsyncClient, dependency override
│   ├── test_sessions_api.py
│   └── test_settings_api.py
├── test_queries/                # Query layer validation (exact value assertions)
│   ├── conftest.py              # Scenario fixtures with known expected values
│   ├── test_costs.py
│   ├── test_battery.py
│   ├── test_energy.py
│   ├── test_trips.py
│   ├── test_dashboard.py
│   ├── test_sessions.py
│   ├── test_locations.py
│   ├── test_vehicles.py
│   └── test_comparisons.py
├── test_ha_sim/                 # Home Assistant WebSocket simulator
│   ├── simulator.py             # HASimulator server
│   ├── trigger.py               # Manual event injection CLI
│   ├── conftest.py              # Simulator fixture, processor state clearing
│   ├── test_connection.py       # Auth, protocol, event delivery
│   └── test_ingestion.py        # Event → DB record pipeline
├── test_unit/                   # Pure function tests (no DB, no network)
│   ├── test_cost_calc.py        # Cost cascade logic
│   └── test_hass_processor.py   # Unit conversions, parsing, slug extraction
├── test_csv_parser.py           # CSV import row transformation
└── test_location_resolution.py  # Haversine distance, address normalization
```

## Test Database

Tests use a dedicated Postgres 16 container defined in `docker/docker-compose.test.yml`:

| Setting        | Value                |
|----------------|----------------------|
| Port           | **5433** (not 5432)  |
| User           | `lightningrod_test`  |
| Password       | `testpass`           |
| Database       | `lightningrod_test`  |
| Storage        | tmpfs (in-memory)    |

The `run-tests.sh` script handles the full lifecycle:
1. Starts the container (`docker compose -f docker/docker-compose.test.yml up -d`)
2. Waits for the healthcheck to pass (up to 30s)
3. Runs pytest with `-x --tb=short` (stop on first failure)
4. Passes through any extra arguments to pytest

The container persists between runs. Data is ephemeral (tmpfs) but survives until the container is removed.

### Manual container management

```bash
# Start
docker compose -f docker/docker-compose.test.yml up -d test-db

# Stop
docker compose -f docker/docker-compose.test.yml down

# Stop and wipe volume
docker compose -f docker/docker-compose.test.yml down -v
```

## Test Isolation: Transaction Rollback

Every test runs inside a database transaction that rolls back when the test ends. This means:

- Tests cannot contaminate each other
- No cleanup SQL is needed
- Order does not matter
- Factories can insert freely without side effects

The mechanism lives in `tests/conftest.py`:
1. Alembic migrations run **once** at module load (via subprocess to avoid event loop conflicts)
2. Each test gets a `db_session` fixture backed by a savepoint
3. On teardown, the transaction rolls back — all inserted rows vanish

## Markers

Defined in `pyproject.toml`:

| Marker    | Meaning                                         | Needs DB? |
|-----------|------------------------------------------------|-----------|
| `db`      | Requires a real Postgres connection             | Yes       |
| `query`   | Query layer validation (uses scenario fixtures) | Yes       |
| `ha_sim`  | Uses the HA WebSocket simulator                 | Yes       |
| `unit`    | Pure function tests, no external dependencies   | No        |

### Running by marker

```bash
./run-tests.sh -m unit           # Fast, no DB
./run-tests.sh -m query          # Query layer only
./run-tests.sh -m ha_sim         # HA simulator only
./run-tests.sh -m db             # Everything that touches the DB
./run-tests.sh -m "not ha_sim"   # Skip simulator tests
```

## Factories

All factories live in `tests/factories/` and share a `BaseFactory` with:

- **Seeded RNG** (`seed=42`) — deterministic data across runs
- **Auto-incrementing counter** — unique IDs per factory call
- **Reset before each test** — `reset_factories` autouse fixture in root conftest

### Usage pattern

```python
async def test_something(db_session):
    vehicle = await VehicleFactory.create(db_session)
    session = await ChargingSessionFactory.create(db_session, device_id=vehicle.device_id)

    # Override any default
    trip = await TripFactory.create(db_session, distance_km=50.0, efficiency=3.5)
```

### Available factories

| Factory                  | Model              | Key defaults                             |
|--------------------------|--------------------|------------------------------------------|
| `VehicleFactory`         | `EVVehicle`        | Ford Mach-E, 91 kWh, unique device_id    |
| `ChargingSessionFactory` | `EVChargingSession` | Random 5-80 kWh, AC/DC type             |
| `BatteryStatusFactory`   | `EVBatteryStatus`  | SOC 10-100%, kW 0-150                    |
| `TripFactory`            | `EVTripMetrics`    | 5-100 mi, computed efficiency            |
| `StatisticsFactory`      | `EVStatistics`     | Energy totals, costs, efficiency         |
| `LocationLookupFactory`  | `LocationLookup`   | Named station with lat/lon              |
| `LocationFactory`        | `EVLocation`       | GPS snapshot with device_id              |
| `NetworkFactory`         | `ChargingNetwork`  | Network with cost_per_kwh 0.20-0.55      |
| `SubscriptionFactory`    | `NetworkSubscription` | Member rate, monthly fee, date range  |

### Adding a new factory

1. Create `tests/factories/your_model.py`
2. Import the model from `db.models.*`
3. Subclass or follow the existing pattern (async `create()` that calls `db.add()` + `db.flush()`)
4. Export from `tests/factories/__init__.py`

## Scenario Fixtures (Query Tests)

Query tests use **scenario fixtures** defined in `tests/test_queries/conftest.py`. Each scenario creates a known dataset and returns expected values for assertions:

| Fixture            | Creates                          | Expected values include                          |
|--------------------|----------------------------------|--------------------------------------------------|
| `cost_scenario`    | 6 sessions, 2 networks, 1 sub   | Total cost, per-network breakdown, savings       |
| `battery_scenario` | 20 battery records over 7 days   | SOC range, charge regions, capacity              |
| `energy_scenario`  | 10 sessions (5 AC + 5 DC)        | Total kWh, AC/DC split, avg efficiency           |
| `trip_scenario`    | 8 trips with known efficiency    | Total miles, avg efficiency                      |

### Writing a query test

```python
@pytest.mark.query
@pytest.mark.db
async def test_total_cost(db_session, cost_scenario):
    vehicle, expected = cost_scenario

    result = await get_cost_summary(db_session, vehicle.device_id)

    assert result.total_cost == pytest.approx(expected["total_cost"], abs=0.01)
```

The scenario fixtures ensure you're testing against **exact known values**, not random factory data.

## API Integration Tests

API tests use `httpx.AsyncClient` with FastAPI's `TestClient` pattern:

```python
@pytest.mark.db
async def test_my_endpoint(client):
    response = await client.get("/my/endpoint")
    assert response.status_code == 200
```

The `client` fixture (in `tests/test_api/conftest.py`):
- Builds a minimal FastAPI app with all routers mounted
- Overrides `get_db` dependency to use the test `db_session`
- Registers Jinja2 filters (e.g., `localtime`)

### Adding a new API test file

1. Create `tests/test_api/test_your_feature.py`
2. Use the `client` and optionally `db_session` fixtures
3. Mark with `@pytest.mark.db`
4. Create test data with factories before making requests

## Home Assistant WebSocket Simulator

The HA simulator (`tests/test_ha_sim/simulator.py`) is a WebSocket server that speaks the Home Assistant protocol:

```
Client connects → auth_required → client sends auth token
→ auth_ok (or auth_invalid) → get_config → get_states
→ subscribe_events → event dispatch loop
```

### Using the simulator in tests

```python
@pytest.mark.ha_sim
async def test_my_ha_feature(ha_simulator, db_session):
    # Inject an event
    ha_simulator.inject_event(
        entity_id="sensor.fordpass_TESTVIN_soc",
        new_state="85",
        old_state="80"
    )

    # Or run a scenario (multiple events with delay)
    events = [
        make_battery_event(soc=80),
        make_charging_session_event(energy=45.0),
    ]
    await ha_simulator.run_scenario(events, interval=0.1)
```

### Event generators

| Function                         | Entity type        | What it simulates           |
|----------------------------------|--------------------|-----------------------------|
| `make_charging_session_event()`  | energytransferlog  | Charging session completion |
| `make_trip_event()`              | elveh              | Trip with distance/energy   |
| `make_battery_event()`           | SOC sensor         | Battery state of charge     |
| `make_gps_event()`              | device_tracker     | GPS location update         |
| `make_temperature_event()`       | cabin temp         | Cabin temperature reading   |
| `make_lastrefresh_event()`       | lastrefresh        | Triggers battery data flush |

### Manual event injection

```bash
# Auto mode: start simulator, inject 10 events, stop
python -m tests.test_ha_sim.trigger --auto

# Types: charge, trip, battery, gps, temperature, random
```

### Processor state clearing

The `clear_processor_state` autouse fixture in `tests/test_ha_sim/conftest.py` resets module-level state in `hass_processor` before and after each test. This prevents cross-test contamination from cached trip values, pending battery status, etc.

## Unit Tests

Pure function tests in `tests/test_unit/` run without any database or network:

```bash
pytest -m unit  # ~0.3s, no Docker needed
```

These cover:
- **Cost calculations**: rate lookup cascade (free → stored → location → subscription → network), savings formulas, monthly fee proration
- **HA processor**: miles↔km, F↔C, Wh↔kWh conversions, slug extraction, safe parsing, charge type normalization

### Adding a unit test

1. Create `tests/test_unit/test_your_module.py`
2. Import the function directly — no fixtures needed
3. Mark with `@pytest.mark.unit`
4. Use plain `assert` statements or `pytest.approx()` for floats

## Running Tests

### Common patterns

```bash
# Everything
./run-tests.sh

# Stop on first failure (default)
./run-tests.sh -x

# Continue past failures
./run-tests.sh --no-header -x0

# Verbose output
./run-tests.sh -v

# Specific file
./run-tests.sh tests/test_queries/test_costs.py

# Specific test function
./run-tests.sh -k "test_total_cost"

# Multiple markers
./run-tests.sh -m "query and not ha_sim"

# Show print output
./run-tests.sh -s

# Parallel (if pytest-xdist installed)
./run-tests.sh -n auto
```

### Without the wrapper script

```bash
# Start DB manually
docker compose -f docker/docker-compose.test.yml up -d test-db

# Run pytest directly
pytest tests/ -x --tb=short

# Unit tests only (no DB needed)
pytest -m unit
```

## pytest Configuration

All pytest settings live in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
testpaths = ["tests"]
```

- `asyncio_mode = "auto"` — async test functions are detected automatically, no `@pytest.mark.asyncio` needed
- `asyncio_default_fixture_loop_scope = "function"` — each test gets its own event loop
- Deprecation warnings from pytest-asyncio are suppressed

## Extending the Test Suite

### New query module

1. Add a scenario fixture to `tests/test_queries/conftest.py` with known data and expected values
2. Create `tests/test_queries/test_your_query.py`
3. Mark tests with `@pytest.mark.query` and `@pytest.mark.db`
4. Assert exact values from the scenario's expected dict

### New API endpoint

1. Add the router to the app builder in `tests/test_api/conftest.py` (if not already included)
2. Create `tests/test_api/test_your_feature.py`
3. Use factories to seed data, then make HTTP requests via `client`

### New HA event type

1. Add an event generator function to `tests/test_ha_sim/simulator.py`
2. Add ingestion test to `tests/test_ha_sim/test_ingestion.py`
3. If the processor has module-level state, add cleanup to the `clear_processor_state` fixture

### New model

1. Create a factory in `tests/factories/`
2. Export it from `tests/factories/__init__.py`
3. Use it in whichever test category applies

## Troubleshooting

**Tests hang on startup**
The test DB container isn't healthy. Check `docker ps` and `docker logs` for the test-db container.

**"relation does not exist" errors**
Alembic migrations failed silently. Run manually:
```bash
POSTGRES_HOST=localhost POSTGRES_USER=lightningrod_test POSTGRES_PASSWORD=testpass \
POSTGRES_DB=lightningrod_test alembic upgrade head
```

**Cross-test contamination**
If a test passes alone but fails in a suite, check for module-level state that isn't being reset. Add cleanup to the relevant conftest.

**Port 5433 already in use**
Another test-db container is running: `docker compose -f docker/docker-compose.test.yml down` then retry.

**"Event loop is closed" errors**
Usually means a fixture scope mismatch. All DB fixtures should be function-scoped (the default).
