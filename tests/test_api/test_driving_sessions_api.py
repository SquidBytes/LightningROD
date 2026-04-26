"""API integration tests for renamed /driving/sessions route. phase_25 Wave 1."""
import pytest

pytestmark = pytest.mark.db


async def test_phase_25_driving_sessions_returns_200(client):
    """GET /driving/sessions returns 200 (successor to /trips)."""
    response = await client.get("/driving/sessions")
    assert response.status_code == 200
    assert "Trip History" in response.text


async def test_phase_25_old_trips_url_returns_404(client):
    """GET /trips returns 404 (clean break, no redirect)."""
    response = await client.get("/trips")
    assert response.status_code == 404


async def test_phase_25_driving_sessions_no_efficiency_trend_chart(client):
    """Response body does NOT contain an 'Efficiency Trend' or 'Driving Efficiency' card."""
    response = await client.get("/driving/sessions")
    assert response.status_code == 200
    assert "Efficiency Trend" not in response.text
    # 'Driving Efficiency' card is on /driving/performance, not here.
    # Note: the page subtitle says "Track driving efficiency…" (lowercase) — we
    # only want to catch the capitalized card title.
    assert "Driving Efficiency" not in response.text


async def test_phase_25_driving_sessions_htmx_range_filter(client):
    """GET /driving/sessions?range=7d with HX-Request returns the partial."""
    response = await client.get(
        "/driving/sessions?range=7d",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    # Partial omits the full page <h1>; summary card title is present.
    assert "Trip History" not in response.text
    assert "Total Trips" in response.text
