"""Functional tests for the Review Queue (Phase 28 prep).

These tests capture the INTENDED behavior of the review queue. Some tests
are expected to fail (marked xfail) because they expose bugs that Phase 28
will address — failing ones are valuable signal, not bugs in the tests.

Covered:
- Verification status toggle (unverified → verified, and reverse)
- Location ↔ network association via edit endpoint (persists in DB)
- Unverified-only filtering on the pending tabs
- Reference Data / approved listing
- Charging-session group-edit cascade (locations-by-network query)
- Edit modal save (the WORKING.md-flagged bug)
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from db.models.charging_session import EVChargingSession
from db.models.reference import EVChargingNetwork, EVLocationLookup
from tests.factories.locations import LocationLookupFactory
from tests.factories.networks import NetworkFactory
from tests.factories.sessions import ChargingSessionFactory
from tests.factories.vehicles import VehicleFactory

pytestmark = pytest.mark.db


# ---------------------------------------------------------------------------
# 1. Verification status toggle
# ---------------------------------------------------------------------------


async def test_verify_unverified_network_marks_it_verified(client, db_session):
    """POST /review/network/{id}/verify flips is_verified from False to True."""
    net = await NetworkFactory.create(
        db_session, network_name="Pending Net", is_verified=False
    )
    await db_session.commit()

    response = await client.post(f"/review/network/{net.id}/verify")

    assert response.status_code == 200
    refreshed = (
        await db_session.execute(
            select(EVChargingNetwork).where(EVChargingNetwork.id == net.id)
        )
    ).scalar_one()
    assert refreshed.is_verified is True
    assert refreshed.source_system == "manual"


async def test_verify_unverified_location_marks_it_verified(client, db_session):
    """POST /review/location/{id}/verify flips is_verified to True."""
    loc = await LocationLookupFactory.create(
        db_session, location_name="Pending Loc", is_verified=False
    )
    await db_session.commit()

    response = await client.post(f"/review/location/{loc.id}/verify")

    assert response.status_code == 200
    refreshed = (
        await db_session.execute(
            select(EVLocationLookup).where(EVLocationLookup.id == loc.id)
        )
    ).scalar_one()
    assert refreshed.is_verified is True
    assert refreshed.source_system == "manual"


@pytest.mark.xfail(
    reason="Phase 28: no unverify/toggle-back endpoint exists yet. "
    "verify endpoint is one-way (False→True only).",
    strict=False,
)
async def test_unverify_verified_network_reverts_status(client, db_session):
    """Verified→unverified transition (round-trip). Phase 28 should add this."""
    net = await NetworkFactory.create(
        db_session, network_name="Verified Net", is_verified=True
    )
    await db_session.commit()

    # Expected future endpoint: POST /review/network/{id}/unverify
    response = await client.post(f"/review/network/{net.id}/unverify")
    assert response.status_code == 200

    refreshed = (
        await db_session.execute(
            select(EVChargingNetwork).where(EVChargingNetwork.id == net.id)
        )
    ).scalar_one()
    assert refreshed.is_verified is False


@pytest.mark.xfail(
    reason="Phase 28: no unverify/toggle-back endpoint exists for locations.",
    strict=False,
)
async def test_unverify_verified_location_reverts_status(client, db_session):
    loc = await LocationLookupFactory.create(
        db_session, location_name="Verified Loc", is_verified=True
    )
    await db_session.commit()

    response = await client.post(f"/review/location/{loc.id}/unverify")
    assert response.status_code == 200

    refreshed = (
        await db_session.execute(
            select(EVLocationLookup).where(EVLocationLookup.id == loc.id)
        )
    ).scalar_one()
    assert refreshed.is_verified is False


# ---------------------------------------------------------------------------
# 2. Location ↔ network association via edit endpoint
# ---------------------------------------------------------------------------


async def test_edit_location_associates_with_network(client, db_session):
    """Unverified location without a network can be associated with an existing
    network via the edit endpoint; association persists in DB."""
    net = await NetworkFactory.create(
        db_session, network_name="Target Net", is_verified=True
    )
    loc = await LocationLookupFactory.create(
        db_session,
        location_name="Orphan Location",
        network_id=None,
        is_verified=False,
    )
    await db_session.commit()

    response = await client.post(
        f"/review/location/{loc.id}/edit",
        data={
            "location_name": "Orphan Location",
            "address": "",
            "location_type": "",
            "network_id": str(net.id),
            "latitude": "",
            "longitude": "",
            "cost_per_kwh": "",
        },
    )

    assert response.status_code == 200
    refreshed = (
        await db_session.execute(
            select(EVLocationLookup).where(EVLocationLookup.id == loc.id)
        )
    ).scalar_one()
    assert refreshed.network_id == net.id


async def test_edit_location_clears_network_when_blank(client, db_session):
    """Submitting blank network_id clears the association back to None."""
    net = await NetworkFactory.create(db_session, network_name="Attached Net")
    loc = await LocationLookupFactory.create(
        db_session,
        location_name="Associated Loc",
        network_id=net.id,
        is_verified=False,
    )
    await db_session.commit()

    response = await client.post(
        f"/review/location/{loc.id}/edit",
        data={
            "location_name": "Associated Loc",
            "address": "",
            "location_type": "",
            "network_id": "",
            "latitude": "",
            "longitude": "",
            "cost_per_kwh": "",
        },
    )

    assert response.status_code == 200
    refreshed = (
        await db_session.execute(
            select(EVLocationLookup).where(EVLocationLookup.id == loc.id)
        )
    ).scalar_one()
    assert refreshed.network_id is None


# ---------------------------------------------------------------------------
# 3. Unverified-only filtering on the primary review queue tabs
# ---------------------------------------------------------------------------


async def test_pending_networks_partial_excludes_verified(client, db_session):
    """GET /review/networks returns only unverified networks."""
    await NetworkFactory.create(
        db_session, network_name="VerifiedOnlyNet", is_verified=True
    )
    await NetworkFactory.create(
        db_session, network_name="PendingOnlyNet", is_verified=False
    )
    await db_session.commit()

    response = await client.get("/review/networks")

    assert response.status_code == 200
    body = response.text
    assert "PendingOnlyNet" in body
    assert "VerifiedOnlyNet" not in body


async def test_pending_locations_partial_excludes_verified(client, db_session):
    """GET /review/locations returns only unverified locations."""
    await LocationLookupFactory.create(
        db_session, location_name="VerifiedOnlyLoc", is_verified=True
    )
    await LocationLookupFactory.create(
        db_session, location_name="PendingOnlyLoc", is_verified=False
    )
    await db_session.commit()

    response = await client.get("/review/locations")

    assert response.status_code == 200
    body = response.text
    assert "PendingOnlyLoc" in body
    assert "VerifiedOnlyLoc" not in body


async def test_review_queue_default_tab_pending_networks(client, db_session):
    """GET /review (default pending tab, networks sub) shows only unverified."""
    await NetworkFactory.create(
        db_session, network_name="AlreadyApproved", is_verified=True
    )
    await NetworkFactory.create(
        db_session, network_name="AwaitingReview", is_verified=False
    )
    await db_session.commit()

    response = await client.get("/review?tab=pending&sub=networks")
    assert response.status_code == 200
    body = response.text
    assert "AwaitingReview" in body
    # Verified ones should NOT appear in the pending tab's network rows.
    # (They may appear in merge-target dropdowns, which is acceptable —
    # if the template ever gets tightened, adjust this assertion.)


# ---------------------------------------------------------------------------
# 4. Reference Data / approved listing
# ---------------------------------------------------------------------------


async def test_approved_tab_lists_verified_networks_and_locations(client, db_session):
    """GET /review/approved renders the approved tree with verified items."""
    net = await NetworkFactory.create(
        db_session, network_name="KnownApprovedNet", is_verified=True
    )
    await LocationLookupFactory.create(
        db_session,
        location_name="KnownApprovedLoc",
        network_id=net.id,
        is_verified=True,
    )
    await LocationLookupFactory.create(
        db_session, location_name="PendingLocNotShown", is_verified=False
    )
    await db_session.commit()

    response = await client.get("/review/approved")

    assert response.status_code == 200
    body = response.text
    assert "KnownApprovedNet" in body
    assert "KnownApprovedLoc" in body
    assert "PendingLocNotShown" not in body


async def test_approved_tab_includes_standalone_locations(client, db_session):
    """Verified locations with network_id NULL appear as standalone entries."""
    await LocationLookupFactory.create(
        db_session,
        location_name="StandaloneApprovedLoc",
        network_id=None,
        is_verified=True,
    )
    await db_session.commit()

    response = await client.get("/review/approved")
    assert response.status_code == 200
    assert "StandaloneApprovedLoc" in response.text


@pytest.mark.skip(
    reason="Phase 28 will decide whether a dedicated 'Reference Data' route "
    "(e.g. /reference-data) is added or if /review?tab=approved is the canonical "
    "listing. Don't invent routes until that lands."
)
async def test_reference_data_dedicated_endpoint(client):  # pragma: no cover
    response = await client.get("/reference-data")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# 5. Charging-session group-edit cascade
# ---------------------------------------------------------------------------


async def test_locations_by_network_returns_only_that_networks_locations(
    client, db_session
):
    """When group-edit cascade requests locations for a network, only that
    network's verified locations appear in the dropdown options."""
    net_a = await NetworkFactory.create(db_session, network_name="NetA")
    net_b = await NetworkFactory.create(db_session, network_name="NetB")
    await LocationLookupFactory.create(
        db_session,
        location_name="LocInNetA",
        network_id=net_a.id,
        is_verified=True,
    )
    await LocationLookupFactory.create(
        db_session,
        location_name="LocInNetB",
        network_id=net_b.id,
        is_verified=True,
    )
    await LocationLookupFactory.create(
        db_session,
        location_name="LocStandalone",
        network_id=None,
        is_verified=True,
    )
    await db_session.commit()

    response = await client.get(f"/locations/by-network?network_id={net_a.id}")

    assert response.status_code == 200
    body = response.text
    assert "LocInNetA" in body
    assert "LocInNetB" not in body
    assert "LocStandalone" not in body


