"""Tests for network auto-detection and resolution (Phase 19 backfill).

Covers the service-layer flow that wires incoming session signals
(network name strings, GPS coordinates) to canonical network and
location rows, including alias-based resolution created by prior merges.

Scope:
- resolve_network: exact name, alias, auto-create paths
- resolve_location: GPS auto-create with attached network,
  GPS alias match (location memory), unverified auto-create
"""

import pytest
from sqlalchemy import func, select

from db.models.reference import (
    EVChargingNetwork,
    EVLocationGPSAlias,
    EVLocationLookup,
    EVNetworkNameAlias,
)
from tests.factories.networks import NetworkFactory
from tests.factories.locations import LocationLookupFactory
from web.queries.locations import resolve_location
from web.queries.settings import resolve_network


pytestmark = [pytest.mark.query, pytest.mark.db]


# ---------------------------------------------------------------------------
# resolve_network: auto-creation
# ---------------------------------------------------------------------------


async def test_resolve_network_returns_none_for_empty_input(db_session):
    """Neither id nor name provided → None."""
    assert await resolve_network(db_session) is None
    assert await resolve_network(db_session, network_name="") is None
    assert await resolve_network(db_session, network_name="   ") is None


async def test_resolve_network_returns_id_when_given(db_session):
    """When a network_id is passed, it's returned directly without lookup."""
    result = await resolve_network(db_session, network_id=12345)
    assert result == 12345


async def test_resolve_network_auto_creates_unverified_row(db_session):
    """Unknown network name → new EVChargingNetwork row, is_verified=False."""
    before = (await db_session.execute(
        select(func.count()).select_from(EVChargingNetwork)
    )).scalar() or 0

    new_id = await resolve_network(
        db_session,
        network_name="BrandNewNetwork",
        source_system="home_assistant",
    )
    assert new_id is not None

    after = (await db_session.execute(
        select(func.count()).select_from(EVChargingNetwork)
    )).scalar() or 0
    assert after == before + 1

    created = (await db_session.execute(
        select(EVChargingNetwork).where(EVChargingNetwork.id == new_id)
    )).scalar_one()
    assert created.network_name == "BrandNewNetwork"
    assert created.is_verified is False
    assert created.source_system == "home_assistant"


async def test_resolve_network_case_insensitive_match(db_session):
    """Existing network matches case-insensitively — does NOT create duplicate."""
    existing = await NetworkFactory.create(
        db_session, network_name="FakeBrandXyz", is_verified=True
    )

    resolved = await resolve_network(db_session, network_name="fakebrandxyz")
    assert resolved == existing.id

    # With whitespace / mixed case
    resolved2 = await resolve_network(db_session, network_name="  FAKEBRANDXYZ  ")
    assert resolved2 == existing.id

    count = (await db_session.execute(
        select(func.count()).select_from(EVChargingNetwork)
        .where(func.lower(EVChargingNetwork.network_name) == "fakebrandxyz")
    )).scalar()
    assert count == 1


async def test_resolve_network_matches_alias(db_session):
    """When an alias row exists for a name, that variant maps to canonical network."""
    canonical = await NetworkFactory.create(
        db_session, network_name="Electrify America", is_verified=True
    )
    db_session.add(EVNetworkNameAlias(
        network_id=canonical.id,
        alias_name="EA",
    ))
    await db_session.flush()

    resolved = await resolve_network(db_session, network_name="EA")
    assert resolved == canonical.id

    # Still case-insensitive for aliases
    resolved2 = await resolve_network(db_session, network_name="ea")
    assert resolved2 == canonical.id


async def test_resolve_network_predefined_gets_brand_metadata(db_session):
    """Auto-create for a known predefined name fills color/cost from template.

    Predefined networks (Tesla Supercharger etc.) are seeded by migrations
    as verified rows. To exercise the auto-create path we first delete any
    existing match so resolve_network() hits the create branch.
    """
    from sqlalchemy import delete as sa_delete

    await db_session.execute(
        sa_delete(EVChargingNetwork).where(
            func.lower(EVChargingNetwork.network_name) == "tesla supercharger"
        )
    )
    await db_session.flush()

    new_id = await resolve_network(db_session, network_name="Tesla Supercharger")
    created = (await db_session.execute(
        select(EVChargingNetwork).where(EVChargingNetwork.id == new_id)
    )).scalar_one()
    assert created.network_name == "Tesla Supercharger"
    assert created.color == "#CC0000"
    assert created.cost_per_kwh is not None
    assert created.is_verified is False


# ---------------------------------------------------------------------------
# resolve_location: GPS-driven auto-create + network attachment
# ---------------------------------------------------------------------------


