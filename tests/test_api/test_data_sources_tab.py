"""Integration tests for Settings Data Sources tab + per-source POST handler."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from db.models.data_source_config import DataSourceConfig
from web.routes.settings import _mask_token

pytestmark = pytest.mark.db


async def _seed_default(db_session, *, ha_url: str, ha_token: str, **extra):
    """Update the migration-seeded ha_fordpass:default row to known values."""
    result = await db_session.execute(
        select(DataSourceConfig).where(
            DataSourceConfig.source_name == "ha_fordpass",
            DataSourceConfig.instance_label == "default",
        )
    )
    row = result.scalar_one()
    row.config_json = {"ha_url": ha_url, "ha_token": ha_token, **extra}
    await db_session.commit()
    return row


async def test_get_data_sources_tab_renders_one_card(client, db_session):
    """GET /settings/data-sources renders the unified Home Assistant card.

    ha_fordpass + ha_gas_price are consolidated into a single card with
    "FordPass" and "Gas Price Sensors" sub-sections.
    """
    await _seed_default(
        db_session,
        ha_url="http://homeassistant.local:8123",
        ha_token="abcdefgh12345678",
    )

    response = await client.get("/settings/data-sources")
    assert response.status_code == 200
    body = response.text
    assert ">Home Assistant<" in body
    assert ">FordPass<" in body
    assert ">Gas Price Sensors<" in body
    assert "http://homeassistant.local:8123" in body
    # Masked token: last 8 chars visible, prior chars asterisks
    assert "********12345678" in body or "***12345678" in body
    # Token must NOT appear in plaintext in the rendered HTML
    assert "abcdefgh12345678" not in body


async def test_post_data_source_saves_real_token(client, db_session):
    """POST with a real (non-masked) token persists ha_url, ha_token, options."""
    response = await client.post(
        "/settings/data-sources/ha_fordpass",
        data={
            "ha_url": "http://homeassistant.local:8123",
            "ha_token": "newrealtoken9999",
            "ha_unit_system": "metric",
            "ha_auto_connect": "true",
        },
    )
    assert response.status_code == 200
    result = await db_session.execute(
        select(DataSourceConfig).where(
            DataSourceConfig.source_name == "ha_fordpass",
            DataSourceConfig.instance_label == "default",
        )
    )
    row = result.scalar_one()
    await db_session.refresh(row)
    assert row.config_json["ha_token"] == "newrealtoken9999"
    assert row.config_json["ha_url"] == "http://homeassistant.local:8123"
    assert row.config_json["ha_unit_system"] == "metric"
    assert row.config_json["ha_auto_connect"] is True


async def test_post_data_source_masked_token_preserves_existing(client, db_session):
    """POST with a masked-token placeholder must NOT overwrite the stored token."""
    original_token = "originaltoken1234"
    row = await _seed_default(
        db_session,
        ha_url="http://x",
        ha_token=original_token,
    )

    response = await client.post(
        "/settings/data-sources/ha_fordpass",
        data={
            "ha_url": "http://x",
            "ha_token": _mask_token(original_token),
            "ha_unit_system": "auto",
            "ha_auto_connect": "true",
        },
    )
    assert response.status_code == 200
    await db_session.refresh(row)
    assert row.config_json["ha_token"] == original_token


async def test_post_data_source_validation_error(client, db_session):
    """Empty ha_url violates min_length=1 and returns 422 with field name surfaced."""
    response = await client.post(
        "/settings/data-sources/ha_fordpass",
        data={
            "ha_url": "",
            "ha_token": "x",
            "ha_unit_system": "auto",
        },
    )
    assert response.status_code == 422
    assert "ha_url" in response.text


async def test_post_data_source_inserts_row_on_fresh_install(client, db_session):
    """Fresh install: no seed row exists, first save INSERTs one.

    Locks the upsert behavior introduced after WR-05 stopped seeding the
    ha_fordpass:default row when no legacy app_settings ha_url/ha_token
    were present to copy from.
    """
    from sqlalchemy import delete

    await db_session.execute(
        delete(DataSourceConfig).where(
            DataSourceConfig.source_name == "ha_fordpass",
            DataSourceConfig.instance_label == "default",
        )
    )
    await db_session.commit()

    response = await client.post(
        "/settings/data-sources/ha_fordpass",
        data={
            "ha_url": "http://homeassistant.local:8123",
            "ha_token": "freshinstalltoken",
            "ha_unit_system": "auto",
            "ha_auto_connect": "true",
        },
    )
    assert response.status_code == 200

    result = await db_session.execute(
        select(DataSourceConfig).where(
            DataSourceConfig.source_name == "ha_fordpass",
            DataSourceConfig.instance_label == "default",
        )
    )
    row = result.scalar_one()
    assert row.config_json["ha_token"] == "freshinstalltoken"
    assert row.config_json["ha_url"] == "http://homeassistant.local:8123"
    assert row.enabled is True


async def test_post_unknown_source_404(client):
    """POST to an unregistered source_name returns 404."""
    response = await client.post(
        "/settings/data-sources/bogus",
        data={"ha_url": "x", "ha_token": "y"},
    )
    assert response.status_code == 404


async def test_settings_index_no_longer_has_hass_tab(client):
    """Settings index renders without the old Home Assistant tab or /settings/hass refs."""
    response = await client.get("/settings")
    assert response.status_code == 200
    body = response.text
    assert 'aria-label="Data Sources"' in body
    assert 'aria-label="Home Assistant"' not in body
    assert "/settings/hass" not in body