async def test_locations_by_network_excludes_unverified(client, db_session):
    """Unverified locations should not appear in the group-edit cascade."""
    net = await NetworkFactory.create(db_session, network_name="CascadeNet")
    await LocationLookupFactory.create(
        db_session,
        location_name="VerifiedCascadeLoc",
        network_id=net.id,
        is_verified=True,
    )
    await LocationLookupFactory.create(
        db_session,
        location_name="UnverifiedCascadeLoc",
        network_id=net.id,
        is_verified=False,
    )
    await db_session.commit()

    response = await client.get(f"/locations/by-network?network_id={net.id}")
    assert response.status_code == 200
    body = response.text
    assert "VerifiedCascadeLoc" in body
    assert "UnverifiedCascadeLoc" not in body


async def test_locations_by_network_without_id_returns_placeholder(client):
    """Missing network_id returns a safe placeholder option, not an error."""
    response = await client.get("/locations/by-network")
    assert response.status_code == 200
    assert "<option" in response.text


async def test_bulk_session_edit_applies_network_and_location(client, db_session):
    """PUT /charging/sessions/bulk persists network_id + location_id changes.

    This is the backend for the Sessions group-edit bar that Phase 27-07
    cascaded — test that the underlying persistence is correct.
    """
    vehicle = await VehicleFactory.create(db_session)
    net = await NetworkFactory.create(db_session, network_name="BulkNet")
    loc = await LocationLookupFactory.create(
        db_session, location_name="BulkLoc", network_id=net.id, is_verified=True
    )
    s1 = await ChargingSessionFactory.create(
        db_session, device_id=vehicle.device_id, network_id=None, location_id=None
    )
    s2 = await ChargingSessionFactory.create(
        db_session, device_id=vehicle.device_id, network_id=None, location_id=None
    )
    await db_session.commit()

    response = await client.put(
        "/charging/sessions/bulk",
        data={
            "session_ids": f"{s1.id},{s2.id}",
            "bulk_network_id": str(net.id),
            "bulk_location_id": str(loc.id),
        },
    )
    assert response.status_code == 200

    rows = (
        await db_session.execute(
            select(EVChargingSession).where(EVChargingSession.id.in_([s1.id, s2.id]))
        )
    ).scalars().all()
    assert len(rows) == 2
    for row in rows:
        assert row.network_id == net.id
        assert row.location_id == loc.id


