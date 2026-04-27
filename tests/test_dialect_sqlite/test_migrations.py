"""alembic upgrade head smoke against fresh SQLite."""
import pytest
from sqlalchemy import inspect


@pytest.mark.db
async def test_all_tables_created(db_session):
    """alembic upgrade head completes against SQLite and creates expected tables.

    The squashed initial migration p30_squashed_initial (Plan 30-03) is the
    only migration that runs here; this test asserts that every core table
    declared by the ORM is actually created on the SQLite backend.
    """

    def _list_tables(sync_session):
        return inspect(sync_session.connection()).get_table_names()

    tables = await db_session.run_sync(_list_tables)
    expected = {
        "ev_charging_session",
        "ev_battery_status",
        "ev_vehicle_status",
        "ev_trip_metrics",
        "ev_location_lookup",
        "ev_charging_networks",
        "ev_vehicles",
        "app_settings",
        "alembic_version",
    }
    missing = expected - set(tables)
    assert not missing, f"missing tables: {missing}"
