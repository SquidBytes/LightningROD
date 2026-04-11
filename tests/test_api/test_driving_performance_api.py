"""API integration tests for /driving/performance route. phase_25 Wave 1–2."""
import pytest

pytestmark = pytest.mark.db


async def test_phase_25_driving_performance_returns_200(client):
    """GET /driving/performance returns 200 with HTML body."""
    response = await client.get("/driving/performance")
    assert response.status_code == 200
    assert "Driving Analytics" in response.text


async def test_phase_25_driving_performance_range_filter_htmx(client):
    """GET /driving/performance?range=7d with HX-Request header returns the partial."""
    response = await client.get(
        "/driving/performance?range=7d",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    # Partial does NOT include the <h1> page heading, but does include
    # the summary card headings that only live in partials/summary.html.
    assert "Driving Analytics" not in response.text
    assert "Total Distance" in response.text


async def test_phase_25_driving_performance_driving_efficiency_title(client):
    """Response body contains the card title 'Driving Efficiency' (renamed from 'Efficiency Trend')."""
    response = await client.get("/driving/performance")
    assert response.status_code == 200
    assert "Driving Efficiency" in response.text


async def test_phase_25_driving_performance_range_recovered_tile_present(client):
    """Response body contains the 'Range Recovered' summary tile (moved from charging)."""
    response = await client.get("/driving/performance")
    assert response.status_code == 200
    assert "Range Recovered" in response.text


async def test_phase_25_driving_performance_temperature_scatter_card(client):
    """Response body contains the temperature scatter card element (always in DOM)."""
    response = await client.get("/driving/performance")
    assert response.status_code == 200
    # Card id is rendered unconditionally by the template; title is stable.
    assert "driving-temperature-scatter-card" in response.text
    assert "Efficiency vs Temperature" in response.text


async def test_phase_25_driving_performance_regen_bar_card(client):
    """Response body contains the regen recovery bar chart card element (always in DOM)."""
    response = await client.get("/driving/performance")
    assert response.status_code == 200
    assert "driving-regen-bar-card" in response.text
    assert "Regen Recovery" in response.text
