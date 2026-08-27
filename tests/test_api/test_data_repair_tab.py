"""API tests for the Settings Data Repair tab: cards, preview, apply, snapshots."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from db.models.repair_backup import RepairBackup
from db.models.trip_metrics import EVTripMetrics
from tests.factories.trips import TripFactory
from web.services.repair import (
    RepairDiff,
    RepairGroup,
    RepairOperation,
    RepairPreview,
)

pytestmark = pytest.mark.db

T0 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
CONSOLIDATION = "trip-duplicate-consolidation"


async def _seed_corrupt_pair(db_session):
    """Seed a x1.609 duplicate trip pair (both ha_fordpass, ~1 min apart)."""
    survivor = await TripFactory.create(
        db_session,
        device_id="TEST_VIN_001",
        distance=122.0,
        end_time=T0,
        source_system="ha_fordpass",
    )
    loser = await TripFactory.create(
        db_session,
        device_id="TEST_VIN_001",
        distance=196.34,
        end_time=T0 + timedelta(seconds=60),
        source_system="ha_fordpass",
    )
    await db_session.commit()
    return survivor, loser


async def _trip_count(db_session) -> int:
    stmt = select(func.count()).select_from(EVTripMetrics)
    return (await db_session.execute(stmt)).scalar_one()


async def test_tab_renders_all_ops_and_snapshot_section(client):
    response = await client.get("/settings/data-repair")
    assert response.status_code == 200
    body = response.text
    assert "Trip duplicate consolidation" in body
    assert "Trip distance double conversion" in body
    assert "Derive trip fields from telemetry" in body
    assert "Recorder history replay" in body
    assert "repair-snapshots" in body
    # No runtime in tests -> replay card disabled with the disconnected note.
    assert "Connect to Home Assistant first." in body
    assert "recorder replay unavailable" in body


class _FakeRuntime:
    """Minimal connected-runtime stand-in serving canned events history."""

    def __init__(self, states):
        self.detected_vin = "TESTVIN001"
        self.health = {"connected": True}
        self._states = states

    async def _fetch_entity_history(
        self, entity_id, start_time_iso=None, end_time_iso=None
    ):
        return self._states


@pytest.fixture
def replay_op():
    """Registry replay op with runtime seam + window cache reset around the test."""
    from web.services.repair.registry import get_operation

    op = get_operation("recorder-replay")
    saved = (op._runtime, op._window, op._window_probed_at)
    op._window = None
    op._window_probed_at = None
    yield op
    op._runtime, op._window, op._window_probed_at = saved


async def test_tab_connected_but_no_recorder_history(client, replay_op):
    replay_op._runtime = _FakeRuntime([])
    response = await client.get("/settings/data-repair")
    assert response.status_code == 200
    body = response.text
    assert "recorder returned no history" in body
    assert "not connected" not in body
    assert "No recorder history available." in body
    assert "Connect to Home Assistant first." not in body


async def test_tab_connected_with_recorder_history(client, replay_op):
    window_ts = datetime.now(UTC) - timedelta(days=3)
    replay_op._runtime = _FakeRuntime([{"last_updated": window_ts.isoformat()}])
    response = await client.get("/settings/data-repair")
    assert response.status_code == 200
    body = response.text
    assert "Replay window: since" in body
    assert "recorder replay unavailable" not in body


async def test_replay_apply_stays_enabled_at_census_zero(client, replay_op):
    """Replay recovers never-ingested trips, so a clean census must not grey it out."""
    window_ts = datetime.now(UTC) - timedelta(days=3)
    replay_op._runtime = _FakeRuntime([{"last_updated": window_ts.isoformat()}])
    response = await client.get("/settings/data-repair")
    assert response.status_code == 200
    body = response.text
    # Only rendered for an operation that declares it runs with a clean census.
    assert "Replay stored history?" in body
    # The other ops are clean too, and theirs stay on the snapshot confirm.
    assert "Snapshot and repair 0 rows?" in body


async def test_census_badge_for_seeded_pair(client, db_session):
    await _seed_corrupt_pair(db_session)
    response = await client.get("/settings/data-repair")
    assert response.status_code == 200
    assert "badge badge-warning" in response.text
    assert "1 rows" in response.text


async def test_telemetry_derive_card_shows_census_badge(client, db_session):
    await TripFactory.create(
        db_session,
        device_id="TEST_VIN_002",
        source_system="ha_fordpass",
        distance=30.0,
        energy_consumed=6.0,
        efficiency=None,
        end_time=T0,
    )
    await db_session.commit()
    response = await client.get("/settings/data-repair")
    assert response.status_code == 200
    body = response.text
    assert "Derive trip fields from telemetry" in body
    # The derivable trip lights the census badge on the telemetry card.
    assert 'hx-post="/settings/data-repair/telemetry-derive/preview"' in body
    assert "badge badge-warning" in body
    assert "1 rows" in body


async def test_preview_shows_both_halves_of_a_pair_with_its_evidence(
    client, db_session
):
    """The pair renders as one unit: both rows, the ratio that matched them,
    and the loser's values alongside the merged result they feed."""
    survivor, loser = await _seed_corrupt_pair(db_session)
    response = await client.post(f"/settings/data-repair/{CONSOLIDATION}/preview")
    assert response.status_code == 200
    body = response.text

    # One group holding both rows, not two unrelated table rows.
    assert body.count("<details") == 1
    assert f"Trips #{survivor.id} + #{loser.id}" in body
    assert ">update</span>" in body
    assert ">delete</span>" in body
    assert ">keep<" in body and ">duplicate<" in body

    # The detection evidence — 196.34 / 122.0 — is on screen.
    assert "distance ratio" in body
    assert "1.609" in body

    # The doomed row's own values are rendered, not a static "row removed".
    assert "196.34" in body
    assert "row removed" not in body


