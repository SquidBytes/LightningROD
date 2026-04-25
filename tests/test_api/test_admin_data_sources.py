"""Integration tests for GET /admin/data-sources."""

import pytest
from httpx import AsyncClient

pytestmark = [pytest.mark.db]


async def test_data_sources_page_returns_200(client: AsyncClient):
    r = await client.get("/admin/data-sources")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


async def test_data_sources_page_lists_ha_fordpass_source(client: AsyncClient):
    r = await client.get("/admin/data-sources")
    assert r.status_code == 200
    # Source name header rendered per Jinja block
    assert "ha_fordpass" in r.text


async def test_data_sources_page_contains_table_headers(client: AsyncClient):
    r = await client.get("/admin/data-sources")
    # "Declared Unit" + "Effective Unit" + "Resolution Method" 
    # replace the prior "Source Unit" column to surface
    # the adapter's per-event unit resolution (declared / ha_unit_system /
    # read_time_uom / declared_fallback).
    for header in [
        "Source Entity",
        "Source Attribute",
        "Declared Unit",
        "Effective Unit",
        "Resolution Method",
        "DB Column",
        "Last Raw Value",
    ]:
        assert header in r.text, f"Missing column header '{header}'"


async def test_data_sources_page_renders_every_contract(client: AsyncClient):
    """Every FIELD_CONTRACTS entry must appear in the rendered HTML."""
    from web.services.sources.ha_fordpass.adapter import FIELD_CONTRACTS
    r = await client.get("/admin/data-sources")
    for contract in FIELD_CONTRACTS:
        assert contract.target_db_column in r.text, (
            f"Contract {contract.target_db_column} missing from rendered page"
        )


async def test_data_sources_sidebar_link_shown_when_dev_mode_enabled(client: AsyncClient):
    """Nav link appears in base.html only when developer mode is on."""
    import web.developer_mode as dm
    dm.set_enabled(True)
    try:
        r = await client.get("/admin/data-sources")
        assert 'href="/admin/data-sources"' in r.text
    finally:
        dm.set_enabled(False)


async def test_data_sources_sidebar_link_hidden_when_dev_mode_disabled(client: AsyncClient):
    """Nav link is absent from base.html when developer mode is off."""
    import web.developer_mode as dm
    dm.set_enabled(False)
    r = await client.get("/admin/data-sources")
    assert 'href="/admin/data-sources"' not in r.text


async def test_data_sources_page_handles_empty_last_seen(client: AsyncClient):
    """Before any ingest, _last_seen_raw is empty; page must still render."""
    from web.services.sources.ha_fordpass import adapter
    adapter._last_seen_raw.clear()
    r = await client.get("/admin/data-sources")
    assert r.status_code == 200
    assert "never observed" in r.text
