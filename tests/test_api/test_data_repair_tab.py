"""API tests for the Settings Data Repair tab: cards, preview, apply, snapshots."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from db.models.repair_backup import RepairBackup
from db.models.trip_metrics import EVTripMetrics
from tests.factories.trips import TripFactory

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


async def test_census_badge_for_seeded_pair(client, db_session):
    await _seed_corrupt_pair(db_session)
    response = await client.get("/settings/data-repair")
    assert response.status_code == 200
    assert "badge badge-warning" in response.text
    assert "1 rows" in response.text


async def test_preview_returns_diff_table(client, db_session):
    await _seed_corrupt_pair(db_session)
    response = await client.post(f"/settings/data-repair/{CONSOLIDATION}/preview")
    assert response.status_code == 200
    body = response.text
    assert "table table-sm" in body
    assert ">update</span>" in body
    assert ">delete</span>" in body


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
        assert "pg_dump" in response.text
