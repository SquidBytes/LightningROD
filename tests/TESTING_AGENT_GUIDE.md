# Testing Agent Guide

Instructions for AI agents working on LightningROD tests. Read this before writing or modifying tests.

## Before You Start

1. **Read `tests/README.md`** for the full architecture overview
2. **Read `tests/conftest.py`** to understand DB setup and the `db_session` fixture
3. **Read the relevant conftest** for the test category you're working in

## Running Tests

```bash
# From the repository root
./run-tests.sh                     # All tests
./run-tests.sh -m unit             # No DB needed, fast
./run-tests.sh -m query            # Query layer only
./run-tests.sh tests/test_api/     # Specific directory
./run-tests.sh -k "test_name"      # Specific test
```

Always run the relevant test subset after making changes. Run the full suite before committing.

## Key Rules

### Transaction rollback — do NOT manually clean up data

Every test runs in a rolled-back transaction. Never write cleanup code like:

```python
# WRONG — unnecessary, the transaction rolls back automatically
await db.execute(text("DELETE FROM ev_vehicles"))
```

Just create data with factories and assert against it.

### Async everything

All DB tests must be `async def`. pytest-asyncio is in `auto` mode — you do not need `@pytest.mark.asyncio` decorators.

```python
# Correct
async def test_something(db_session):
    ...

# Wrong — will not interact with the DB correctly
def test_something(db_session):
    ...
```

### Use factories, not raw SQL

```python
# Correct
vehicle = await VehicleFactory.create(db_session, year=2024)

# Wrong — bypasses model validation and is fragile
await db_session.execute(text("INSERT INTO ev_vehicles ..."))
```

### Mark your tests

Every test must have the appropriate marker:

```python
@pytest.mark.db        # Touches the database
@pytest.mark.query     # Query layer test (implies db)
@pytest.mark.ha_sim    # Uses HA simulator (implies db)
@pytest.mark.unit      # Pure function, no DB
```

### Use `pytest.approx()` for floats

```python
assert result.total == pytest.approx(54.50, abs=0.01)
```

### Use scenario fixtures for query tests

Don't create ad-hoc data in query tests. Use or extend the scenario fixtures in `tests/test_queries/conftest.py`. Each scenario returns `(vehicle, expected_dict)` with pre-computed expected values.

## How to Add Tests for New Features

### New API endpoint

1. Ensure the router is included in `tests/test_api/conftest.py` app builder
2. Create test file in `tests/test_api/`
3. Use `client` fixture for HTTP requests, `db_session` for data setup

```python
@pytest.mark.db
async def test_new_endpoint(client, db_session):
    vehicle = await VehicleFactory.create(db_session)
    response = await client.get(f"/vehicles/{vehicle.device_id}")
    assert response.status_code == 200
```

### New query function

1. Check if an existing scenario fixture covers your data needs
2. If not, add a new scenario to `tests/test_queries/conftest.py`:

```python
@pytest_asyncio.fixture
async def my_scenario(db_session):
    vehicle = await VehicleFactory.create(db_session)
    # Create exact known data
    await SomeFactory.create(db_session, device_id=vehicle.device_id, value=42.0)

    expected = {
        "total": 42.0,
        "count": 1,
    }
    return vehicle, expected
```

3. Write tests that assert against `expected`:

```python
@pytest.mark.query
@pytest.mark.db
async def test_my_query(db_session, my_scenario):
    vehicle, expected = my_scenario
    result = await my_query_function(db_session, vehicle.device_id)
    assert result.total == pytest.approx(expected["total"], abs=0.01)
```

### New model / DB table

1. Create a factory in `tests/factories/your_model.py`:

```python
from db.models.your_module import YourModel
from tests.factories import BaseFactory

class YourModelFactory(BaseFactory):
    @classmethod
    async def create(cls, db, **overrides):
        n = cls._next_id()
        obj = YourModel(
            name=overrides.get("name", f"Test Item {n}"),
            value=overrides.get("value", cls._random_float(1.0, 100.0)),
        )
        db.add(obj)
        await db.flush()
        return obj
```

