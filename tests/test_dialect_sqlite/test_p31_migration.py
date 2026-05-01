"""SQLite-backend assertions for the p31 data-source-foundation migration.

Verifies the post-migration shape of:
  - data_source_configs (one seeded row, JSONStorage payload)
  - source_system value standardization across tables that carry it
  - app_settings legacy ha_url/ha_token keys removed
  - ev_vehicles.primary_source_id FK column reachable
"""

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import DataSourceConfig, EVVehicle


@pytest.mark.db
async def test_p31_seed_row_present(db_session: AsyncSession):
    result = await db_session.execute(
        select(DataSourceConfig).where(
            DataSourceConfig.source_name == "ha_fordpass",
            DataSourceConfig.instance_label == "default",
        )
    )
    row = result.scalar_one_or_none()
    assert row is not None
    assert isinstance(row.config_json, dict)
    assert "ha_url" in row.config_json
    assert "ha_token" in row.config_json
    assert row.enabled is True


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
