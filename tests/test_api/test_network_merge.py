"""Tests for network and location merge endpoints ( backfill).
These cover the POST /review/network/{id}/merge and /review/location/{id}/merge
endpoints that:
Reassign all FK references (sessions, subscriptions, locations) to target
Create an EVNetworkNameAlias / EVLocationGPSAlias preserving history
Delete the source row
"""

import pytest
from sqlalchemy import func, select

from db.models.charging_session import EVChargingSession
from db.models.reference import (
    EVChargingNetwork,
    EVLocationGPSAlias,
    EVLocationLookup,
    EVNetworkNameAlias,
    EVNetworkSubscription,
)
from tests.factories.locations import LocationLookupFactory
from tests.factories.networks import NetworkFactory, SubscriptionFactory
from tests.factories.sessions import ChargingSessionFactory
from tests.factories.vehicles import VehicleFactory

pytestmark = pytest.mark.db


# ---------------------------------------------------------------------------
# Network merge preview
# ---------------------------------------------------------------------------


async def test_network_merge_preview_shows_counts(client, db_session):
    """GET /review/network/{id}/merge-preview returns modal with affected counts."""
    vehicle = await VehicleFactory.create(db_session)
    src = await NetworkFactory.create(db_session, network_name="Src Net")
    await NetworkFactory.create(db_session, network_name="Tgt Net")

    await ChargingSessionFactory.create(
        db_session, device_id=vehicle.device_id, network_id=src.id
    )
    await ChargingSessionFactory.create(
        db_session, device_id=vehicle.device_id, network_id=src.id
    )
    await SubscriptionFactory.create(db_session, network_id=src.id)
    await LocationLookupFactory.create(db_session, network_id=src.id)

    response = await client.get(f"/review/network/{src.id}/merge-preview")
    assert response.status_code == 200
    body = response.text
    # Numeric counts rendered somewhere in the modal template
    assert "2" in body  # 2 sessions
    assert "Src Net" in body


async def test_network_merge_preview_404_when_missing(client, db_session):
    response = await client.get("/review/network/99999/merge-preview")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Network merge execution
# ---------------------------------------------------------------------------


async def test_network_merge_reassigns_all_references(client, db_session):
    """Merging src→tgt moves sessions, subscriptions, and locations; deletes src."""
    vehicle = await VehicleFactory.create(db_session)
    src = await NetworkFactory.create(db_session, network_name="Old Network")
    tgt = await NetworkFactory.create(db_session, network_name="New Network")

    s1 = await ChargingSessionFactory.create(
        db_session, device_id=vehicle.device_id, network_id=src.id
    )
    s2 = await ChargingSessionFactory.create(
        db_session, device_id=vehicle.device_id, network_id=src.id
    )
    sub = await SubscriptionFactory.create(db_session, network_id=src.id)
    loc = await LocationLookupFactory.create(
        db_session, network_id=src.id, location_name="Attached"
    )

    response = await client.post(
        f"/review/network/{src.id}/merge",
        data={"target_id": str(tgt.id)},
    )
    assert response.status_code == 200

    # Sessions reassigned
    session_net_ids = (await db_session.execute(
        select(EVChargingSession.network_id).where(
            EVChargingSession.id.in_([s1.id, s2.id])
        )
    )).scalars().all()
    assert set(session_net_ids) == {tgt.id}

    # Subscription reassigned
    refreshed_sub = (await db_session.execute(
        select(EVNetworkSubscription).where(EVNetworkSubscription.id == sub.id)
    )).scalar_one()
    assert refreshed_sub.network_id == tgt.id

    # Location reassigned
    refreshed_loc = (await db_session.execute(
        select(EVLocationLookup).where(EVLocationLookup.id == loc.id)
    )).scalar_one()
    assert refreshed_loc.network_id == tgt.id

    # Source deleted
    src_row = (await db_session.execute(
        select(EVChargingNetwork).where(EVChargingNetwork.id == src.id)
    )).scalar_one_or_none()
    assert src_row is None


async def test_network_merge_creates_name_alias(client, db_session):
    """After merge, source network_name becomes an alias pointing at target."""
    src = await NetworkFactory.create(db_session, network_name="UniqueOldName")
    tgt = await NetworkFactory.create(db_session, network_name="CanonicalName")

    response = await client.post(
        f"/review/network/{src.id}/merge",
        data={"target_id": str(tgt.id)},
    )
    assert response.status_code == 200

    alias = (await db_session.execute(
        select(EVNetworkNameAlias).where(
            func.lower(EVNetworkNameAlias.alias_name) == "uniqueoldname"
        )
    )).scalar_one_or_none()
    assert alias is not None
    assert alias.network_id == tgt.id


async def test_network_merge_alias_enables_future_resolution(client, db_session):
    """Once merged, subsequent resolve_network on the old name returns target."""
    from web.queries.settings import resolve_network

    src = await NetworkFactory.create(db_session, network_name="LegacyName")
    tgt = await NetworkFactory.create(db_session, network_name="ModernName")

    await client.post(
        f"/review/network/{src.id}/merge",
        data={"target_id": str(tgt.id)},
    )

    resolved = await resolve_network(db_session, network_name="LegacyName")
    assert resolved == tgt.id


async def test_network_merge_into_self_rejected(client, db_session):
    src = await NetworkFactory.create(db_session)
    response = await client.post(
        f"/review/network/{src.id}/merge",
        data={"target_id": str(src.id)},
    )
    assert response.status_code == 400


async def test_network_merge_missing_target_404(client, db_session):
    src = await NetworkFactory.create(db_session)
    response = await client.post(
        f"/review/network/{src.id}/merge",
        data={"target_id": "99999"},
    )
    assert response.status_code == 404


