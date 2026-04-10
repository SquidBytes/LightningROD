"""API integration tests for /driving/performance route. phase_25 Wave 0 stubs."""
import pytest

pytestmark = pytest.mark.db


async def test_phase_25_driving_performance_returns_200(client):
    """GET /driving/performance returns 200 with HTML body."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 1 route restructure task")


async def test_phase_25_driving_performance_range_filter_htmx(client):
    """GET /driving/performance?range=7d with HX-Request header returns partial HTML."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 1 route restructure task")


async def test_phase_25_driving_performance_driving_efficiency_title(client):
    """Response body contains the string 'Driving Efficiency' as card title."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 1 route restructure task")


async def test_phase_25_driving_performance_range_recovered_tile_present(client):
    """Response body contains a 'Range Recovered' summary tile (moved from charging)."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 1 route restructure task")


async def test_phase_25_driving_performance_temperature_scatter_card(client):
    """Response body contains a card with id or title indicating temperature correlation."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 2 temperature scatter task")


async def test_phase_25_driving_performance_regen_bar_card(client):
    """Response body contains a card with id or title indicating regen recovery."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 2 regen bar task")
