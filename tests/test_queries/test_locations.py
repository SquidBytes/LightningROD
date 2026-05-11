"""Locations query layer validation tests.

Tests location resolution, geo-matching, and address normalization.
"""

import pytest

from web.queries.locations import (
    get_location_network_id,
    inherit_network_from_location,
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


# ---------------------------------------------------------------------------
# get_location_network_id + inherit_network_from_location
# ---------------------------------------------------------------------------


async def test_get_location_network_id_returns_attached_network(db_session):
    """Location with a network -> helper returns that network's id."""
    from db.models.reference import EVChargingNetwork, EVLocationLookup

    db = db_session
    net = EVChargingNetwork(network_name="Helper Net", is_verified=True)
    db.add(net)
    await db.flush()
    loc = EVLocationLookup(
        location_name="Helper Loc",
        network_id=net.id,
        is_verified=False,
        source_system="ha_fordpass",
    )
    db.add(loc)
    await db.flush()

    assert await get_location_network_id(db, loc.id) == net.id


async def test_get_location_network_id_returns_none_when_unset(db_session):
    """Location with no network -> helper returns None."""
    from db.models.reference import EVLocationLookup

    db = db_session
    loc = EVLocationLookup(
        location_name="Bare Loc",
        network_id=None,
        is_verified=False,
        source_system="ha_fordpass",
    )
    db.add(loc)
    await db.flush()

    assert await get_location_network_id(db, loc.id) is None


async def test_get_location_network_id_returns_none_for_missing_id(db_session):
    """Helper returns None for None/0/missing location_id without querying."""
    assert await get_location_network_id(db_session, None) is None
    assert await get_location_network_id(db_session, 0) is None
    # Non-existent id resolves cleanly via scalar_one_or_none
    assert await get_location_network_id(db_session, 999_999) is None


async def test_inherit_network_from_location_passes_through_when_set(db_session):
    """If network_id is already set, return it unchanged (no DB lookup needed)."""
    # No location seeded; the helper must short-circuit on the network_id check.
    assert await inherit_network_from_location(db_session, 42, 999) == 42


async def test_inherit_network_from_location_inherits_when_unset(db_session):
    """network_id=None + location with attached network -> inherit it."""
    from db.models.reference import EVChargingNetwork, EVLocationLookup

    db = db_session
    net = EVChargingNetwork(network_name="Inherit Net", is_verified=True)
    db.add(net)
    await db.flush()
    loc = EVLocationLookup(
        location_name="Inherit Loc",
        network_id=net.id,
        is_verified=False,
        source_system="ha_fordpass",
    )
    db.add(loc)
    await db.flush()

    assert await inherit_network_from_location(db, None, loc.id) == net.id


async def test_inherit_network_from_location_returns_none_without_location(db_session):
    """No location_id and no network_id -> stays None."""
    assert await inherit_network_from_location(db_session, None, None) is None


# ---------------------------------------------------------------------------
# resolve_location: auto-learn network onto unverified matched location
# ---------------------------------------------------------------------------


async def test_resolve_location_auto_learns_network_on_unverified_match(db_session):
    """An auto-created (unverified) location with no network learns the
    network attached to an incoming charging payload that matches by GPS.
    This is the buildout-over-time path: the location's network_id is NULL
    today and becomes set after a payload with a known network resolves to it.
    """
    from sqlalchemy import select

    from db.models.reference import EVChargingNetwork, EVLocationLookup

    db = db_session
    net = EVChargingNetwork(network_name="Auto-Learn Net", is_verified=True)
    db.add(net)
    await db.flush()

    # Auto-created (unverified, non-manual) location with NULL network
    loc = EVLocationLookup(
        location_name="Auto-Learn Loc",
        latitude=45.5000,
        longitude=-122.6500,
        network_id=None,
        is_verified=False,
        source_system="ha_fordpass",
    )
    db.add(loc)
    await db.flush()

    resolved = await resolve_location(
        db,
        latitude=45.5003,
        longitude=-122.6503,
        network_id=net.id,
    )
    assert resolved == loc.id

    refreshed = (
        await db.execute(select(EVLocationLookup).where(EVLocationLookup.id == loc.id))
    ).scalar_one()
    assert refreshed.network_id == net.id


async def test_resolve_location_auto_learn_does_not_overwrite_existing(db_session):
    """A matched location that already has a network keeps its existing
    network_id even when the incoming payload reports a different network."""
    from sqlalchemy import select

    from db.models.reference import EVChargingNetwork, EVLocationLookup

    db = db_session
    net_existing = EVChargingNetwork(network_name="Existing Net", is_verified=True)
    net_incoming = EVChargingNetwork(network_name="Incoming Net", is_verified=True)
    db.add_all([net_existing, net_incoming])
    await db.flush()

    loc = EVLocationLookup(
        location_name="Conflict Loc",
        latitude=45.5000,
        longitude=-122.6500,
        network_id=net_existing.id,
        is_verified=False,
        source_system="ha_fordpass",
    )
    db.add(loc)
    await db.flush()

    resolved = await resolve_location(
        db,
        latitude=45.5003,
        longitude=-122.6503,
        network_id=net_incoming.id,
    )
    assert resolved == loc.id

    refreshed = (
        await db.execute(select(EVLocationLookup).where(EVLocationLookup.id == loc.id))
    ).scalar_one()
    assert refreshed.network_id == net_existing.id


async def test_resolve_location_auto_learn_skips_manual_location(db_session):
    """Manual locations are protected by the early return — auto-learn must
    not mutate them even when network_id is NULL."""
    from sqlalchemy import select

    from db.models.reference import EVChargingNetwork, EVLocationLookup

    db = db_session
    net = EVChargingNetwork(network_name="Protect Net", is_verified=True)
    db.add(net)
    await db.flush()

    loc = EVLocationLookup(
        location_name="Manual Loc",
        latitude=45.5000,
        longitude=-122.6500,
        network_id=None,
        is_verified=True,
        source_system="manual",  # user-touched: protected
    )
    db.add(loc)
    await db.flush()

    resolved = await resolve_location(
        db,
        latitude=45.5003,
        longitude=-122.6503,
        network_id=net.id,
    )
    assert resolved == loc.id

    refreshed = (
        await db.execute(select(EVLocationLookup).where(EVLocationLookup.id == loc.id))
    ).scalar_one()
    assert refreshed.network_id is None
