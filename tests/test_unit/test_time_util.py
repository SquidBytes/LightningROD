"""web.util.time: parse_iso, utc, to_utc."""

import logging
from datetime import UTC, datetime, timedelta, timezone

import pytest

from web.util.time import parse_iso, to_utc, utc

pytestmark = pytest.mark.unit


def test_parse_iso_z_suffix_is_aware():
    result = parse_iso("2025-06-15T12:00:00Z")
    assert result == datetime(2025, 6, 15, 12, tzinfo=UTC)


def test_parse_iso_offset_kept():
    result = parse_iso("2025-06-15T12:00:00+02:00")
    assert result is not None
    assert result.utcoffset() == timedelta(hours=2)


def test_parse_iso_naive_stays_naive():
    result = parse_iso("2025-06-15T12:00:00")
    assert result is not None
    assert result.tzinfo is None


def test_parse_iso_empty_is_silent(caplog):
    with caplog.at_level(logging.WARNING, logger="lightningrod.util.time"):
        assert parse_iso(None) is None
        assert parse_iso("") is None
    assert caplog.records == []


def test_parse_iso_malformed_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="lightningrod.util.time"):
        assert parse_iso("not-a-date") is None
        assert parse_iso(12345) is None
    assert [r.levelname for r in caplog.records] == ["WARNING", "WARNING"]
    assert "not-a-date" in caplog.records[0].getMessage()


def test_parse_iso_passes_datetime_through():
    dt = datetime(2025, 6, 15, tzinfo=UTC)
    assert parse_iso(dt) is dt


def test_utc_stamps_naive_only():
    naive = datetime(2025, 6, 15, 12)
    plus2 = datetime(2025, 6, 15, 12, tzinfo=timezone(timedelta(hours=2)))
    assert utc(naive) == datetime(2025, 6, 15, 12, tzinfo=UTC)
    assert utc(plus2) is plus2
    assert utc(None) is None
    assert utc("x") == "x"


def test_to_utc_rebases_aware_and_stamps_naive():
    naive = datetime(2025, 6, 15, 12)
    plus2 = datetime(2025, 6, 15, 12, tzinfo=timezone(timedelta(hours=2)))
    assert to_utc(naive) == datetime(2025, 6, 15, 12, tzinfo=UTC)
    assert to_utc(plus2) == datetime(2025, 6, 15, 10, tzinfo=UTC)
    assert to_utc(None) is None
