"""API integration tests for settings endpoints."""

import pytest


@pytest.mark.db
async def test_settings_index_returns_200(client, db_session):
    """GET /settings returns 200 with settings page."""
    response = await client.get("/settings")
    assert response.status_code == 200
    assert "Settings" in response.text


@pytest.mark.db
async def test_settings_vehicles_tab(client, db_session):
    """GET /settings?tab=vehicles returns 200 with vehicles tab active."""
    response = await client.get("/settings?tab=vehicles")
    assert response.status_code == 200


@pytest.mark.db
async def test_create_vehicle_via_post(client, db_session):
    """POST /settings/vehicles creates a vehicle and returns 200."""
    response = await client.post(
        "/settings/vehicles",
        data={
            "display_name": "Test Mach-E",
            "make": "Ford",
            "model": "Mustang Mach-E",
            "year": "2024",
            "device_id": "POST_TEST_VIN_001",
        },
    )
    assert response.status_code == 200
    assert "Test Mach-E" in response.text