async def test_network_merge_preserves_target_verification(client, db_session):
    """Merging unverified src into verified tgt: tgt remains verified.

    Documents current behavior: merge is one-directional (src→tgt), tgt's
    row is unmodified. Neither direction is rejected based on verified
    status — aliases + FK reassignment happen regardless.
    """
    src = await NetworkFactory.create(
        db_session, network_name="autodetected", is_verified=False
    )
    tgt = await NetworkFactory.create(
        db_session, network_name="Curated", is_verified=True
    )

    response = await client.post(
        f"/review/network/{src.id}/merge",
        data={"target_id": str(tgt.id)},
    )
    assert response.status_code == 200

    refreshed = (await db_session.execute(
        select(EVChargingNetwork).where(EVChargingNetwork.id == tgt.id)
    )).scalar_one()
    assert refreshed.is_verified is True


async def test_network_merge_verified_into_unverified_allowed(client, db_session):
    """Reverse direction (verified src → unverified tgt) is NOT rejected;
    target keeps its unverified flag. Documents current permissive behavior."""
    src = await NetworkFactory.create(
        db_session, network_name="VerifiedOld", is_verified=True
    )
    tgt = await NetworkFactory.create(
        db_session, network_name="UnverifiedNew", is_verified=False
    )

    response = await client.post(
        f"/review/network/{src.id}/merge",
        data={"target_id": str(tgt.id)},
    )
    assert response.status_code == 200

    refreshed = (await db_session.execute(
        select(EVChargingNetwork).where(EVChargingNetwork.id == tgt.id)
    )).scalar_one()
    assert refreshed.is_verified is False


# ---------------------------------------------------------------------------
# Location merge
# ---------------------------------------------------------------------------


async def test_location_merge_reassigns_sessions_and_creates_gps_alias(
    client, db_session
):
    """Merge src location → tgt: sessions repoint, GPS alias created, src deleted."""
    vehicle = await VehicleFactory.create(db_session)
    src = await LocationLookupFactory.create(
        db_session,
        location_name="Old Spot",
        latitude=40.0,
        longitude=-74.0,
    )
    tgt = await LocationLookupFactory.create(
        db_session,
        location_name="Canonical Spot",
        latitude=41.0,  # far enough that src coords don't fall within 100m of tgt
        longitude=-75.0,
    )

    s = await ChargingSessionFactory.create(
        db_session, device_id=vehicle.device_id, location_id=src.id
    )

    response = await client.post(
        f"/review/location/{src.id}/merge",
        data={"target_id": str(tgt.id)},
    )
    assert response.status_code == 200

    # Session repointed
    refreshed_s = (await db_session.execute(
        select(EVChargingSession).where(EVChargingSession.id == s.id)
    )).scalar_one()
    assert refreshed_s.location_id == tgt.id

    # Source gone
    src_row = (await db_session.execute(
        select(EVLocationLookup).where(EVLocationLookup.id == src.id)
    )).scalar_one_or_none()
    assert src_row is None

    # GPS alias created pointing to tgt with src coords
    alias = (await db_session.execute(
        select(EVLocationGPSAlias).where(EVLocationGPSAlias.location_id == tgt.id)
    )).scalar_one_or_none()
    assert alias is not None
    assert float(alias.latitude) == pytest.approx(40.0, abs=1e-6)
    assert float(alias.longitude) == pytest.approx(-74.0, abs=1e-6)
    assert alias.source == "merge"


async def test_location_merge_skips_alias_when_already_covered(client, db_session):
    """If tgt already has a GPS alias within 100m of src coords,
    a new alias is NOT added (de-dup behavior)."""
    src = await LocationLookupFactory.create(
        db_session, latitude=40.0, longitude=-74.0
    )
    tgt = await LocationLookupFactory.create(
        db_session, latitude=45.0, longitude=-70.0
    )
    # Existing alias near src coords (~11m)
    db_session.add(EVLocationGPSAlias(
        location_id=tgt.id,
        latitude=40.0001,
        longitude=-74.0,
        source="manual",
    ))
    await db_session.flush()

    before_count = (await db_session.execute(
        select(func.count()).select_from(EVLocationGPSAlias)
        .where(EVLocationGPSAlias.location_id == tgt.id)
    )).scalar()

    response = await client.post(
        f"/review/location/{src.id}/merge",
        data={"target_id": str(tgt.id)},
    )
    assert response.status_code == 200

    after_count = (await db_session.execute(
        select(func.count()).select_from(EVLocationGPSAlias)
        .where(EVLocationGPSAlias.location_id == tgt.id)
    )).scalar()
    # No new alias because existing one covered the src coords
    assert after_count == before_count


async def test_location_merge_into_self_rejected(client, db_session):
    src = await LocationLookupFactory.create(db_session)
    response = await client.post(
        f"/review/location/{src.id}/merge",
        data={"target_id": str(src.id)},
    )
    assert response.status_code == 400


async def test_location_merge_missing_source_404(client, db_session):
    tgt = await LocationLookupFactory.create(db_session)
    response = await client.post(
        "/review/location/99999/merge",
        data={"target_id": str(tgt.id)},
    )
    assert response.status_code == 404


async def test_location_merge_without_gps_skips_alias(client, db_session):
    """Src with no lat/lon → merge succeeds but no GPS alias is created."""
    src = await LocationLookupFactory.create(
        db_session, latitude=None, longitude=None
    )
    tgt = await LocationLookupFactory.create(db_session)

    response = await client.post(
        f"/review/location/{src.id}/merge",
        data={"target_id": str(tgt.id)},
    )
    assert response.status_code == 200

    aliases = (await db_session.execute(
        select(EVLocationGPSAlias).where(EVLocationGPSAlias.location_id == tgt.id)
    )).scalars().all()
    assert len(aliases) == 0