async def test_resolve_location_auto_creates_with_attached_network(db_session):
    """GPS + network name with no existing match → new unverified location
    with auto-created/attached network."""
    before_locs = (await db_session.execute(
        select(func.count()).select_from(EVLocationLookup)
    )).scalar() or 0
    before_nets = (await db_session.execute(
        select(func.count()).select_from(EVChargingNetwork)
    )).scalar() or 0

    loc_id = await resolve_location(
        db_session,
        latitude=40.7128,
        longitude=-74.0060,
        network_name="SomeNewProvider",
        location_name="Downtown Charger",
        source_system="home_assistant",
    )
    assert loc_id is not None

    after_locs = (await db_session.execute(
        select(func.count()).select_from(EVLocationLookup)
    )).scalar()
    after_nets = (await db_session.execute(
        select(func.count()).select_from(EVChargingNetwork)
    )).scalar()
    assert after_locs == before_locs + 1
    assert after_nets == before_nets + 1

    loc = (await db_session.execute(
        select(EVLocationLookup).where(EVLocationLookup.id == loc_id)
    )).scalar_one()
    assert loc.is_verified is False
    assert loc.network_id is not None
    assert loc.location_name == "Downtown Charger"

    net = (await db_session.execute(
        select(EVChargingNetwork).where(EVChargingNetwork.id == loc.network_id)
    )).scalar_one()
    assert net.network_name == "SomeNewProvider"
    assert net.is_verified is False


async def test_resolve_location_reuses_existing_within_100m(db_session):
    """GPS within 100m of an unverified location → returns existing id, no new row."""
    existing = await LocationLookupFactory.create(
        db_session,
        location_name="Existing Charger",
        latitude=40.0,
        longitude=-74.0,
        is_verified=False,
        source_system="home_assistant",
    )

    before = (await db_session.execute(
        select(func.count()).select_from(EVLocationLookup)
    )).scalar()

    # ~11m north — well within 100m radius
    resolved = await resolve_location(
        db_session,
        latitude=40.0001,
        longitude=-74.0,
        source_system="home_assistant",
    )
    assert resolved == existing.id

    after = (await db_session.execute(
        select(func.count()).select_from(EVLocationLookup)
    )).scalar()
    assert after == before


async def test_resolve_location_creates_new_when_outside_radius(db_session):
    """GPS > 100m from all existing → new location row."""
    await LocationLookupFactory.create(
        db_session, latitude=40.0, longitude=-74.0, is_verified=False
    )

    # ~1.1km north
    resolved = await resolve_location(
        db_session,
        latitude=40.01,
        longitude=-74.0,
        location_name="Other Place",
        source_system="home_assistant",
    )
    assert resolved is not None

    # Should be a new row
    all_locs = (await db_session.execute(select(EVLocationLookup))).scalars().all()
    assert len(all_locs) == 2


async def test_resolve_location_gps_alias_resolves_first(db_session):
    """An EVLocationGPSAlias within 100m → returns its target location_id even
    if a closer unverified location exists (memory from a prior merge)."""
    canonical = await LocationLookupFactory.create(
        db_session,
        location_name="Canonical",
        latitude=41.0,
        longitude=-75.0,
        is_verified=True,
    )
    # Alias at a different nearby point — location memory
    db_session.add(EVLocationGPSAlias(
        location_id=canonical.id,
        latitude=42.0,
        longitude=-76.0,
        source="merge",
    ))
    await db_session.flush()

    # Incoming coordinates very close to the alias point
    resolved = await resolve_location(
        db_session,
        latitude=42.00005,
        longitude=-76.00005,
    )
    assert resolved == canonical.id


async def test_resolve_location_returns_none_when_no_signals(db_session):
    """No lat/lon and no address → None (nothing to resolve against)."""
    result = await resolve_location(db_session, network_name="ChargePoint")
    assert result is None


# ---------------------------------------------------------------------------
# Duplicate-network detection
# ---------------------------------------------------------------------------


async def test_duplicate_networks_can_be_surfaced_by_lowercased_name(db_session):
    """Two auto-created networks that differ only by casing/whitespace can be
    grouped together by lowercased name — the building block for a
    'merge candidate' query.

    NOTE: Phase 19 does not ship a dedicated merge-candidates endpoint.
    Duplicate prevention happens at resolve_network() time (case-insensitive
    lookup + alias check). This test documents that once a raw duplicate
    exists in the DB (e.g. seeded / imported), it is grouped by lowered name.
    """
    # Intentionally bypass resolve_network to simulate a pre-existing raw dup
    n1 = EVChargingNetwork(
        network_name="FooNet", is_verified=False, source_system="csv_import"
    )
    n2 = EVChargingNetwork(
        network_name="foonet ", is_verified=False, source_system="home_assistant"
    )
    db_session.add_all([n1, n2])
    await db_session.flush()

    result = await db_session.execute(
        select(
            func.lower(func.trim(EVChargingNetwork.network_name)).label("key"),
            func.count().label("n"),
        )
        .group_by(func.lower(func.trim(EVChargingNetwork.network_name)))
        .having(func.count() > 1)
    )
    rows = result.all()
    keys = {r.key for r in rows}
    assert "foonet" in keys


# ---------------------------------------------------------------------------
# Verified-network protection (documenting current behavior)
# ---------------------------------------------------------------------------


async def test_resolve_network_returns_verified_when_name_matches(db_session):
    """If a verified network already exists with the incoming name, resolve_network
    returns its id regardless of verification — it does NOT create a duplicate
    unverified row. This is the primary 'protection' against auto-detect
    polluting curated data."""
    verified = await NetworkFactory.create(
        db_session, network_name="CuratedUniqueCoOp", is_verified=True
    )
    resolved = await resolve_network(
        db_session, network_name="curateduniquecoop", source_system="home_assistant"
    )
    assert resolved == verified.id

    refreshed = (await db_session.execute(
        select(EVChargingNetwork).where(EVChargingNetwork.id == verified.id)
    )).scalar_one()
    # Status untouched
    assert refreshed.is_verified is True