async def test_preview_delete_column_carries_the_merged_values_origin(
    client, db_session
):
    """A field merged onto the survivor shows its source value on the loser."""
    await TripFactory.create(
        db_session,
        device_id="ORIGIN_VIN",
        distance=122.0,
        end_time=T0,
        ambient_temp=None,
        source_system="ha_fordpass",
    )
    await TripFactory.create(
        db_session,
        device_id="ORIGIN_VIN",
        distance=196.34,
        end_time=T0 + timedelta(seconds=30),
        ambient_temp=21.5,
        source_system="ha_fordpass",
    )
    await db_session.commit()

    body = (
        await client.post(f"/settings/data-repair/{CONSOLIDATION}/preview")
    ).text
    # ambient_temp appears twice: NULL -> 21.5 on the survivor, 21.5 on the loser.
    assert body.count("21.5") >= 2
    assert "ambient_temp" in body


async def _seed_pairs(db_session, count: int) -> None:
    """`count` x1.609 duplicate pairs, each an hour apart from the next."""
    for i in range(count):
        end = T0 + timedelta(hours=i)
        await TripFactory.create(
            db_session,
            device_id="PAGING_VIN",
            distance=122.0,
            end_time=end,
            source_system="ha_fordpass",
        )
        await TripFactory.create(
            db_session,
            device_id="PAGING_VIN",
            distance=196.34,
            end_time=end + timedelta(seconds=30),
            source_system="ha_fordpass",
        )
    await db_session.commit()


