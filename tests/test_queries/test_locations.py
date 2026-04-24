"""Locations query layer validation tests.

Tests location resolution, geo-matching, and address normalization.
"""

import pytest

from web.queries.locations import (
    resolve_location,
)
from web.queries.settings import get_locations_for_network

pytestmark = [pytest.mark.query, pytest.mark.db]


async def test_get_locations_for_network(db_session):
    """Create network + locations -> verify locations_for_network returns correct set."""
    from db.models.reference import EVChargingNetwork, EVLocationLookup

    db = db_session

    net = EVChargingNetwork(
        network_name="Location Test Net",
        cost_per_kwh=0.35,
        is_free=False,
        is_verified=True,
    )
    db.add(net)
    await db.flush()

    loc1 = EVLocationLookup(
        location_name="Station A",
        network_id=net.id,
        location_type="public",
        is_verified=True,
    )
    loc2 = EVLocationLookup(
        location_name="Station B",
        network_id=net.id,
        location_type="public",
        is_verified=True,
    )
    loc_other = EVLocationLookup(
        location_name="Other Station",
        network_id=None,
        location_type="public",
        is_verified=True,
    )
    db.add_all([loc1, loc2, loc_other])
    await db.flush()

    locations = await get_locations_for_network(db, net.id)

    assert len(locations) == 2
    names = {loc.location_name for loc in locations}
    assert names == {"Station A", "Station B"}


async def test_resolve_location_geo_match(db_session):
    """Resolve location by GPS proximity match (within 100m)."""
    from db.models.reference import EVLocationLookup

    db = db_session

    existing = EVLocationLookup(
        location_name="Known Station",
        latitude=45.5000,
        longitude=-122.6500,
        location_type="public",
        is_verified=True,
        source_system="test",
    )
    db.add(existing)
    await db.flush()

    # Should match: within ~50m
    result_id = await resolve_location(
        db,
        latitude=45.5003,
        longitude=-122.6503,
    )

    assert result_id == existing.id


async def test_resolve_location_creates_new(db_session):
    """Resolve location creates new entry when no match found."""
    db = db_session

    result_id = await resolve_location(
        db,
        latitude=40.0000,
        longitude=-100.0000,
        location_name="New Station",
    )

    assert result_id is not None
    # Should be a new location
    from sqlalchemy import select

    from db.models.reference import EVLocationLookup

    loc = await db.execute(
        select(EVLocationLookup).where(EVLocationLookup.id == result_id)
    )
    loc_obj = loc.scalar_one()
    assert loc_obj.location_name == "New Station"


async def test_resolve_location_returns_none_no_data(db_session):
    """No lat/lon or address -> returns None."""
    result_id = await resolve_location(db_session)

    assert result_id is None