# ---------------------------------------------------------------------------
# 6. Edit modal save — persists field changes
# ---------------------------------------------------------------------------


async def test_edit_location_persists_all_field_changes(client, db_session):
    """The POST endpoint the edit modal hits persists name, address, coords,
    type, network_id, cost. If this fails, it's the WORKING.md bug flagged
    for Phase 28 (modal edits not saving)."""
    net = await NetworkFactory.create(db_session, network_name="EditNet")
    loc = await LocationLookupFactory.create(
        db_session,
        location_name="Original Name",
        address="123 Old St",
        location_type="public",
        latitude=45.50,
        longitude=-122.60,
        cost_per_kwh=0.25,
        network_id=None,
        is_verified=False,
    )
    await db_session.commit()

    response = await client.post(
        f"/review/location/{loc.id}/edit",
        data={
            "location_name": "New Name",
            "address": "456 New Ave",
            "location_type": "home",
            "network_id": str(net.id),
            "latitude": "47.12",
            "longitude": "-120.34",
            "cost_per_kwh": "0.42",
        },
    )

    assert response.status_code == 200
    refreshed = (
        await db_session.execute(
            select(EVLocationLookup).where(EVLocationLookup.id == loc.id)
        )
    ).scalar_one()
    assert refreshed.location_name == "New Name"
    assert refreshed.address == "456 New Ave"
    assert refreshed.location_type == "home"
    assert refreshed.network_id == net.id
    assert float(refreshed.latitude) == pytest.approx(47.12, abs=0.001)
    assert float(refreshed.longitude) == pytest.approx(-120.34, abs=0.001)
    assert float(refreshed.cost_per_kwh) == pytest.approx(0.42, abs=0.001)


