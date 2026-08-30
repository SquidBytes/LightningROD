"""Trip duplicate consolidation: pair matcher, merge precedence, DB lifecycle.

Covers the trip-duplicate-consolidation repair op:
  - find_unit_duplicate_pairs ratio band, time window, NULL handling, greed
  - merge_pair precedence (temps events-canonical, scores elveh-canonical,
    energy/duration MAX, distance/efficiency pinned to the survivor)
  - census/preview/apply/restore lifecycle plus the manual-rows invariant
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from db.models.repair_backup import RepairBackup
from db.models.trip_metrics import EVTripMetrics
from tests.factories.trips import TripFactory
from tests.factories.vehicles import VehicleFactory
from web.services.repair import restore_run
from web.services.repair.ops.trip_duplicates import (
    TripDuplicateConsolidation,
    find_unit_duplicate_pairs,
    merge_pair,
)

pytestmark = pytest.mark.unit

T0 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _row(id: int, distance, end_time=T0, device_id="VIN_A", **extra) -> dict:
    row = {"id": id, "device_id": device_id, "end_time": end_time, "distance": distance}
    row.update(extra)
    return row


# ---------------------------------------------------------------------------
# find_unit_duplicate_pairs
# ---------------------------------------------------------------------------


def test_exact_conversion_factor_pair_matches():
    survivor = _row(1, 122.0)
    loser = _row(2, 122.0 * 1.609344, end_time=T0 + timedelta(seconds=30))
    pairs = find_unit_duplicate_pairs([loser, survivor])
    assert pairs == [(survivor, loser)]  # smaller distance survives


@pytest.mark.parametrize("ratio", [1.50, 1.70])
def test_ratio_outside_band_rejected(ratio):
    rows = [_row(1, 100.0), _row(2, 100.0 * ratio)]
    assert find_unit_duplicate_pairs(rows) == []


def test_end_time_gap_over_two_minutes_rejected():
    rows = [_row(1, 100.0), _row(2, 160.9344, end_time=T0 + timedelta(minutes=3))]
    assert find_unit_duplicate_pairs(rows) == []


def test_null_fields_never_pair():
    rows = [
        _row(1, None),
        _row(2, 160.9344),
        _row(3, 100.0, end_time=None),
        _row(4, 100.0, device_id=None),
    ]
    assert find_unit_duplicate_pairs(rows) == []


def test_different_device_rejected():
    rows = [_row(1, 100.0), _row(2, 160.9344, device_id="VIN_B")]
    assert find_unit_duplicate_pairs(rows) == []


def test_greedy_each_row_in_at_most_one_pair():
    a = _row(1, 100.0)
    b = _row(2, 160.9344)
    c = _row(3, 160.9344)  # could also pair with a, but a is taken
    pairs = find_unit_duplicate_pairs([a, b, c])
    assert pairs == [(a, b)]


# A genuine twin is one drive written twice: same start, same duration, same
# kWh, only the distance re-converted. These fixtures hold that shape.
_SAME_DRIVE = {
    "start_time": T0 - timedelta(minutes=15),
    "duration": 900.0,
    "energy_consumed": 18.0,
}


def test_shared_end_time_but_different_drive_rejected():
    """Two drives that ended at one instant, half an hour and 55% of the
    energy apart. Their 1.625 distance ratio lands in the band by coincidence."""
    end = datetime(2026, 6, 1, 8, 30, 12, tzinfo=UTC)
    short = _row(
        1,
        8,
        end_time=end,
        start_time=end - timedelta(seconds=655),
        duration=655,
        energy_consumed=1.594,
    )
    long_run = _row(
        2,
        13,
        end_time=end,
        start_time=end - timedelta(seconds=2481),
        duration=2481,
        energy_consumed=2.463,
    )
    assert find_unit_duplicate_pairs([short, long_run]) == []


def test_genuine_twin_differs_only_in_distance():
    """One drive, two sources: the distance is x1.609, everything else drifts
    by rounding at most."""
    survivor = _row(1, 122.0, **_SAME_DRIVE)
    loser = _row(
        2,
        122.0 * 1.609344,
        end_time=T0 + timedelta(seconds=30),
        start_time=_SAME_DRIVE["start_time"] + timedelta(seconds=5),
        duration=905.0,
        energy_consumed=18.4,
    )
    assert find_unit_duplicate_pairs([loser, survivor]) == [(survivor, loser)]


@pytest.mark.parametrize(
    "disagreement",
    [
        {"start_time": T0 - timedelta(minutes=45)},  # began half an hour earlier
        {"duration": 2400.0},  # ran nearly three times as long
        {"energy_consumed": 30.0},  # drew 60% more energy
    ],
    ids=["start_time", "duration", "energy_consumed"],
)
def test_one_disagreeing_measurement_rejects_the_pair(disagreement):
    survivor = _row(1, 122.0, **_SAME_DRIVE)
    loser = _row(
        2,
        122.0 * 1.609344,
        end_time=T0 + timedelta(seconds=30),
        **{**_SAME_DRIVE, **disagreement},
    )
    assert find_unit_duplicate_pairs([survivor, loser]) == []


@pytest.mark.parametrize("field", ["start_time", "duration", "energy_consumed"])
@pytest.mark.parametrize("missing_on", ["survivor", "loser"])
def test_field_only_one_source_recorded_still_pairs(field, missing_on):
    """A NULL proves nothing: the checks that can still run decide the pair."""
    survivor_fields, loser_fields = dict(_SAME_DRIVE), dict(_SAME_DRIVE)
    target = survivor_fields if missing_on == "survivor" else loser_fields
    target[field] = None
    survivor = _row(1, 122.0, **survivor_fields)
    loser = _row(
        2, 122.0 * 1.609344, end_time=T0 + timedelta(seconds=30), **loser_fields
    )
    assert find_unit_duplicate_pairs([survivor, loser]) == [(survivor, loser)]


def test_missing_energy_does_not_excuse_a_different_start():
    survivor = _row(1, 122.0, **{**_SAME_DRIVE, "energy_consumed": None})
    loser = _row(
        2,
        122.0 * 1.609344,
        end_time=T0 + timedelta(seconds=30),
        start_time=T0 - timedelta(minutes=45),
        duration=900.0,
        energy_consumed=None,
    )
    assert find_unit_duplicate_pairs([survivor, loser]) == []


def test_string_end_time_coerced():
    rows = [_row(1, 100.0, end_time="2026-06-01T12:00:00Z"), _row(2, 160.9344)]
    assert len(find_unit_duplicate_pairs(rows)) == 1


# ---------------------------------------------------------------------------
# merge_pair
# ---------------------------------------------------------------------------


def test_temps_from_events_canonical_loser_win():
    survivor = _row(1, 122.0, driving_score=90.0)  # elveh-canonical, no temps
    loser = _row(2, 196.34, ambient_temp=21.5, cabin_temp=22.0)
    merged = merge_pair(survivor, loser)
    assert merged["ambient_temp"] == 21.5
    assert merged["cabin_temp"] == 22.0


def test_scores_from_elveh_canonical_loser_win():
    survivor = _row(1, 122.0, ambient_temp=21.5)  # events-canonical, no scores
    loser = _row(2, 196.34, driving_score=88.0, range_regenerated=4.2)
    merged = merge_pair(survivor, loser)
    assert merged["driving_score"] == 88.0
    assert merged["range_regenerated"] == 4.2


def test_distance_and_efficiency_never_merged():
    # P34's MAX-wins would take 196.34; the repair must keep 122.0.
    survivor = _row(1, 122.0, efficiency=6.1)
    loser = _row(2, 196.34, efficiency=9.8)
    merged = merge_pair(survivor, loser)
    assert "distance" not in merged
    assert "efficiency" not in merged


def test_energy_and_duration_take_max():
    survivor = _row(1, 122.0, energy_consumed=18.0, duration=80.0)
    loser = _row(2, 196.34, energy_consumed=20.0, duration=75.0)
    merged = merge_pair(survivor, loser)
    assert merged["energy_consumed"] == 20.0
    assert "duration" not in merged  # survivor already holds the max


def test_odometers_and_start_time_null_filled():
    start = T0 - timedelta(minutes=45)
    survivor = _row(1, 122.0)
    loser = _row(2, 196.34, odometer_start=1000.0, odometer_end=1122.0, start_time=start)
    merged = merge_pair(survivor, loser)
    assert merged["odometer_start"] == 1000.0
    assert merged["odometer_end"] == 1122.0
    assert merged["start_time"] == start


def test_merge_returns_only_changed_fields():
    survivor = _row(1, 122.0, ambient_temp=20.0, energy_consumed=18.0)
    loser = _row(2, 196.34, ambient_temp=20.0, energy_consumed=18.0)
    assert merge_pair(survivor, loser) == {}


# ---------------------------------------------------------------------------
# DB lifecycle
# ---------------------------------------------------------------------------


async def _seed_corrupt_pair(db, device: str, source: str = "ha_fordpass"):
    """Survivor (correct km, scores) + loser (x1.609 twin, temps + odometers)."""
    survivor = await TripFactory.create(
        db,
        device_id=device,
        source_system=source,
        distance=122.0,
        end_time=T0,
        start_time=None,
        energy_consumed=18.0,
        duration=80.0,
        efficiency=6.78,
        driving_score=90.0,
        speed_score=95.0,
        odometer_start=None,
        odometer_end=None,
    )
    loser = await TripFactory.create(
        db,
        device_id=device,
        source_system=source,
        distance=196.34,
        end_time=T0 + timedelta(seconds=45),
        energy_consumed=20.0,
        duration=75.0,
        efficiency=9.82,
        ambient_temp=21.5,
        cabin_temp=22.0,
        odometer_start=1000.0,
        odometer_end=1122.0,
    )
    return survivor, loser


@pytest.mark.db
async def test_consolidation_full_lifecycle(db_session):
    """census -> preview -> apply -> idempotent no-op -> restore."""
    device = "DUP_PAIR_VIN"
    await VehicleFactory.create(db_session, device_id=device)
    survivor, loser = await _seed_corrupt_pair(db_session, device)
    loser_id = loser.id

    op = TripDuplicateConsolidation()
    assert await op.census(db_session) == 1

    preview = await op.preview(db_session)
    (group,) = preview.groups
    assert preview.total == 1
    assert preview.unit == "pairs"
    # The pair is one reviewable unit carrying the evidence that matched it.
    assert group.label == f"Trips #{survivor.id} + #{loser.id}"
    assert group.context["distance ratio"].startswith("1.609")
    assert [d.role for d in group.diffs] == ["keep", "duplicate"]
    # Both rows are recognisable without opening the database.
    assert set(group.diffs[0].identity) == {"start_time", "end_time", "distance"}
    # The deleted twin carries its whole row, not just an id.
    assert group.diffs[1].before["ambient_temp"] is not None

    diffs = preview.diffs
    assert [(d.action, d.row_id) for d in diffs] == [
        ("update", survivor.id),
        ("delete", loser_id),
    ]
    update = diffs[0]
    assert update.after["ambient_temp"] == pytest.approx(21.5)
    assert update.before["ambient_temp"] is None
    assert "distance" not in update.after

    result = await op.apply(db_session)
    assert result.run_id is not None
    assert result.affected == 2
    assert result.snapshot_rows == 2
    backups = (
        (
            await db_session.execute(
                select(RepairBackup).where(RepairBackup.run_id == result.run_id)
            )
        )
        .scalars()
        .all()
    )
    assert {b.row_pk for b in backups} == {survivor.id, loser_id}

    # One merged row remains: correct distance, loser's temps + odometers,
    # survivor's scores, MAX energy.
    remaining = (
        (
            await db_session.execute(
                select(EVTripMetrics).where(EVTripMetrics.device_id == device)
            )
        )
        .scalars()
        .all()
    )
    assert [r.id for r in remaining] == [survivor.id]
    merged = remaining[0]
    assert float(merged.distance) == pytest.approx(122.0)
    assert float(merged.efficiency) == pytest.approx(6.78)
    assert float(merged.ambient_temp) == pytest.approx(21.5)
    assert float(merged.driving_score) == pytest.approx(90.0)
    assert float(merged.energy_consumed) == pytest.approx(20.0)
    assert float(merged.duration) == pytest.approx(80.0)
    assert float(merged.odometer_start) == pytest.approx(1000.0)
    assert float(merged.odometer_end) == pytest.approx(1122.0)

    # Idempotent: the pair is gone.
    second = await op.apply(db_session)
    assert second.run_id is None
    assert second.affected == 0

    # Restore brings the loser back and reverts the survivor's merge.
    assert await restore_run(db_session, result.run_id) == 2
    restored_loser = await db_session.get(EVTripMetrics, loser_id)
    assert restored_loser is not None
    assert float(restored_loser.distance) == pytest.approx(196.34)
    restored_survivor = await db_session.get(EVTripMetrics, survivor.id)
    assert restored_survivor.ambient_temp is None


@pytest.mark.db
async def test_manual_twin_pair_invisible_and_untouched(db_session):
    device = "DUP_MANUAL_VIN"
    await VehicleFactory.create(db_session, device_id=device)
    await _seed_corrupt_pair(db_session, device, source="manual_entry")

    op = TripDuplicateConsolidation()
    assert await op.census(db_session) == 0

    result = await op.apply(db_session)
    assert result.affected == 0

    rows = (
        (
            await db_session.execute(
                select(EVTripMetrics).where(EVTripMetrics.device_id == device)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    assert {float(r.distance) for r in rows} == {122.0, 196.34}
