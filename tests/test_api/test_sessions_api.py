"""API integration tests for charging session endpoints."""

import pytest
from sqlalchemy import select

from db.models.charging_session import EVChargingSession
from tests.factories.locations import LocationLookupFactory
from tests.factories.networks import NetworkFactory
from tests.factories.sessions import ChargingSessionFactory
from tests.factories.vehicles import VehicleFactory


@pytest.mark.db
async def test_sessions_list_returns_200(client, db_session):
    """GET /charging/sessions returns 200."""
    response = await client.get("/charging/sessions")
    assert response.status_code == 200
    assert "Charging Sessions" in response.text


@pytest.mark.db
async def test_sessions_list_contains_table_structure(client, db_session):
    """GET /charging/sessions renders the sessions page with expected structure."""
    vehicle = await VehicleFactory.create(db_session)
    await ChargingSessionFactory.create(
        db_session,
        device_id=vehicle.device_id,
        energy_kwh=42.5,
    )
    response = await client.get("/charging/sessions")
    assert response.status_code == 200
    # Page structure assertions
    assert "sessions-table-region" in response.text
    assert "Add Session" in response.text


@pytest.mark.db
async def test_session_detail_returns_200(client, db_session):
    """GET /charging/sessions/{id}/detail returns 200 for existing session."""
    vehicle = await VehicleFactory.create(db_session)
    session = await ChargingSessionFactory.create(
        db_session,
        device_id=vehicle.device_id,
    )
    response = await client.get(f"/charging/sessions/{session.id}/detail")
    assert response.status_code == 200


@pytest.mark.db
async def test_session_new_modal_returns_200(client, db_session):
    """GET /charging/sessions/new/modal returns the new session form."""
    response = await client.get("/charging/sessions/new/modal")
    assert response.status_code == 200


@pytest.mark.db
async def test_bulk_update_sets_location_id(client, db_session):
    """PUT /charging/sessions/bulk applies bulk_location_id to selected sessions."""
    vehicle = await VehicleFactory.create(db_session)
    net = await NetworkFactory.create(db_session)
    loc = await LocationLookupFactory.create(db_session, network_id=net.id)
    session = await ChargingSessionFactory.create(
        db_session,
        device_id=vehicle.device_id,
    )
    await db_session.commit()

    response = await client.put(
        "/charging/sessions/bulk",
        data={
            "session_ids": str(session.id),
            "bulk_network_id": str(net.id),
            "bulk_location_id": str(loc.id),
        },
    )
    assert response.status_code == 200

    # Reload the session via a fresh query to confirm persisted values
    result = await db_session.execute(
        select(EVChargingSession).where(EVChargingSession.id == session.id)
    )
    reloaded = result.scalar_one()
    await db_session.refresh(reloaded)
    assert reloaded.location_id == loc.id
    assert reloaded.network_id == net.id


@pytest.mark.db
async def test_locations_by_network_returns_options(client, db_session):
    """GET /locations/by-network returns <option> fragment filtered by network."""
    net = await NetworkFactory.create(db_session)
    loc_a = await LocationLookupFactory.create(
        db_session, network_id=net.id, location_name="Alpha Station", is_verified=True
    )
    # Location for a different network — must not appear
    other_net = await NetworkFactory.create(db_session)
    await LocationLookupFactory.create(
        db_session, network_id=other_net.id, location_name="Bravo Station", is_verified=True
    )
    await db_session.commit()

    response = await client.get(f"/locations/by-network?network_id={net.id}")
    assert response.status_code == 200
    body = response.text
    assert f'value="{loc_a.id}"' in body
    assert "Alpha Station" in body
    assert "Bravo Station" not in body


@pytest.mark.db
async def test_locations_by_network_without_id_returns_placeholder(client, db_session):
    """GET /locations/by-network with no network_id returns a placeholder option."""
    response = await client.get("/locations/by-network")
    assert response.status_code == 200
    assert "select network first" in response.text.lower()
