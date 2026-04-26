"""Trip query layer validation tests.

Tests trip listing, pagination, efficiency trends, and summary aggregation.
"""

import pytest

from web.queries.trips import query_efficiency_trend, query_trips

pytestmark = [pytest.mark.query, pytest.mark.db]


async def test_trip_list_paginated(trip_scenario):
    """trip_scenario -> query_trips -> verify correct count and pagination."""
    db = trip_scenario["db"]
    exp = trip_scenario["expected"]

    trips, total, summary = await query_trips(
        db, page=1, per_page=25, date_preset="all"
    )

    assert total == exp["count"]
    assert len(trips) == exp["count"]
    assert summary["count"] == exp["count"]


async def test_trip_summary_totals(trip_scenario):
    """Verify trip summary aggregation: total_distance and avg_efficiency."""
    db = trip_scenario["db"]
    exp = trip_scenario["expected"]

    trips, total, summary = await query_trips(
        db, page=1, per_page=25, date_preset="all"
    )

    assert summary["total_distance"] == pytest.approx(exp["total_distance"], abs=0.1)
    assert summary["avg_efficiency"] == pytest.approx(exp["avg_efficiency"], abs=0.01)


async def test_trip_efficiency_trend(trip_scenario):
    """Verify efficiency trend data returns correct number of data points."""
    db = trip_scenario["db"]

    data = await query_efficiency_trend(db, time_range="all")

    assert len(data) == trip_scenario["expected"]["count"]
    # All entries should have date, efficiency, distance
    for entry in data:
        assert "date" in entry
        assert "efficiency" in entry
        assert "distance" in entry
        assert entry["efficiency"] > 0


async def test_trip_sorting(trip_scenario):
    """Verify trips can be sorted by distance descending."""
    db = trip_scenario["db"]

    trips, total, _ = await query_trips(
        db, page=1, per_page=25, date_preset="all",
        sort_by="distance", sort_dir="desc",
    )

    distances = [float(t.distance) for t in trips if t.distance is not None]
    assert distances == sorted(distances, reverse=True)


async def test_trips_empty(db_session):
    """No trips -> returns empty list and zero totals."""
    trips, total, summary = await query_trips(
        db_session, page=1, per_page=25, date_preset="all"
    )

    assert total == 0
    assert len(trips) == 0
    assert summary["count"] == 0
    assert summary["total_distance"] == 0.0