async def test_preview_pages_by_pair_and_never_splits_one(client, db_session):
    """Paging is in whole pairs: a page boundary can't separate two twins."""
    from web.routes.settings import PREVIEW_PAGE_SIZE

    total = PREVIEW_PAGE_SIZE + 2
    await _seed_pairs(db_session, total)

    first = await client.post(f"/settings/data-repair/{CONSOLIDATION}/preview")
    assert first.status_code == 200
    assert first.text.count("<details") == PREVIEW_PAGE_SIZE
    # Both halves of every pair on the page — no orphaned survivor or twin.
    assert first.text.count(">keep<") == PREVIEW_PAGE_SIZE
    assert first.text.count(">duplicate<") == PREVIEW_PAGE_SIZE
    assert f"{total} pairs to review" in first.text
    assert f"showing 1&ndash;{PREVIEW_PAGE_SIZE}" in first.text

    last = await client.post(
        f"/settings/data-repair/{CONSOLIDATION}/preview",
        params={"offset": PREVIEW_PAGE_SIZE},
    )
    assert last.status_code == 200
    assert last.text.count("<details") == total - PREVIEW_PAGE_SIZE
    assert last.text.count(">keep<") == total - PREVIEW_PAGE_SIZE
    assert last.text.count(">duplicate<") == total - PREVIEW_PAGE_SIZE
    assert f"showing {PREVIEW_PAGE_SIZE + 1}&ndash;{total}" in last.text


def _pager_buttons(body: str) -> dict[str, str]:
    """Map each pager button's label to its own <button ...> markup."""
    found = re.findall(r"<button[^>]*>\s*(Previous|Next)", body)
    tags = re.findall(r"<button[^>]*>(?=\s*(?:Previous|Next))", body)
    return dict(zip(found, tags, strict=True))


async def test_preview_pager_disables_the_edges(client, db_session):
    """Previous is dead on the first page and Next on the last."""
    from web.routes.settings import PREVIEW_PAGE_SIZE

    await _seed_pairs(db_session, PREVIEW_PAGE_SIZE + 1)

    first = _pager_buttons(
        (await client.post(f"/settings/data-repair/{CONSOLIDATION}/preview")).text
    )
    assert "disabled" in first["Previous"]
    assert "disabled" not in first["Next"]

    last = _pager_buttons(
        (
            await client.post(
                f"/settings/data-repair/{CONSOLIDATION}/preview",
                params={"offset": PREVIEW_PAGE_SIZE},
            )
        ).text
    )
    assert "disabled" not in last["Previous"]
    assert "disabled" in last["Next"]
    assert "preview?offset=0" in last["Previous"]


async def test_preview_single_page_has_no_pager(client, db_session):
    await _seed_corrupt_pair(db_session)
    body = (await client.post(f"/settings/data-repair/{CONSOLIDATION}/preview")).text
    assert "Previous" not in body
    assert "1 pair to review" in body


async def test_preview_offset_past_the_end_renders_empty(client, db_session):
    await _seed_corrupt_pair(db_session)
    response = await client.post(
        f"/settings/data-repair/{CONSOLIDATION}/preview", params={"offset": 500}
    )
    assert response.status_code == 200
    assert "Nothing to repair." in response.text


async def test_apply_consolidates_and_rerenders_clean_card(client, db_session):
    await _seed_corrupt_pair(db_session)
    response = await client.post(f"/settings/data-repair/{CONSOLIDATION}/apply")
    assert response.status_code == 200
    body = response.text
    assert "badge badge-ghost" in body
    assert ">clean</span>" in body
    assert "toast" in body
    assert "alert-success" in body
    assert await _trip_count(db_session) == 1


async def test_snapshot_lifecycle_over_api(client, db_session):
    await _seed_corrupt_pair(db_session)
    apply_response = await client.post(f"/settings/data-repair/{CONSOLIDATION}/apply")
    assert apply_response.status_code == 200
    assert await _trip_count(db_session) == 1

    tab = await client.get("/settings/data-repair")
    assert CONSOLIDATION in tab.text  # run row lists the operation

    run_id = (
        await db_session.execute(select(RepairBackup.run_id).limit(1))
    ).scalar_one()

    restore = await client.post(f"/settings/data-repair/snapshots/{run_id}/restore")
    assert restore.status_code == 200
    assert "Restored 2 rows." in restore.text
    assert await _trip_count(db_session) == 2

    purge = await client.post(f"/settings/data-repair/snapshots/{run_id}/purge")
    assert purge.status_code == 200
    assert "Snapshot deleted." in purge.text
    assert "No snapshots yet." in purge.text


