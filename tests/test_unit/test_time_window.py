"""Unit tests for the shared preset/custom date-window resolver."""

from datetime import UTC, datetime, timedelta

import pytest

from db.models.charging_session import EVChargingSession
from web.queries.time_window import resolve_time_window, window_clause

pytestmark = pytest.mark.unit


class TestResolveTimeWindow:
    def test_preset_returns_cutoff_only(self):
        for preset, days in (("7d", 7), ("30d", 30), ("90d", 90), ("1y", 365)):
            start, end = resolve_time_window(preset)
            assert end is None
            expected = datetime.now(UTC) - timedelta(days=days)
            assert abs((start - expected).total_seconds()) < 5

    def test_ytd_starts_jan_first(self):
        start, end = resolve_time_window("ytd")
        assert start == datetime(datetime.now(UTC).year, 1, 1, tzinfo=UTC)
        assert end is None

    def test_all_and_empty_are_unbounded(self):
        assert resolve_time_window("all") == (None, None)
        assert resolve_time_window("") == (None, None)
        assert resolve_time_window(None) == (None, None)

    def test_custom_window_both_bounds(self):
        start, end = resolve_time_window("all", "2026-01-05", "2026-01-10")
        assert start == datetime(2026, 1, 5, tzinfo=UTC)
        # end is exclusive: date_to + 1 day (inclusive end-of-day)
        assert end == datetime(2026, 1, 11, tzinfo=UTC)

    def test_custom_window_single_bound(self):
        start, end = resolve_time_window("all", date_from="2026-01-05")
        assert start == datetime(2026, 1, 5, tzinfo=UTC)
        assert end is None

        start, end = resolve_time_window("all", date_to="2026-01-10")
        assert start is None
        assert end == datetime(2026, 1, 11, tzinfo=UTC)

    def test_preset_wins_over_custom_dates(self):
        start, end = resolve_time_window("7d", "2026-01-05", "2026-01-10")
        assert end is None
        expected = datetime.now(UTC) - timedelta(days=7)
        assert abs((start - expected).total_seconds()) < 5

    def test_invalid_dates_ignored(self):
        assert resolve_time_window("all", "garbage", "also-garbage") == (None, None)
        start, end = resolve_time_window("all", "garbage", "2026-01-10")
        assert start is None
        assert end == datetime(2026, 1, 11, tzinfo=UTC)

    def test_unknown_preset_is_unbounded(self):
        assert resolve_time_window("14d") == (None, None)


class TestCentralBuilders:
    """The per-model builders all emit a bounded window for custom dates."""

    def test_custom_window_has_both_bounds(self):
        from web.queries.battery import build_battery_time_filter
        from web.queries.costs import build_time_filter
        from web.queries.energy import build_time_filter_trip
        from web.queries.trips import build_trip_time_filter

        for builder in (
            build_time_filter,
            build_time_filter_trip,
            build_trip_time_filter,
            build_battery_time_filter,
        ):
            clause = builder("all", "2026-01-05", "2026-01-10")
            text = str(clause)
            assert ">=" in text and " < " in text and " AND " in text
            assert builder("all") is None


class TestWindowClause:
    def test_unbounded_returns_none(self):
        col = EVChargingSession.session_start_utc
        assert window_clause(col, None, None) is None

    def test_lower_bound_only(self):
        col = EVChargingSession.session_start_utc
        clause = window_clause(col, datetime(2026, 1, 5, tzinfo=UTC), None)
        assert ">=" in str(clause)
        assert " < " not in str(clause)

    def test_upper_bound_only(self):
        col = EVChargingSession.session_start_utc
        clause = window_clause(col, None, datetime(2026, 1, 11, tzinfo=UTC))
        assert " < " in str(clause)
        assert ">=" not in str(clause)

    def test_both_bounds_anded(self):
        col = EVChargingSession.session_start_utc
        clause = window_clause(
            col,
            datetime(2026, 1, 5, tzinfo=UTC),
            datetime(2026, 1, 11, tzinfo=UTC),
        )
        text = str(clause)
        assert ">=" in text
        assert " < " in text
        assert " AND " in text
