"""Functional tests for review queue routes and partial responses."""

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


async def test_unverify_verified_network_reverts_status(client, db_session):
    """Verified→unverified transition for networks (round-trip)."""
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


async def test_unverify_verified_location_reverts_status(client, db_session):
    """Verified→unverified transition for locations (round-trip)."""
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


async def test_unverify_network_does_not_change_source_system(client, db_session):
    """Guarantee: unverify is a pure flag flip.
    The handler must NOT mutate ``source_system`` (no 'manual' overwrite, no
    reset to NULL). A network auto-detected from Home Assistant that was
    mis-verified should still remember it came from HA after being unverified.
    """
    net = await NetworkFactory.create(
        db_session,
        network_name="Misverified Net",
        is_verified=True,
        source_system="home_assistant",
    )
    await db_session.commit()

    response = await client.post(f"/review/network/{net.id}/unverify")

    assert response.status_code == 200
    refreshed = (
        await db_session.execute(
            select(EVChargingNetwork).where(EVChargingNetwork.id == net.id)
        )
    ).scalar_one()
    assert refreshed.is_verified is False
    assert refreshed.source_system == "home_assistant", (
        "unverify must not change source_system"
    )


async def test_unverify_location_does_not_change_source_system(client, db_session):
    """Guarantee: location unverify is a pure flag flip; source_system
    is preserved across the transition.
    """
    loc = await LocationLookupFactory.create(
        db_session,
        location_name="Misverified Loc",
        is_verified=True,
        source_system="home_assistant",
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
    assert refreshed.source_system == "home_assistant", (
        "unverify must not change source_system"
    )


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
    This is the backend for the Sessions group-edit bar that
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
    type, network_id, and cost fields.
    """
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


async def test_edit_network_persists_field_changes(client, db_session):
    """PUT /review/networks/{id} persists edits, returns the pending networks
    partial, and fires HX-Trigger: closeNetworkModal."""
    net = await NetworkFactory.create(
        db_session, network_name="Original", is_verified=False
    )
    await db_session.commit()

    response = await client.put(
        f"/review/networks/{net.id}",
        data={"network_name": "Renamed", "cost_per_kwh": "0.31"},
    )
    assert response.status_code == 200

    refreshed = (
        await db_session.execute(
            select(EVChargingNetwork).where(EVChargingNetwork.id == net.id)
        )
    ).scalar_one()
    assert refreshed.network_name == "Renamed"
    assert float(refreshed.cost_per_kwh) == pytest.approx(0.31, abs=0.001)

    assert "closeNetworkModal" in response.headers.get("HX-Trigger", "")
    assert 'hx-get="/review/networks"' in response.text


# ---------------------------------------------------------------------------
# 7. Review-scoped network edit
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
    Subscription tabs hidden).
    """
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
# 8. Page-scope location edit modal + close trigger behavior
# ---------------------------------------------------------------------------


async def test_review_edit_location_fires_close_trigger(client, db_session):
    """POST /review/location/{id}/edit now returns HX-Trigger: closeEditLocModal
    alongside the locations partial. The body listener
    in review_queue.html closes the page-scope #edit-loc-modal on success.
    """
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
    /review/location/{id}/edit and the network <select>.
    """
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


# ---------------------------------------------------------------------------
# 9. Pending filter lock + approved tab rendering checks
# ---------------------------------------------------------------------------


async def test_pending_tab_networks_excludes_verified(client, db_session):
    """GET /review?tab=pending&sub=networks never leaks verified networks.
    Seeds a mix of verified and unverified rows and asserts that only the
    unverified names appear in the page body. Regression-locks the
    filter="unverified" SQL guard in _networks_context.
    """
    unverified_names = ["PendingNetAlpha", "PendingNetBravo"]
    verified_names = ["ApprovedNetCharlie", "ApprovedNetDelta", "ApprovedNetEcho"]
    for name in unverified_names:
        await NetworkFactory.create(db_session, network_name=name, is_verified=False)
    for name in verified_names:
        await NetworkFactory.create(db_session, network_name=name, is_verified=True)
    await db_session.commit()

    response = await client.get("/review?tab=pending&sub=networks")

    assert response.status_code == 200
    body = response.text
    for name in unverified_names:
        assert name in body, f"expected unverified network {name!r} to render"
    # Verified names must not appear as a rendered table row. The merge-target
    # dropdown can include them, so scope the check to the tbody row id pattern
    # used by review_networks_table.html (`id="net-row-{id}"` — only the main
    # table renders these IDs, not the dropdown).
    for v_name in verified_names:
        # The verified row would render as `>ApprovedNetCharlie<` inside the
        # `<td>` of the main `net-row-*` table. A simple substring check on the
        # name would false-positive on dropdown options, so check that no
        # `id="net-row-` marker sits before the verified name in the body.
        # Simpler: confirm the verified name doesn't appear inside the
        # rendered <tbody> at all by checking for the name inside a `<td>`.
        assert f"<td>\n                    {v_name}" not in body and \
               f"<td>{v_name}" not in body, (
            f"verified network {v_name!r} leaked into pending table"
        )


async def test_pending_tab_locations_excludes_verified(client, db_session):
    """GET /review?tab=pending&sub=locations never leaks verified locations."""
    unverified_names = ["PendingLocAlpha", "PendingLocBravo"]
    verified_names = ["ApprovedLocCharlie", "ApprovedLocDelta", "ApprovedLocEcho"]
    for name in unverified_names:
        await LocationLookupFactory.create(
            db_session, location_name=name, is_verified=False
        )
    for name in verified_names:
        await LocationLookupFactory.create(
            db_session, location_name=name, is_verified=True
        )
    await db_session.commit()

    response = await client.get("/review?tab=pending&sub=locations")

    assert response.status_code == 200
    body = response.text
    for name in unverified_names:
        assert name in body, f"expected unverified location {name!r} to render"
    for v_name in verified_names:
        # The locations table renders `<td>{{ loc.location_name }}</td>` at
        # line 45 of review_locations_table.html. Check that exact pattern does
        # not contain the verified name.
        assert f"<td>{v_name}</td>" not in body, (
            f"verified location {v_name!r} leaked into pending table"
        )


async def test_pending_badge_shows_combined_unverified_count(client, db_session):
    """Test that pending badge shows combined unverified count."""
    # 2 unverified networks + 1 verified network
    await NetworkFactory.create(db_session, network_name="UN1", is_verified=False)
    await NetworkFactory.create(db_session, network_name="UN2", is_verified=False)
    await NetworkFactory.create(db_session, network_name="VN1", is_verified=True)
    # 3 unverified locations + 2 verified locations
    for i in range(3):
        await LocationLookupFactory.create(
            db_session, location_name=f"UL{i}", is_verified=False
        )
    for i in range(2):
        await LocationLookupFactory.create(
            db_session, location_name=f"VL{i}", is_verified=True
        )
    await db_session.commit()

    response = await client.get("/review")

    assert response.status_code == 200
    body = response.text
    # Pending badge retained — warning badge with combined unverified count
    # (2 networks + 3 locations = 5).
    assert 'badge badge-sm badge-warning">5<' in body, (
        "expected pending badge-warning to render the combined count '5'"
    )
    # Approved tab's success badge removed.
    assert 'badge badge-sm badge-success' not in body.split("id=\"review-content\"")[0], (
        "Approved tab's badge-success should have been removed from the tab "
        "header — verified-row badges INSIDE the approved tree are fine."
    )


# ---------------------------------------------------------------------------
# 10. Approved tree with "No Network" pseudo-node
# ---------------------------------------------------------------------------


async def test_approved_tree_contains_no_network_pseudo_node_when_standalone_exists(
    client, db_session
):
    """Standalone verified locations render inside the networks tree
    as a synthetic 'No Network' pseudo-node (sentinel id 'none'), not in a
    separate section.
    """
    await NetworkFactory.create(
        db_session, network_name="HasNetTreeNet", is_verified=True
    )
    await LocationLookupFactory.create(
        db_session,
        location_name="FreeStandaloneCharger",
        network_id=None,
        is_verified=True,
    )
    await db_session.commit()

    response = await client.get("/review?tab=approved")

    assert response.status_code == 200
    body = response.text
    # Pseudo-node parent row + child row markers use the 'none' sentinel
    assert 'approved-net-row-none' in body, (
        "expected synthetic pseudo-node parent row (id='approved-net-row-none')"
    )
    assert 'approved-net-children-none' in body, (
        "expected synthetic pseudo-node child row (id='approved-net-children-none')"
    )
    # Standalone location renders INSIDE the child row, not in a separate section
    # — we assert ordering: `approved-net-children-none` appears BEFORE the
    # standalone location name in the body.
    idx_children = body.find('approved-net-children-none')
    idx_loc = body.find('FreeStandaloneCharger')
    assert idx_children >= 0 and idx_loc >= 0
    assert idx_children < idx_loc, (
        "standalone location must render inside the pseudo-node child row, "
        "not in a separate 'Standalone Locations' section before it"
    )
    # The old separate section header must be gone
    assert 'Standalone Locations' not in body, (
        "D-B6: the old 'Standalone Locations' h2 section must be removed"
    )


async def test_approved_tree_omits_pseudo_node_when_no_standalone(client, db_session):
    """The synthetic 'No Network' pseudo-node only appears when there
    are standalone verified locations. With zero standalone rows, the tree
    renders without it.
    """
    net = await NetworkFactory.create(
        db_session, network_name="OnlyNetworkedTreeNet", is_verified=True
    )
    await LocationLookupFactory.create(
        db_session,
        location_name="AttachedLocInTree",
        network_id=net.id,
        is_verified=True,
    )
    await db_session.commit()

    response = await client.get("/review?tab=approved")

    assert response.status_code == 200
    body = response.text
    assert 'approved-net-row-none' not in body, (
        "no-standalone state must NOT render the synthetic 'none' row"
    )
    assert 'approved-net-children-none' not in body, (
        "no-standalone state must NOT render the synthetic 'none' child row"
    )


async def test_approved_tree_renders_network_children_in_nested_rows(
    client, db_session
):
    """Networked verified locations render inside the parent network's
    expandable child row (approved-net-children-{network.id}), not as a flat
    locations table beside the networks table.
    """
    net = await NetworkFactory.create(
        db_session, network_name="ParentTreeNet", is_verified=True
    )
    await LocationLookupFactory.create(
        db_session,
        location_name="ChildLocAlpha",
        network_id=net.id,
        is_verified=True,
    )
    await LocationLookupFactory.create(
        db_session,
        location_name="ChildLocBravo",
        network_id=net.id,
        is_verified=True,
    )
    await db_session.commit()

    response = await client.get("/review?tab=approved")

    assert response.status_code == 200
    body = response.text
    child_row_marker = f'approved-net-children-{net.id}'
    assert child_row_marker in body, (
        f"expected child-row id {child_row_marker!r} in tree markup"
    )
    # Both children must appear AFTER the child-row marker (nested inside it)
    idx_marker = body.find(child_row_marker)
    idx_alpha = body.find('ChildLocAlpha')
    idx_bravo = body.find('ChildLocBravo')
    assert idx_marker >= 0 and idx_alpha >= 0 and idx_bravo >= 0
    assert idx_marker < idx_alpha, (
        "ChildLocAlpha must render inside the expandable child row"
    )
    assert idx_marker < idx_bravo, (
        "ChildLocBravo must render inside the expandable child row"
    )


# ---------------------------------------------------------------------------
# 11. Merge crosses verified/unverified warning
# ---------------------------------------------------------------------------


async def test_merge_preview_network_warns_when_source_verified_target_unverified(
    client, db_session
):
    """When merge preview has a target with different verification status,
    different is_verified status than the source, the modal renders an
    alert-warning line ("This merge crosses verified...") so the user
    understands the surviving row's verification impact.
    The merge submit button remains enabled — the dialog is a foot-gun
    guard, not a block. There is no server-side gating.
    """
    source = await NetworkFactory.create(
        db_session, network_name="VerifiedSource", is_verified=True
    )
    # Target with DIFFERING verified status — triggers the warning.
    await NetworkFactory.create(
        db_session, network_name="UnverifiedTarget", is_verified=False
    )
    await db_session.commit()

    response = await client.get(f"/review/network/{source.id}/merge-preview")

    assert response.status_code == 200
    body = response.text
    assert "This merge crosses verified" in body, (
        "expected cross-verification warning copy when a differing-status "
        "target exists in the dropdown"
    )
    # The submit button itself must not be disabled (UI-only guard).
    assert 'class="btn btn-warning">Merge' in body, (
        "expected Merge submit button to render without `disabled` attribute"
    )
    # Belt-and-suspenders: ensure no disabled Merge button literal
    assert 'disabled>Merge' not in body and "disabled type=\"submit\"" not in body


async def test_merge_preview_network_no_warning_when_same_status(
    client, db_session
):
    """When every available target matches the source's is_verified value,
    the warning is NOT rendered.
    Note: Alembic-seeded predefined networks (ChargePoint, EVgo, etc.) are
    all verified and persist across tests (they live in migration, not the
    per-test transaction). We delete them in this test so the scenario
    "all targets share the source's status" is actually achievable.
    """
    from sqlalchemy import delete
    # Clear the seed-migrated verified networks so the only targets left are
    # our unverified test rows (the per-test transaction rollback reverts this).
    await db_session.execute(
        delete(EVChargingNetwork).where(EVChargingNetwork.is_verified == True)  # noqa: E712
    )
    source = await NetworkFactory.create(
        db_session, network_name="UnverifiedSource", is_verified=False
    )
    # Only targets with the SAME status — no warning should appear.
    await NetworkFactory.create(
        db_session, network_name="AnotherUnverified", is_verified=False
    )
    await db_session.commit()

    response = await client.get(f"/review/network/{source.id}/merge-preview")

    assert response.status_code == 200
    assert "This merge crosses verified" not in response.text


async def test_merge_preview_location_warns_when_source_verified_target_unverified(
    client, db_session
):
    """Symmetric warning behavior for location merges."""
    source = await LocationLookupFactory.create(
        db_session, location_name="VerifiedSourceLoc", is_verified=True
    )
    await LocationLookupFactory.create(
        db_session, location_name="UnverifiedTargetLoc", is_verified=False
    )
    await db_session.commit()

    response = await client.get(f"/review/location/{source.id}/merge-preview")

    assert response.status_code == 200
    body = response.text
    assert "This merge crosses verified" in body, (
        "expected cross-verification warning copy when a differing-status "
        "target location exists in the dropdown"
    )
    assert 'class="btn btn-warning">Merge' in body


async def test_merge_preview_location_no_warning_when_same_status(
    client, db_session
):
    """Symmetric: no warning when location targets all share the source's
    verified status.
    Note: no seeded locations exist today, but clear any that might exist
    to keep the test robust against future seeding.
    """
    from sqlalchemy import delete
    await db_session.execute(
        delete(EVLocationLookup).where(EVLocationLookup.is_verified == True)  # noqa: E712
    )
    source = await LocationLookupFactory.create(
        db_session, location_name="UnverifiedSourceLoc", is_verified=False
    )
    await LocationLookupFactory.create(
        db_session, location_name="AnotherUnverifiedLoc", is_verified=False
    )
    await db_session.commit()

    response = await client.get(f"/review/location/{source.id}/merge-preview")

    assert response.status_code == 200
    assert "This merge crosses verified" not in response.text


# ---------------------------------------------------------------------------
# Session edit modal and drawer network/location cascade behavior
# ---------------------------------------------------------------------------


async def test_single_row_session_modal_location_select_cascades_off_network(
    client, db_session
):
    """The session edit modal includes a network-to-location HTMX cascade.

    The modal form must render ``<select name="location_id">`` and wire
    ``hx-get="/locations/by-network"`` to update that select.
    """
    vehicle = await VehicleFactory.create(db_session)
    net = await NetworkFactory.create(db_session, network_name="ModalCascadeNet")
    loc = await LocationLookupFactory.create(
        db_session,
        location_name="ModalCascadeLoc",
        network_id=net.id,
        is_verified=True,
    )
    session = await ChargingSessionFactory.create(
        db_session,
        device_id=vehicle.device_id,
        network_id=net.id,
        location_id=loc.id,
    )
    await db_session.commit()

    response = await client.get(f"/charging/sessions/{session.id}/modal")

    assert response.status_code == 200
    body = response.text
    assert 'name="location_id"' in body
    assert 'hx-get="/locations/by-network"' in body
    assert 'hx-target="#modal-location-id"' in body
    assert 'id="modal-location-id"' in body


async def test_single_row_session_drawer_location_select_cascades_off_network(
    client, db_session
):
    """The session drawer uses the same network-to-location HTMX cascade.

    The drawer form must render ``<select name="location_id">`` and wire
    ``hx-get="/locations/by-network"`` to update that select.
    """
    vehicle = await VehicleFactory.create(db_session)
    net = await NetworkFactory.create(db_session, network_name="DrawerCascadeNet")
    loc = await LocationLookupFactory.create(
        db_session,
        location_name="DrawerCascadeLoc",
        network_id=net.id,
        is_verified=True,
    )
    session = await ChargingSessionFactory.create(
        db_session,
        device_id=vehicle.device_id,
        network_id=net.id,
        location_id=loc.id,
    )
    await db_session.commit()

    response = await client.get(f"/charging/sessions/{session.id}/detail")

    assert response.status_code == 200
    body = response.text
    assert 'name="location_id"' in body
    assert 'hx-get="/locations/by-network"' in body
    assert 'hx-target="#drawer-location-id"' in body
    assert 'id="drawer-location-id"' in body


# ---------------------------------------------------------------------------
# Associate and Promote actions for unverified location rows
# ---------------------------------------------------------------------------


async def test_associate_modal_returns_picker_form(client, db_session):
    """GET /review/location/{id}/associate-modal returns a lightweight
    picker form wired to POST the chosen network via a datalist combobox.
    The form must post to /review/location/{id}/associate, contain a hidden
    `network_id` input (auto-populated by the datalist resolver), and carry a
    datalist attribute so the combobox UX works. The 'network-datalist'
    substring is intentionally loose — any id of the form ``network-datalist*``
    satisfies the contract and keeps the picker's datalist distinct from the
    edit-modal's network <select>.
    """
    loc = await LocationLookupFactory.create(
        db_session,
        location_name="PickerLocation",
        is_verified=False,
        network_id=None,
    )
    await db_session.commit()

    response = await client.get(f"/review/location/{loc.id}/associate-modal")

    assert response.status_code == 200
    body = response.text
    assert f'hx-post="/review/location/{loc.id}/associate"' in body
    assert 'name="network_id"' in body  # hidden input for resolved id
    assert 'list="network-datalist' in body  # datalist combobox wiring


async def test_associate_location_to_existing_network_does_not_verify(
    client, db_session
):
    """Associating an unverified location with an existing network must
    persist the network_id AND leave is_verified=False. Verification is a
    separate action from association.
    """
    net = await NetworkFactory.create(
        db_session, network_name="ExistingTargetNet", is_verified=True
    )
    loc = await LocationLookupFactory.create(
        db_session,
        location_name="ToBeAssociated",
        is_verified=False,
        network_id=None,
    )
    await db_session.commit()

    response = await client.post(
        f"/review/location/{loc.id}/associate",
        data={
            "network_id": str(net.id),
            "network_name": net.network_name,
        },
    )

    assert response.status_code == 200
    refreshed = (
        await db_session.execute(
            select(EVLocationLookup).where(EVLocationLookup.id == loc.id)
        )
    ).scalar_one()
    assert refreshed.network_id == net.id
    assert refreshed.is_verified is False, (
        "D-C5: Associate must NOT flip is_verified"
    )


async def test_associate_location_to_new_network_creates_unverified_network(
    client, db_session
):
    """Associating with a free-text name that does not match any existing
    network (case-insensitive) creates a brand-new EVChargingNetwork row with
    is_verified=False and source_system='manual', and the location becomes
    associated with it. The location's is_verified is not touched.
    """
    loc = await LocationLookupFactory.create(
        db_session,
        location_name="NewNetAssocLoc",
        is_verified=False,
        network_id=None,
    )
    await db_session.commit()

    # Count existing networks with this name before the POST — must be zero
    # so the handler falls through to the create branch.
    new_name = "Brand New Co"
    pre_count = (
        await db_session.execute(
            select(EVChargingNetwork).where(
                EVChargingNetwork.network_name == new_name
            )
        )
    ).scalars().all()
    assert len(pre_count) == 0

    response = await client.post(
        f"/review/location/{loc.id}/associate",
        data={
            "network_id": "",
            "network_name": new_name,
        },
    )

    assert response.status_code == 200

    # The network was created and carries the expected provenance fields.
    created = (
        await db_session.execute(
            select(EVChargingNetwork).where(
                EVChargingNetwork.network_name == new_name
            )
        )
    ).scalar_one()
    assert created.is_verified is False
    assert created.source_system == "manual"

    # The location now points at that new network, but remains unverified.
    refreshed = (
        await db_session.execute(
            select(EVLocationLookup).where(EVLocationLookup.id == loc.id)
        )
    ).scalar_one()
    assert refreshed.network_id == created.id
    assert refreshed.is_verified is False


async def test_promote_location_creates_network_and_verifies(client, db_session):
    """POST /review/location/{id}/promote creates a new network named
    after the location, sets ``loc.network_id`` to it, and flips
    ``loc.is_verified=True`` — all in one click for the one-off-charger case
    (campground chargers, small businesses, non-branded chargers).
    The new network is is_verified=True + source_system="manual" because the
    user's explicit Promote click vouches for both the location and the
    network that will now carry its name.
    """
    loc = await LocationLookupFactory.create(
        db_session,
        location_name="RV Park Charger",
        is_verified=False,
        network_id=None,
        source_system="home_assistant",
    )
    await db_session.commit()

    # Sanity — no network with this name exists yet.
    pre = (
        await db_session.execute(
            select(EVChargingNetwork).where(
                EVChargingNetwork.network_name == "RV Park Charger"
            )
        )
    ).scalars().all()
    assert len(pre) == 0

    response = await client.post(f"/review/location/{loc.id}/promote")

    assert response.status_code == 200

    created = (
        await db_session.execute(
            select(EVChargingNetwork).where(
                EVChargingNetwork.network_name == "RV Park Charger"
            )
        )
    ).scalar_one()
    assert created.is_verified is True
    assert created.source_system == "manual"

    refreshed = (
        await db_session.execute(
            select(EVLocationLookup).where(EVLocationLookup.id == loc.id)
        )
    ).scalar_one()
    assert refreshed.network_id == created.id
    assert refreshed.is_verified is True
    assert refreshed.source_system == "manual"


async def test_promote_location_works_for_already_networked_location(
    client, db_session
):
    """Promote is valid for any unverified location regardless of its
    current network_id. A location that was auto-associated to the wrong
    network can be Promote'd to its own new network in one click — the new
    network replaces the prior network_id on the location row.
    """
    prior_net = await NetworkFactory.create(
        db_session,
        network_name="PriorMisassociatedNet",
        is_verified=True,
    )
    loc = await LocationLookupFactory.create(
        db_session,
        location_name="DistinctChargerSite",
        is_verified=False,
        network_id=prior_net.id,
    )
    await db_session.commit()

    response = await client.post(f"/review/location/{loc.id}/promote")

    assert response.status_code == 200

    new_net = (
        await db_session.execute(
            select(EVChargingNetwork).where(
                EVChargingNetwork.network_name == "DistinctChargerSite"
            )
        )
    ).scalar_one()
    assert new_net.is_verified is True

    refreshed = (
        await db_session.execute(
            select(EVLocationLookup).where(EVLocationLookup.id == loc.id)
        )
    ).scalar_one()
    # The location's network_id must now point at the freshly-created
    # network — not the prior misassociation.
    assert refreshed.network_id == new_net.id
    assert refreshed.network_id != prior_net.id
    assert refreshed.is_verified is True


# ---------------------------------------------------------------------------
# Merge location into location (Pending and Approved caller paths)
# ---------------------------------------------------------------------------


async def test_review_merge_location_into_location_pending(client, db_session):
    """Merge an unverified source location into an unverified target location.

    `return_to="pending"` must:
    - succeed (200)
    - return the locations-table partial (pending swap zone #review-inner)
    - emit HX-Trigger: closeMergeModal
    - delete the source row
    - keep the target row
    """
    src = await LocationLookupFactory.create(
        db_session,
        location_name="PendingMergeSrc",
        is_verified=False,
        latitude=40.0,
        longitude=-74.0,
    )
    tgt = await LocationLookupFactory.create(
        db_session,
        location_name="PendingMergeTgt",
        is_verified=False,
        latitude=41.0,
        longitude=-75.0,
    )
    await db_session.commit()

    response = await client.post(
        f"/review/location/{src.id}/merge",
        data={"target_id": str(tgt.id), "return_to": "pending"},
    )

    assert response.status_code == 200
    assert response.headers.get("HX-Trigger") == "closeMergeModal"
    # Pending partial signature: the search input driving the locations table
    body = response.text
    assert 'hx-get="/review/locations"' in body

    src_row = (
        await db_session.execute(
            select(EVLocationLookup).where(EVLocationLookup.id == src.id)
        )
    ).scalar_one_or_none()
    assert src_row is None
    tgt_row = (
        await db_session.execute(
            select(EVLocationLookup).where(EVLocationLookup.id == tgt.id)
        )
    ).scalar_one()
    assert tgt_row.location_name == "PendingMergeTgt"


async def test_review_merge_location_into_location_approved(client, db_session):
    """Merge a verified source location into a verified target location.

    `return_to="approved"` must:
    - succeed (200)
    - return the approved-tree partial (approved swap zone #review-content)
    - NOT return the pending locations-table partial
    - emit HX-Trigger: closeMergeModal
    - delete source, keep target
    """
    src = await LocationLookupFactory.create(
        db_session,
        location_name="ApprovedMergeSrc",
        is_verified=True,
        latitude=40.0,
        longitude=-74.0,
    )
    tgt = await LocationLookupFactory.create(
        db_session,
        location_name="ApprovedMergeTgt",
        is_verified=True,
        latitude=41.0,
        longitude=-75.0,
    )
    await db_session.commit()

    response = await client.post(
        f"/review/location/{src.id}/merge",
        data={"target_id": str(tgt.id), "return_to": "approved"},
    )

    assert response.status_code == 200
    assert response.headers.get("HX-Trigger") == "closeMergeModal"
    body = response.text
    # Approved-tree markers
    assert "These verified entries are used" in body
    # Must NOT be the pending locations table
    assert 'hx-get="/review/locations"' not in body

    src_row = (
        await db_session.execute(
            select(EVLocationLookup).where(EVLocationLookup.id == src.id)
        )
    ).scalar_one_or_none()
    assert src_row is None
    tgt_row = (
        await db_session.execute(
            select(EVLocationLookup).where(EVLocationLookup.id == tgt.id)
        )
    ).scalar_one()
    assert tgt_row.location_name == "ApprovedMergeTgt"


async def test_review_merge_location_preview_accepts_return_to_and_renders_hidden_input(
    client, db_session
):
    """The location merge preview echoes ``return_to`` as a hidden input.
    """
    src = await LocationLookupFactory.create(
        db_session, location_name="PreviewLocSrc", is_verified=True
    )
    await LocationLookupFactory.create(
        db_session, location_name="PreviewLocTgt", is_verified=True
    )
    await db_session.commit()

    response = await client.get(
        f"/review/location/{src.id}/merge-preview?return_to=approved"
    )

    assert response.status_code == 200
    assert b'name="return_to"' in response.content
    assert b'value="approved"' in response.content
    assert b'hx-post="/review/location/' in response.content


async def test_review_merge_location_crosses_warning_still_renders_on_preview(
    client, db_session
):
    """Location merge preview keeps cross-verification warning UI in place.

    The warning block and ``data-is-verified`` attributes must still render
    even when ``return_to`` routing is present.
    """
    src = await LocationLookupFactory.create(
        db_session, location_name="CrossSrcLoc", is_verified=False
    )
    await LocationLookupFactory.create(
        db_session, location_name="CrossTgtVerifiedLoc", is_verified=True
    )
    await db_session.commit()

    response = await client.get(f"/review/location/{src.id}/merge-preview")

    assert response.status_code == 200
    body = response.text
    assert f"merge-location-cross-warning-{src.id}" in body
    # The data-is-verified attribute on at least one target option — the
    # verified target we created above ensures has_crossing=True.
    assert 'data-is-verified="1"' in body


async def test_review_merge_network_preview_accepts_return_to(client, db_session):
    """The network merge preview echoes ``return_to`` as a hidden input.
    """
    src = await NetworkFactory.create(
        db_session, network_name="PreviewNetSrc", is_verified=True
    )
    await NetworkFactory.create(
        db_session, network_name="PreviewNetTgt", is_verified=True
    )
    await db_session.commit()

    response = await client.get(
        f"/review/network/{src.id}/merge-preview?return_to=approved"
    )

    assert response.status_code == 200
    assert b'name="return_to"' in response.content
    assert b'value="approved"' in response.content


# ---------------------------------------------------------------------------
# Approved-tab edit dialog behavior
# ---------------------------------------------------------------------------


async def test_review_edit_approved_location_fires_close_trigger(client, db_session):
    """Approved-tab edit save returns the approved tree and close trigger.

    The response should target the approved tree partial and emit
    ``HX-Trigger: closeEditLocModal`` so the modal closes on success.
    """
    loc = await LocationLookupFactory.create(
        db_session,
        location_name="Before Approved Rename",
        is_verified=True,
    )
    await db_session.commit()

    response = await client.post(
        f"/review/location/{loc.id}/edit",
        data={
            "location_name": "Renamed Approved",
            "address": "",
            "location_type": "",
            "network_id": "",
            "latitude": "",
            "longitude": "",
            "cost_per_kwh": "",
            "return_to": "approved",
        },
    )

    assert response.status_code == 200
    assert response.headers.get("HX-Trigger") == "closeEditLocModal"
    body = response.text
    # Approved-tree partial markers
    assert "These verified entries are used" in body
    # Must NOT be the pending locations table
    assert 'hx-get="/review/locations"' not in body

    refreshed = (
        await db_session.execute(
            select(EVLocationLookup).where(EVLocationLookup.id == loc.id)
        )
    ).scalar_one()
    assert refreshed.location_name == "Renamed Approved"


async def test_review_location_edit_form_accepts_return_to_approved(
    client, db_session
):
    """Edit form accepts ``return_to=approved`` and preserves it in markup.
    """
    loc = await LocationLookupFactory.create(
        db_session,
        location_name="EditFormApprovedCtx",
        is_verified=True,
    )
    await db_session.commit()

    response = await client.get(
        f"/review/location/{loc.id}/edit-form?return_to=approved"
    )

    assert response.status_code == 200
    assert b'name="return_to"' in response.content
    assert b'value="approved"' in response.content
    assert b'hx-post="/review/location/' in response.content


async def test_review_location_edit_form_default_return_to_is_pending(
    client, db_session
):
    """Edit form defaults ``return_to`` to ``pending`` when not provided."""
    loc = await LocationLookupFactory.create(
        db_session,
        location_name="EditFormDefaultCtx",
        is_verified=False,
    )
    await db_session.commit()

    response = await client.get(f"/review/location/{loc.id}/edit-form")

    assert response.status_code == 200
    assert b'value="pending"' in response.content


# ---------------------------------------------------------------------------
# Action-cell visual grouping and button order
# ---------------------------------------------------------------------------


async def test_review_pending_locations_actions_use_cluster_grouping(
    client, db_session
):
    """Pending location rows render grouped actions in a fixed order.

    Expected order: Verify, Edit, Associate, Promote, Merge, Delete.
    """
    await LocationLookupFactory.create(
        db_session,
        location_name="G3PendingActionsLoc",
        is_verified=False,
    )
    await db_session.commit()

    response = await client.get("/review/locations")

    assert response.status_code == 200

    # Cluster marker present — three clusters × one row.
    assert b"review-action-cluster" in response.content
    assert response.content.count(b"review-action-cluster") == 3

    # D-UX2 left→right order: Verify, Edit, Associate, Promote, Merge, Delete.
    verify_pos = response.content.find(b">\n                            Verify\n")
    edit_pos = response.content.find(b">\n                            Edit\n")
    associate_pos = response.content.find(
        b">\n                            Associate\n"
    )
    promote_pos = response.content.find(b">\n                            Promote\n")
    merge_pos = response.content.find(b">\n                            Merge\n")
    delete_pos = response.content.find(b">\n                            Delete\n")
    # All six labels must render.
    assert verify_pos != -1
    assert edit_pos != -1
    assert associate_pos != -1
    assert promote_pos != -1
    assert merge_pos != -1
    assert delete_pos != -1
    # Locked order from D-UX2.
    assert (
        verify_pos
        < edit_pos
        < associate_pos
        < promote_pos
        < merge_pos
        < delete_pos
    )

    # D-UX3: Actions <th> carries w-56.
    assert b'<th class="w-56">Actions</th>' in response.content


async def test_review_approved_tree_location_actions_use_cluster_grouping(
    client, db_session
):
    """Approved child-location rows render grouped actions in fixed order.

    Expected order: Unverify, Edit, Merge.
    """
    net = await NetworkFactory.create(
        db_session, network_name="G3ApprovedActionsNet", is_verified=True
    )
    await LocationLookupFactory.create(
        db_session,
        location_name="G3ApprovedChildLoc",
        network_id=net.id,
        is_verified=True,
    )
    await db_session.commit()

    response = await client.get("/review/approved")

    assert response.status_code == 200

    # Cluster marker present on the child-loc row(s).
    assert b"review-action-cluster" in response.content

    # D-UX2 (approved-adapted) order on the location row: Unverify, Edit, Merge.
    unverify_pos = response.content.find(
        b">\n                                                Unverify\n"
    )
    edit_pos = response.content.find(
        b">\n                                                Edit\n"
    )
    merge_pos = response.content.find(
        b">\n                                                Merge\n"
    )
    assert unverify_pos != -1
    assert edit_pos != -1
    assert merge_pos != -1
    assert unverify_pos < edit_pos < merge_pos

    # D-UX3: inner-table Actions <th> carries w-56.
    assert b'<th class="text-right w-56">Actions</th>' in response.content


async def test_review_approved_tree_standalone_location_actions_use_cluster_grouping(
    client, db_session
):
    """Standalone approved locations use the same grouped action order."""
    await LocationLookupFactory.create(
        db_session,
        location_name="G3ApprovedStandaloneLoc",
        network_id=None,
        is_verified=True,
    )
    await db_session.commit()

    response = await client.get("/review/approved")

    assert response.status_code == 200

    # Standalone pseudo-node rendered — confirms we are hitting the
    # standalone-location branch of the tree.
    assert b'<tr id="approved-net-row-none"' in response.content

    # Cluster marker present on the standalone row.
    assert b"review-action-cluster" in response.content

    # D-UX2 (approved-adapted) order: Unverify, Edit, Merge.
    unverify_pos = response.content.find(
        b">\n                                                Unverify\n"
    )
    edit_pos = response.content.find(
        b">\n                                                Edit\n"
    )
    merge_pos = response.content.find(
        b">\n                                                Merge\n"
    )
    assert unverify_pos != -1
    assert edit_pos != -1
    assert merge_pos != -1
    assert unverify_pos < edit_pos < merge_pos


# ---------------------------------------------------------------------------
# Pending merge responses must keep verified rows out of pending tables
# ---------------------------------------------------------------------------
# These tests lock a past bug where pending merge handlers returned an
# unfiltered partial and leaked verified rows into pending tables.


async def test_review_merge_location_pending_excludes_verified(client, db_session):
    """Pending location merge response contains only unverified rows.

    Seed 1 verified + 2 unverified locations. Merge the two unverified rows
    on the Pending tab. Assert verified row does NOT appear in the response
    body. This would fail against review.py:1172 _locations_context(db)
    without filter="unverified" and passes after the fix.
    """
    verified_name = "VerifiedLocLeak"
    src_name = "PendingMergeSrcFilter"
    tgt_name = "PendingMergeTgtFilter"
    await LocationLookupFactory.create(
        db_session, location_name=verified_name, is_verified=True
    )
    src = await LocationLookupFactory.create(
        db_session,
        location_name=src_name,
        is_verified=False,
        latitude=40.0,
        longitude=-74.0,
    )
    tgt = await LocationLookupFactory.create(
        db_session,
        location_name=tgt_name,
        is_verified=False,
        latitude=41.0,
        longitude=-75.0,
    )
    await db_session.commit()

    response = await client.post(
        f"/review/location/{src.id}/merge",
        data={"target_id": str(tgt.id), "return_to": "pending"},
    )

    assert response.status_code == 200
    assert response.headers.get("HX-Trigger") == "closeMergeModal"
    body = response.text
    # Partial shape still present.
    assert 'hx-get="/review/locations"' in body
    # Target unverified row stays visible.
    assert tgt_name in body, (
        f"expected unverified target {tgt_name!r} to render post-merge"
    )
    # Verified row must not leak into the pending partial.
    assert f"<td>{verified_name}</td>" not in body, (
        f"verified location {verified_name!r} leaked into pending "
        "merge response body on merge_location POST"
    )
    # Source row deleted.
    src_row = (
        await db_session.execute(
            select(EVLocationLookup).where(EVLocationLookup.id == src.id)
        )
    ).scalar_one_or_none()
    assert src_row is None


async def test_review_merge_network_pending_excludes_verified(client, db_session):
    """Pending network merge response contains only unverified rows.

    Symmetric to ``test_review_merge_location_pending_excludes_verified``.
    """
    verified_name = "VerifiedNetLeak"
    src_name = "PendingNetMergeSrc"
    tgt_name = "PendingNetMergeTgt"
    await NetworkFactory.create(
        db_session, network_name=verified_name, is_verified=True
    )
    src = await NetworkFactory.create(
        db_session, network_name=src_name, is_verified=False
    )
    tgt = await NetworkFactory.create(
        db_session, network_name=tgt_name, is_verified=False
    )
    await db_session.commit()

    response = await client.post(
        f"/review/network/{src.id}/merge",
        data={"target_id": str(tgt.id), "return_to": "pending"},
    )

    assert response.status_code == 200
    assert response.headers.get("HX-Trigger") == "closeMergeModal"
    body = response.text
    # Partial shape still present.
    assert 'hx-get="/review/networks"' in body
    # Target unverified row stays visible.
    assert tgt_name in body, (
        f"expected unverified target {tgt_name!r} to render post-merge"
    )
    # Verified row must not leak. Networks-table may indent cell contents.
    assert (
        f"<td>{verified_name}</td>" not in body
        and f"<td>\n                    {verified_name}" not in body
    ), (
        f"verified network {verified_name!r} leaked into pending "
        "merge response body on merge_network POST"
    )
    # Source row deleted.
    src_row = (
        await db_session.execute(
            select(EVChargingNetwork).where(EVChargingNetwork.id == src.id)
        )
    ).scalar_one_or_none()
    assert src_row is None
