"""API integration tests for renamed /driving/sessions route. phase_25 Wave 0 stubs."""
import pytest

pytestmark = pytest.mark.db


async def test_phase_25_driving_sessions_returns_200(client):
    """GET /driving/sessions returns 200 (successor to /trips)."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 1 route restructure task")


async def test_phase_25_old_trips_url_returns_404(client):
    """GET /trips returns 404 (clean break, no redirect)."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 1 route restructure task")


async def test_phase_25_driving_sessions_no_efficiency_trend_chart(client):
    """Response body does NOT contain an 'Efficiency Trend' or 'Driving Efficiency' card (moved to analytics)."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 1 route restructure task")


async def test_phase_25_driving_sessions_htmx_range_filter(client):
    """GET /driving/sessions?range=7d with HX-Request returns partial."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 1 route restructure task")
