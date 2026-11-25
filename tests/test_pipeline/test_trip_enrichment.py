"""Regen and coaching scores from a Ford `metrics` dict onto the newest trip."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.factories.trips import TripFactory
from web.services.pipeline.observation import Observation
from web.services.pipeline.trips import enrich_newest_trip
from web.services.sources.ford_metrics.parser import (
    parse_metrics,
    parse_trip_enrichment,
)
from web.util.timestamps import utc

DEVICE = "your_vin_000000001"
END = datetime(2026, 8, 24, 12, 30, tzinfo=UTC)


def _m(value, at: str = "2026-08-24T12:30:00Z") -> dict:
    return {"updateTime": at, "value": value}


def test_regen_and_scores_are_read_with_custom_keys_matched_by_substring():
    row = parse_trip_enrichment({
        "tripXevBatteryRangeRegenerated": _m(4.4, "2026-08-24T12:29:00Z"),
        "tripXevBatteryChargeRegenerated": _m(3.0),
        "customMetrics": {
            "abc123:custom:accumulated-vehicle-speed-cruising-coaching-score": _m(81.0),
            "abc123:custom:accumulated-acceleration-coaching-score": _m(72.0),
            "abc123:custom:accumulated-deceleration-coaching-score": _m(64.0),
            "custom:vehicle-electrical-efficiency": _m(93.0),
            "custom:charge-power-kw": _m(7.2),
        },
    })

    assert row.table == "ev_trip_metrics"
    assert row.recorded_at == END
    assert row.values == {
        "range_regenerated": 4.4, "charge_regenerated": 3.0, "speed_score": 81.0,
        "acceleration_score": 72.0, "deceleration_score": 64.0, "electrical_efficiency": 93.0,
    }


def test_zero_means_unmeasured_so_nothing_is_reported():
    assert parse_trip_enrichment({
        "tripXevBatteryRangeRegenerated": _m(0),
        "tripXevBatteryChargeRegenerated": _m(0.0),
        "customMetrics": {"x:accumulated-acceleration-coaching-score": _m(0)},
    }) is None


def test_no_trip_metrics_means_no_row():
    assert parse_trip_enrichment({"odometer": _m(30000.0)}) is None


@pytest.mark.parametrize(("raw", "kwh"), [(131000, 131.0), (141.2, 141.2), (106.75, 106.75)])
def test_battery_capacity_in_wh_or_kwh_is_stored_as_kwh(raw, kwh):
    (row,) = parse_metrics({"xevBatteryCapacity": _m(raw)})

    assert row.values["hv_battery_capacity"] == pytest.approx(kwh)


def _reading(at: datetime, **values) -> Observation:
    return Observation(DEVICE, "ev_trip_metrics", at, values, "ha_fordpass")


async def _trip(db, **overrides):
    return await TripFactory.create(db, **{
        "device_id": DEVICE, "source_system": "ha_fordpass", "end_time": END,
        "start_time": END - timedelta(minutes=30), "range_regenerated": 2.0,
        "charge_regenerated": None, "distance": 20.0, **overrides,
    })


@pytest.mark.db
async def test_a_reading_taken_as_the_drive_ended_refreshes_regen_and_scores(db_session):
    trip = await _trip(db_session)

    await enrich_newest_trip(db_session, _reading(END + timedelta(seconds=30), range_regenerated=4.4, charge_regenerated=3.0))

    assert (trip.range_regenerated, trip.charge_regenerated, trip.distance) == (4.4, 3.0, 20.0)


@pytest.mark.db
async def test_a_reading_taken_long_after_the_drive_is_not_applied(db_session):
    """It may describe the next drive, whose trip row does not exist yet."""
    trip = await _trip(db_session)

    reading = _reading(END + timedelta(minutes=45), range_regenerated=9.9, charge_regenerated=3.0)
    assert await enrich_newest_trip(db_session, reading) is None
    assert (trip.range_regenerated, trip.charge_regenerated) == (2.0, None)


@pytest.mark.db
async def test_the_trip_ending_at_the_reading_is_chosen_over_an_earlier_one(db_session):
    earlier = await _trip(db_session, end_time=END - timedelta(minutes=45), speed_score=None)
    current = await _trip(db_session, speed_score=None)

    await enrich_newest_trip(db_session, _reading(END, speed_score=81.0))

    assert (earlier.speed_score, current.speed_score) == (None, 81.0)


@pytest.mark.db
@pytest.mark.parametrize("source", ["manual_entry", "csv_import"])
async def test_manual_and_imported_trips_are_never_enriched(db_session, source):
    trip = await _trip(db_session, source_system=source)

    assert await enrich_newest_trip(db_session, _reading(END, range_regenerated=4.4, charge_regenerated=3.0)) is None
    assert (trip.range_regenerated, trip.charge_regenerated) == (2.0, None)


@pytest.mark.db
async def test_a_trip_that_ended_hours_before_the_reading_is_left_alone(db_session):
    trip = await _trip(db_session)

    assert await enrich_newest_trip(db_session, _reading(END + timedelta(hours=5), charge_regenerated=3.0)) is None
    assert trip.charge_regenerated is None


@pytest.mark.db
async def test_a_reading_from_before_the_trip_ended_is_not_applied(db_session):
    """Replaying an old payload must not write onto a later drive."""
    trip = await _trip(db_session)

    assert await enrich_newest_trip(db_session, _reading(END - timedelta(hours=2), charge_regenerated=3.0)) is None
    assert trip.charge_regenerated is None


def test_reading_time_is_utc():
    row = parse_trip_enrichment({"tripXevBatteryChargeRegenerated": _m(3.0, "2026-08-24T14:30:00+02:00")})

    assert utc(row.recorded_at) == END
