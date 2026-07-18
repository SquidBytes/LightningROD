"""Unit tests for the hide-short-trips filter helper."""

import pytest
from sqlalchemy import select

from db.models.trip_metrics import EVTripMetrics
from tests.factories.trips import TripFactory
from web.queries.trips import build_short_trip_filter, get_trip_hide_settings

HIDE = {"enabled": True, "min_duration_s": 300.0, "min_distance_km": 4.8}


def test_disabled_returns_none():
    assert build_short_trip_filter({"enabled": False}) is None
    assert build_short_trip_filter({"enabled": False}, hidden=True) is None


@pytest.mark.db
async def test_defaults_when_settings_absent(db_session):
    hide = await get_trip_hide_settings(db_session)
    assert hide == {
        "enabled": False,
        "min_duration_s": 300.0,
        "min_distance_km": 4.8,
    }


async def _visible_distances(db_session, hidden=False) -> set:
    clause = build_short_trip_filter(HIDE, hidden=hidden)
    stmt = select(EVTripMetrics.distance).where(clause)
    rows = (await db_session.execute(stmt)).scalars().all()
    return {float(d) if d is not None else None for d in rows}


@pytest.mark.db
async def test_filter_hides_and_keeps_correct_trips(db_session):
    """NULL/NULL and under-both trips hide; clearing either threshold keeps."""
    # Hidden: key-on row with no data at all
    await TripFactory.create(db_session, duration=None, distance=None)
    # Hidden: under both thresholds
    await TripFactory.create(db_session, duration=120.0, distance=1.0)
    # Visible: clears duration threshold only
    await TripFactory.create(db_session, duration=600.0, distance=2.0)
    # Visible: clears distance threshold only (duration NULL)
    await TripFactory.create(db_session, duration=None, distance=10.0)
    # Visible: exactly at duration threshold (< is strict)
    await TripFactory.create(db_session, duration=300.0, distance=3.0)

    visible = await _visible_distances(db_session)
    assert visible == {2.0, 10.0, 3.0}

    hidden = await _visible_distances(db_session, hidden=True)
    assert hidden == {None, 1.0}
