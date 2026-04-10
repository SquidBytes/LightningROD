"""Sidebar link presence tests. phase_25 Wave 0 stubs."""
import pytest

pytestmark = pytest.mark.db


async def test_phase_25_sidebar_has_trip_sessions_link(client):
    """Home page sidebar includes an anchor to /driving/sessions with label 'Trip Sessions'."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 1 route restructure task")


async def test_phase_25_sidebar_has_driving_analytics_link(client):
    """Home page sidebar includes an anchor to /driving/performance with label 'Analytics'."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 1 route restructure task")


async def test_phase_25_sidebar_no_bare_trips_link(client):
    """Home page sidebar no longer contains href=\"/trips\" (clean rename)."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 1 route restructure task")