2. Add to `tests/factories/__init__.py` exports
3. Use it in tests: `item = await YourModelFactory.create(db_session)`

### New HA event type

1. Add an event generator to `tests/test_ha_sim/simulator.py`:

```python
def make_your_event(**overrides):
    return {
        "entity_id": f"sensor.fordpass_TESTVIN_your_sensor",
        "new_state": {"state": overrides.get("value", "42"), ...},
        "old_state": {"state": "0", ...},
    }
```

2. Add ingestion test to `tests/test_ha_sim/test_ingestion.py`
3. If the processor caches state, add cleanup to `clear_processor_state` in `tests/test_ha_sim/conftest.py`

### New pure function / utility

1. Create `tests/test_unit/test_your_module.py`
2. Import the function directly, no fixtures needed
3. Mark with `@pytest.mark.unit`

```python
@pytest.mark.unit
def test_my_function():
    assert my_function(input) == expected_output
```

## Common Fixtures Reference

| Fixture              | Scope    | Source                          | Provides                                    |
|----------------------|----------|---------------------------------|---------------------------------------------|
| `db_session`         | function | `tests/conftest.py`             | Async SQLAlchemy session (auto-rollback)    |
| `reset_factories`    | function | `tests/conftest.py`             | Autouse — resets factory RNG seed + counter |
| `client`             | function | `tests/test_api/conftest.py`    | httpx.AsyncClient bound to test app         |
| `ha_simulator`       | function | `tests/test_ha_sim/conftest.py` | Running HASimulator instance                |
| `clear_processor_state` | function | `tests/test_ha_sim/conftest.py` | Autouse — clears hass_processor caches   |
| `cost_scenario`      | function | `tests/test_queries/conftest.py`| (vehicle, expected_dict) for cost tests     |
| `battery_scenario`   | function | `tests/test_queries/conftest.py`| (vehicle, expected_dict) for battery tests  |
| `energy_scenario`    | function | `tests/test_queries/conftest.py`| (vehicle, expected_dict) for energy tests   |
| `trip_scenario`      | function | `tests/test_queries/conftest.py`| (vehicle, expected_dict) for trip tests     |

## Factory Defaults Quick Reference

| Factory                  | Unique field            | Key defaults                    |
|--------------------------|-------------------------|---------------------------------|
| `VehicleFactory`         | `device_id=TEST_VIN_N`  | Ford Mach-E, 91 kWh            |
| `ChargingSessionFactory` | auto ID                 | 5-80 kWh, random AC/DC         |
| `BatteryStatusFactory`   | auto ID                 | SOC 10-100%, kW 0-150          |
| `TripFactory`            | auto ID                 | 5-100 mi, computed efficiency   |
| `StatisticsFactory`      | auto ID                 | Random energy/cost totals       |
| `LocationLookupFactory`  | auto ID                 | Named station, lat/lon          |
| `LocationFactory`        | auto ID                 | GPS snapshot                    |
| `NetworkFactory`         | auto ID                 | cost_per_kwh 0.20-0.55         |
| `SubscriptionFactory`    | auto ID                 | Member rate, monthly fee        |

All factories accept `**overrides` — pass any model field as a keyword argument to replace the default.

## Gotchas

- **Never commit PII** — use `TESTVIN`, `Test Vehicle`, placeholder addresses. See CLAUDE.md.
- **Port 5433** — test DB runs on 5433, not 5432. If tests can't connect, check the container is running.
- **Module-level state** — `hass_processor` caches values between calls. The `clear_processor_state` fixture handles this for `test_ha_sim/`. If you see cross-test leakage elsewhere, check for module-level dicts/variables.
- **Alembic runs once** — migrations apply at module load. If you add a new migration, restart the test run (or stop/start the test DB container to reset).
- **Factory counter resets per test** — IDs like `TEST_VIN_001` repeat across tests. This is fine because each test has its own transaction. But don't rely on globally unique factory IDs within a single test session.
- **Scenario expected values are hardcoded** — if you change scenario fixture data, update the expected dict to match. The whole point is exact value assertions.