async def test_unknown_slug_404(client):
    preview = await client.post("/settings/data-repair/not-a-repair/preview")
    assert preview.status_code == 404
    apply_ = await client.post("/settings/data-repair/not-a-repair/apply")
    assert apply_.status_code == 404


async def test_settings_tab_query_param_checks_radio(client):
    response = await client.get("/settings", params={"tab": "data_repair"})
    assert response.status_code == 200
    assert 'aria-label="Data Repair" checked' in response.text


async def test_backup_download_or_pg_instructions(client, db_session):
    """SQLite installs stream a valid DB copy; PostgreSQL installs get 409 + pg_dump hint."""
    dialect = db_session.get_bind().dialect.name
    response = await client.get("/settings/data-repair/backup")
    if dialect == "sqlite":
        assert response.status_code == 200
        assert response.headers["content-disposition"].startswith("attachment")
        assert response.content.startswith(b"SQLite format 3\x00")
    else:
        assert response.status_code == 409
        assert "pg_dump" in response.text


async def test_tab_shows_backup_section(client, db_session):
    dialect = db_session.get_bind().dialect.name
    response = await client.get("/settings/data-repair")
    assert response.status_code == 200
    assert "Back up first" in response.text
    if dialect == "sqlite":
        assert "Download database backup" in response.text
    else:
        # The command must expand POSTGRES_* inside the container; the bare
        # host-shell form fails with `role "-d" does not exist`.
        assert "sh -c &#39;pg_dump" in response.text or "sh -c 'pg_dump" in response.text


# ---------------------------------------------------------------------------
# Operations that supply no review context
# ---------------------------------------------------------------------------


class _BareOperation(RepairOperation):
    """Supplies only the required diff fields: no identity, notes, or context."""

    slug = "test-bare-operation"
    display_name = "Test bare operation"
    description = "Registered by tests only."
    model = EVTripMetrics

    async def census(self, db):
        return 3

    async def preview(self, db, limit=10, offset=0):
        groups = [
            RepairGroup([RepairDiff(11, {"distance": 1.5}, {"distance": 2.5}, "update")]),
            RepairGroup([RepairDiff(12, {"distance": 9.5, "duration": 640}, None, "delete")]),
            RepairGroup([RepairDiff(13, None, {"distance": 7.5}, "insert")]),
        ]
        return RepairPreview(groups[offset : offset + limit], len(groups), offset, limit)

    async def affected_rows(self, db):
        return []

    async def execute(self, db):
        return 0


@pytest.fixture
def bare_op():
    """Register a context-free operation for the length of one test."""
    from web.services.repair.registry import REPAIR_REGISTRY

    op = _BareOperation()
    REPAIR_REGISTRY.append(op)
    yield op
    REPAIR_REGISTRY.remove(op)


async def test_preview_renders_every_action_with_its_data(client, bare_op):
    """Update, delete, and insert each show the values they carry."""
    body = (await client.post(f"/settings/data-repair/{bare_op.slug}/preview")).text

    assert body.count("<details") == 3
    # update: both sides of the change.
    assert "1.5" in body and "2.5" in body
    # delete: what is being lost, not a stub sentence.
    assert "9.5" in body and "640" in body
    # insert: what is arriving.
    assert "7.5" in body


async def test_preview_renders_without_any_context(client, bare_op):
    """No label, identity, notes, or evidence still renders a usable page."""
    response = await client.post(f"/settings/data-repair/{bare_op.slug}/preview")
    assert response.status_code == 200
    body = response.text
    # Falls back to naming rows by id.
    assert "Row 11" in body and "Row 12" in body and "Row 13" in body
    assert "3 rows to review" in body
