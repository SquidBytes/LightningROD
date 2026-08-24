"""App-settings typed readers."""

import pytest

from web.queries.settings import (
    RAW_ARCHIVE_DEFAULT_RETENTION_DAYS,
    get_raw_archive_settings,
    set_app_setting,
)

pytestmark = [pytest.mark.query, pytest.mark.db]


async def test_raw_archive_defaults_when_keys_absent(db_session):
    """A fresh install archives events and keeps them for the default window."""
    settings = await get_raw_archive_settings(db_session)

    assert settings["enabled"] is True
    assert settings["retention_days"] == RAW_ARCHIVE_DEFAULT_RETENTION_DAYS


async def test_raw_archive_reads_saved_values(db_session):
    await set_app_setting(db_session, "raw_archive_enabled", "false")
    await set_app_setting(db_session, "raw_archive_retention_days", "30")

    settings = await get_raw_archive_settings(db_session)

    assert settings["enabled"] is False
    assert settings["retention_days"] == 30


async def test_raw_archive_zero_retention_means_forever(db_session):
    await set_app_setting(db_session, "raw_archive_retention_days", "0")

    assert (await get_raw_archive_settings(db_session))["retention_days"] == 0


async def test_raw_archive_unparseable_retention_falls_back(db_session):
    """A non-numeric value returns the default rather than raising."""
    await set_app_setting(db_session, "raw_archive_retention_days", "forever")

    settings = await get_raw_archive_settings(db_session)

    assert settings["retention_days"] == RAW_ARCHIVE_DEFAULT_RETENTION_DAYS


async def test_raw_archive_negative_retention_clamps_to_forever(db_session):
    await set_app_setting(db_session, "raw_archive_retention_days", "-5")

    assert (await get_raw_archive_settings(db_session))["retention_days"] == 0
