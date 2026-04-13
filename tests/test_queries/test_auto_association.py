"""Auto-association tests: location memory (GPS + verified match) + network memory.

Phase 23 introduced:
- `resolve_location`: prefers GPS alias hits, then verified-location proximity
  matches over creating new unverified rows.
- `resolve_network`: checks `EVNetworkNameAlias` so previously-merged names
  map back to the canonical network.

These tests verify that a new ingestion at a known coordinate/alias reuses
the existing row and does NOT create a duplicate unverified location/network.
"""

import pytest
from sqlalchemy import func, select

from db.models.reference import (
    EVChargingNetwork,
    EVLocationGPSAlias,
    EVLocationLookup,
    EVNetworkNameAlias,
)
from web.queries.locations import resolve_location
from web.queries.settings import resolve_network

pytestmark = [pytest.mark.query, pytest.mark.db]


# ---------------------------------------------------------------------------
# Location memory
# ---------------------------------------------------------------------------


async def test_location_verified_proximity_match_reused(db_session):
    """A verified location within 100m + similar name is reused (no new row)."""
    existing = EVLocationLookup(
        location_name="Ikea Charger",
        latitude=40.0000,
        longitude=-74.0000,
        location_type="public",
        is_verified=True,
        source_system="manual",
    )
    db_session.add(existing)
    await db_session.flush()

    # New GPS point ~20m away with the same-ish name
    resolved_id = await resolve_location(
        db_session,
        latitude=40.00015,
        longitude=-74.00005,
        location_name="Ikea Charger",
        source_system="home_assistant",
    )

    assert resolved_id == existing.id

    # Ensure no new location was created
    count = (await db_session.execute(
        select(func.count()).select_from(EVLocationLookup)
    )).scalar_one()
    assert count == 1


async def test_location_gps_alias_takes_precedence(db_session):
    """A GPS alias within 100m resolves to the alias target location.

    Simulates the "previously merged locations" case: the user merged
    another location into this canonical one; the merged coords were saved
    as an EVLocationGPSAlias. Future ingestion at those coords should
    resolve to the canonical location via the alias.
    """
    canonical = EVLocationLookup(
        location_name="Canonical Home",
        latitude=40.10000,
        longitude=-74.10000,
        location_type="home",
        is_verified=True,
        source_system="manual",
    )
    db_session.add(canonical)
    await db_session.flush()

    # Alias GPS — a different point (in the "real world" this was a merged-in loc)
    alias = EVLocationGPSAlias(
        location_id=canonical.id,
        latitude=40.20000,
        longitude=-74.20000,
        source="merge",
    )
    db_session.add(alias)
    await db_session.flush()

    # Ingest new session at the ALIAS coordinates — should hit the alias.
    resolved_id = await resolve_location(
        db_session,
        latitude=40.20003,
        longitude=-74.19998,
        location_name="Some Other Label",
        source_system="home_assistant",
    )
    assert resolved_id == canonical.id


async def test_location_new_coords_create_unverified_location(db_session):
    """Sanity: totally new GPS coords create a new unverified location."""
    resolved_id = await resolve_location(
        db_session,
        latitude=41.5,
        longitude=-75.5,
        location_name="Brand New Station",
        source_system="home_assistant",
    )
    assert resolved_id is not None

    row = (await db_session.execute(
        select(EVLocationLookup).where(EVLocationLookup.id == resolved_id)
    )).scalar_one()
    assert row.is_verified is False
    assert row.source_system == "home_assistant"


# ---------------------------------------------------------------------------
# Network memory
# ---------------------------------------------------------------------------


async def test_network_name_match_is_case_insensitive(db_session):
    """resolve_network case-insensitively matches existing networks.

    The migration seeds the predefined list (incl. "Electrify America"),
    so we reuse an existing canonical row rather than inserting a fresh one.
    """
    existing = (await db_session.execute(
        select(EVChargingNetwork).where(
            func.lower(EVChargingNetwork.network_name) == "electrify america"
        )
    )).scalar_one()

    count_before = (await db_session.execute(
        select(func.count()).select_from(EVChargingNetwork)
    )).scalar_one()

    resolved_id = await resolve_network(db_session, network_name="ELECTRIFY AMERICA")
    assert resolved_id == existing.id

    count_after = (await db_session.execute(
        select(func.count()).select_from(EVChargingNetwork)
    )).scalar_one()
    assert count_after == count_before, "Case-insensitive match should not create a new row"


async def test_network_alias_resolves_to_canonical(db_session):
    """Prior merge created an alias — future ingestion uses it, no new network."""
    canonical = (await db_session.execute(
        select(EVChargingNetwork).where(
            func.lower(EVChargingNetwork.network_name) == "electrify america"
        )
    )).scalar_one()

    alias = EVNetworkNameAlias(
        network_id=canonical.id,
        alias_name="ea-custom-alias-xyz",  # unique, not a predefined network name
    )
    db_session.add(alias)
    await db_session.flush()

    count_before = (await db_session.execute(
        select(func.count()).select_from(EVChargingNetwork)
    )).scalar_one()

    resolved_id = await resolve_network(db_session, network_name="EA-Custom-Alias-XYZ")
    assert resolved_id == canonical.id

    count_after = (await db_session.execute(
        select(func.count()).select_from(EVChargingNetwork)
    )).scalar_one()
    assert count_after == count_before, "Alias match should not create a new network"


async def test_network_unknown_name_autocreates(db_session):
    """Unknown name creates an auto-unverified network row."""
    resolved_id = await resolve_network(
        db_session, network_name="TotallyNewProvider", source_system="home_assistant"
    )
    row = (await db_session.execute(
        select(EVChargingNetwork).where(EVChargingNetwork.id == resolved_id)
    )).scalar_one()
    assert row.network_name == "TotallyNewProvider"
    assert row.is_verified is False
    assert row.source_system == "home_assistant"
