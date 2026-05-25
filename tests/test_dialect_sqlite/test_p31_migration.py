"""SQLite-backend assertions for the p31 data-source-foundation migration.

Verifies the post-migration shape of:
  - data_source_configs (table created, no row seeded on fresh install)
  - source_system value standardization across tables that carry it
  - app_settings legacy ha_url/ha_token keys removed
  - ev_vehicles.primary_source_id FK column reachable
"""

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import DataSourceConfig, EVVehicle


@pytest.mark.db
async def test_p31_no_seed_row_on_fresh_install(db_session: AsyncSession):
    """WR-05: when app_settings has no legacy ha_url/ha_token, no row is seeded.

    The fresh-install SQLite test DB has no legacy keys to copy from, so the
    ha_fordpass:default row should not exist. The table itself must exist
    (asserted by the count() executing against it without error).
    """
    result = await db_session.execute(
        select(DataSourceConfig).where(
            DataSourceConfig.source_name == "ha_fordpass",
            DataSourceConfig.instance_label == "default",
        )
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.db
async def test_p31_source_system_no_legacy_value(db_session: AsyncSession):
    # After migration, no row in any tracked table should carry 'home_assistant'.
    for table in (
        "ev_charging_session",
        "ev_battery_status",
        "ev_location",
        "ev_trip_metrics",
        "ev_vehicle_status",
        "ev_charging_networks",
        "ev_location_lookup",
        "ev_vehicles",
    ):
        result = await db_session.execute(
            text(f"SELECT count(*) FROM {table} WHERE source_system = 'home_assistant'")
        )
        assert result.scalar() == 0, f"{table} still has legacy source_system value"


@pytest.mark.db
async def test_p31_app_settings_legacy_keys_deleted(db_session: AsyncSession):
    result = await db_session.execute(
        text("SELECT count(*) FROM app_settings WHERE key IN ('ha_url', 'ha_token')")
    )
    assert result.scalar() == 0


@pytest.mark.db
async def test_p31_primary_source_id_column_reachable(db_session: AsyncSession):
    # New inserts default the FK to NULL; the migration's backfill only acted
    # on rows that existed at migration time.
    await db_session.execute(
        insert(EVVehicle).values(
            display_name="Test EV",
            device_id="test-vin-12345",
            vin="test-vin-12345",
            source_system="ha_fordpass",
        )
    )
    await db_session.flush()
    row = (
        await db_session.execute(
            select(EVVehicle).where(EVVehicle.vin == "test-vin-12345")
        )
    ).scalar_one()
    assert row.primary_source_id is None