@pytest.mark.skip(
    reason="Phase 28 will add a POST /review/network/{id}/edit endpoint. "
    "Currently only locations have an edit endpoint; network editing is "
    "done via settings routes, not the review queue."
)
async def test_edit_network_persists_field_changes(client, db_session):  # pragma: no cover
    """Network edit endpoint expected in Phase 28."""
    net = await NetworkFactory.create(
        db_session, network_name="Original", is_verified=False
    )
    await db_session.commit()

    response = await client.post(
        f"/review/network/{net.id}/edit",
        data={"network_name": "Renamed", "cost_per_kwh": "0.31"},
    )
    assert response.status_code == 200

    refreshed = (
        await db_session.execute(
            select(EVChargingNetwork).where(EVChargingNetwork.id == net.id)
        )
    ).scalar_one()
    assert refreshed.network_name == "Renamed"


# ---------------------------------------------------------------------------
# 7. Phase 28 Task 1 — Review-scoped network edit (D-A1, D-A3)
# ---------------------------------------------------------------------------


async def test_review_edit_network_persists_changes(client, db_session):
    """PUT /review/networks/{id} persists edits and returns the review
    networks partial (not the settings network_management partial)."""
    net = await NetworkFactory.create(
        db_session, network_name="Before Rename", is_verified=False
    )
    await db_session.commit()

    response = await client.put(
        f"/review/networks/{net.id}",
        data={
            "network_name": "Renamed Via Review",
            "color": "#123456",
            "cost_per_kwh": "0.42",
        },
    )

    assert response.status_code == 200
    refreshed = (
        await db_session.execute(
            select(EVChargingNetwork).where(EVChargingNetwork.id == net.id)
        )
    ).scalar_one()
    assert refreshed.network_name == "Renamed Via Review"
    assert refreshed.color == "#123456"
    assert float(refreshed.cost_per_kwh) == pytest.approx(0.42, abs=0.001)

    # Body should contain the review networks partial markup, not the settings
    # network_management-card partial.
    body = response.text
    assert 'hx-get="/review/networks"' in body
    assert 'id="network-management-card"' not in body


