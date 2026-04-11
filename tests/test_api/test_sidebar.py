"""Sidebar link presence tests. phase_25 Wave 1 — navigation restructure."""
import pytest

pytestmark = pytest.mark.db


async def test_phase_25_sidebar_has_trip_sessions_link(client):
    """Home page sidebar includes an anchor to /driving/sessions with label 'Trip Sessions'."""
    response = await client.get("/")
    assert response.status_code == 200
    assert 'href="/driving/sessions"' in response.text
    assert "Trip Sessions" in response.text


async def test_phase_25_sidebar_has_driving_analytics_link(client):
    """Home page sidebar includes an anchor to /driving/performance labelled 'Analytics'."""
    response = await client.get("/")
    assert response.status_code == 200
    assert 'href="/driving/performance"' in response.text
    # Sidebar label is "Analytics" (distinct from /charging/performance's "Performance")
    assert "Analytics" in response.text


async def test_phase_25_sidebar_no_bare_trips_link(client):
    """Home page sidebar no longer contains href="/trips" (clean rename, no redirect)."""
    response = await client.get("/")
    assert response.status_code == 200
    assert 'href="/trips"' not in response.text
