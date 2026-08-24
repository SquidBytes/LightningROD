"""API tests for the event-archive settings card."""

import pytest

from web.queries.settings import get_app_setting

pytestmark = pytest.mark.db


async def test_general_tab_renders_the_archive_card_enabled(client):
    """A fresh install shows the archive on, with the default retention window."""
    response = await client.get("/settings")

    assert response.status_code == 200
    assert 'name="raw_archive_enabled"' in response.text
    assert 'name="raw_archive_retention_days"' in response.text
    assert 'value="90"' in response.text


async def test_saving_unchecked_disables_the_archive(client, db_session):
    """An unchecked box posts no field at all and must persist as off."""
    response = await client.post(
        "/settings/raw-archive", data={"raw_archive_retention_days": "30"}
    )

    assert response.status_code == 200
    assert await get_app_setting(db_session, "raw_archive_enabled") == "false"
    assert await get_app_setting(db_session, "raw_archive_retention_days") == "30"


async def test_saving_checked_enables_and_clamps_retention(client, db_session):
    response = await client.post(
        "/settings/raw-archive",
        data={"raw_archive_enabled": "on", "raw_archive_retention_days": "-3"},
    )

    assert response.status_code == 200
    assert await get_app_setting(db_session, "raw_archive_enabled") == "true"
    assert await get_app_setting(db_session, "raw_archive_retention_days") == "0"


async def test_saving_drops_the_writer_settings_cache(client):
    """The ingestion writer must not serve a stale copy after a save."""
    from web.services.ingestion.raw_archive import raw_archive

    raw_archive._settings = {"enabled": True, "retention_days": 90}

    await client.post("/settings/raw-archive", data={"raw_archive_enabled": "on"})

    assert raw_archive._settings is None