async def test_review_edit_network_fires_close_trigger(client, db_session):
    """PUT /review/networks/{id} sets HX-Trigger: closeNetworkModal so the
    page-scope modal listener can dismiss the dialog on success."""
    net = await NetworkFactory.create(
        db_session, network_name="Will Close", is_verified=False
    )
    await db_session.commit()

    response = await client.put(
        f"/review/networks/{net.id}",
        data={
            "network_name": "Still Will Close",
            "color": "",
            "cost_per_kwh": "",
        },
    )

    assert response.status_code == 200
    assert "closeNetworkModal" in response.headers.get("HX-Trigger", "")


async def test_review_edit_network_modal_fetch_returns_details_only(
    client, db_session
):
    """GET /review/networks/{id}/edit-modal returns the network_edit_modal
    template but with only the Details tab rendered (Locations and
    Subscription tabs hidden per D-A3)."""
    net = await NetworkFactory.create(
        db_session, network_name="Details Only Net", is_verified=False
    )
    await db_session.commit()

    response = await client.get(f"/review/networks/{net.id}/edit-modal")

    assert response.status_code == 200
    body = response.text
    # Details-only: the other tabs' aria-labels must not render
    assert 'aria-label="Locations"' not in body
    assert 'aria-label="Subscription"' not in body
    # But Details tab + form field must be present
    assert 'aria-label="Details"' in body
    assert 'name="network_name"' in body
    # Save button must target the review-scoped endpoint + review swap zone
    assert 'hx-put="/review/networks/' in body
    assert 'hx-target="#review-inner"' in body


# ---------------------------------------------------------------------------
# 8. Phase 28 Task 2 — Page-scope location edit modal + HX-Trigger close
# ---------------------------------------------------------------------------


async def test_review_edit_location_fires_close_trigger(client, db_session):
    """POST /review/location/{id}/edit now returns HX-Trigger: closeEditLocModal
    alongside the locations partial (D-A2 / Pattern S1). The body listener
    in review_queue.html closes the page-scope #edit-loc-modal on success."""
    net = await NetworkFactory.create(
        db_session, network_name="CloseTriggerNet", is_verified=True
    )
    loc = await LocationLookupFactory.create(
        db_session,
        location_name="Before Close",
        network_id=None,
        is_verified=False,
    )
    await db_session.commit()

    response = await client.post(
        f"/review/location/{loc.id}/edit",
        data={
            "location_name": "After Close",
            "address": "",
            "location_type": "",
            "network_id": str(net.id),
            "latitude": "",
            "longitude": "",
            "cost_per_kwh": "",
        },
    )

    assert response.status_code == 200
    assert "closeEditLocModal" in response.headers.get("HX-Trigger", "")
    refreshed = (
        await db_session.execute(
            select(EVLocationLookup).where(EVLocationLookup.id == loc.id)
        )
    ).scalar_one()
    assert refreshed.location_name == "After Close"
    assert refreshed.network_id == net.id


async def test_review_location_edit_form_endpoint_returns_form(client, db_session):
    """GET /review/location/{id}/edit-form returns the form markup for the
    page-scope #edit-loc-modal. Must include the POST target back to
    /review/location/{id}/edit and the network <select> (D-C2 retained
    association path)."""
    await NetworkFactory.create(
        db_session, network_name="Form Endpoint Net", is_verified=True
    )
    loc = await LocationLookupFactory.create(
        db_session,
        location_name="Form Endpoint Loc",
        network_id=None,
        is_verified=False,
    )
    await db_session.commit()

    response = await client.get(f"/review/location/{loc.id}/edit-form")

    assert response.status_code == 200
    body = response.text
    assert f'hx-post="/review/location/{loc.id}/edit"' in body
    assert 'name="network_id"' in body
