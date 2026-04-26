"""Smoke tests: every top-level page returns 200 with an empty DB.

These are breadth-over-depth coverage so CI catches import errors, template
syntax errors, missing context keys, and broken route wiring on every commit.
Deeper assertions live in the per-feature test files.
"""
import pytest

pytestmark = pytest.mark.db


# ---------------------------------------------------------------------------
# Top-level pages
# ---------------------------------------------------------------------------


async def test_smoke_dashboard(client):
    """GET / renders the home dashboard."""
    response = await client.get("/")
    assert response.status_code == 200
    assert "<html" in response.text.lower()


async def test_smoke_battery_page(client):
    """GET /battery renders the battery analytics page."""
    response = await client.get("/battery")
    assert response.status_code == 200


async def test_smoke_charging_performance(client):
    """GET /charging/performance renders the Charging Performance page."""
    response = await client.get("/charging/performance")
    assert response.status_code == 200


async def test_smoke_charging_costs(client):
    """GET /charging/costs renders the charging costs page."""
    response = await client.get("/charging/costs")
    assert response.status_code == 200


async def test_smoke_charging_sessions(client):
    """GET /charging/sessions renders the sessions list."""
    response = await client.get("/charging/sessions")
    assert response.status_code == 200


async def test_smoke_driving_sessions(client):
    """GET /driving/sessions renders the trip list ( rename)."""
    response = await client.get("/driving/sessions")
    assert response.status_code == 200


async def test_smoke_driving_performance(client):
    """GET /driving/performance renders the driving analytics page."""
    response = await client.get("/driving/performance")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------


async def test_smoke_review_queue_default(client):
    """GET /review renders the review queue with default Networks tab."""
    response = await client.get("/review")
    assert response.status_code == 200
    assert "Review Queue" in response.text


async def test_smoke_review_queue_locations_tab(client):
    """GET /review?tab=locations renders the locations tab."""
    response = await client.get("/review?tab=locations")
    assert response.status_code == 200


async def test_smoke_review_networks_partial(client):
    """GET /review/networks returns the networks table partial."""
    response = await client.get("/review/networks")
    assert response.status_code == 200


async def test_smoke_review_locations_partial(client):
    """GET /review/locations returns the locations table partial."""
    response = await client.get("/review/locations")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Settings tabs (phases 14, 16, 18, 20, 22, 23)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tab",
    ["general", "vehicles", "networks", "hass", "import", "gas-prices"],
)
async def test_smoke_settings_tabs(client, tab):
    """GET /settings?tab=<name> renders without error for each known tab."""
    response = await client.get(f"/settings?tab={tab}")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# CSV import
# ---------------------------------------------------------------------------


async def test_smoke_csv_import_template_download(client):
    """GET /settings/import/template returns a downloadable CSV template."""
    response = await client.get("/settings/import/template")
    assert response.status_code == 200
    # FileResponse or StreamingResponse with a CSV payload — loose content check
    body = response.text
    assert "," in body  # it's CSV-shaped
