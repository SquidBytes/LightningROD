"""web.util.timestamps: parse_iso, utc, from_local."""

import logging
from datetime import UTC, datetime, timedelta, timezone

import pytest

from web.util.timestamps import from_local, parse_iso, utc

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


def test_parse_iso_silent_by_default(caplog):
    with caplog.at_level(logging.WARNING):
        assert parse_iso(None) is None
        assert parse_iso("") is None
        assert parse_iso("not-a-date") is None
    assert caplog.records == []


def test_parse_iso_logs_malformed_to_given_logger(caplog):
    log = logging.getLogger("test.parse_iso")
    with caplog.at_level(logging.WARNING, logger="test.parse_iso"):
        assert parse_iso(None, logger=log) is None
        assert parse_iso("not-a-date", logger=log) is None
        assert parse_iso(12345, logger=log) is None
    assert [r.levelname for r in caplog.records] == ["WARNING", "WARNING"]
    assert "not-a-date" in caplog.records[0].getMessage()


def test_parse_iso_passes_datetime_through():
    dt = datetime(2025, 6, 15, tzinfo=UTC)
    assert parse_iso(dt) is dt


def test_utc_stamps_naive_and_rebases_aware():
    naive = datetime(2025, 6, 15, 12)
    plus2 = datetime(2025, 6, 15, 12, tzinfo=timezone(timedelta(hours=2)))
    assert utc(naive) == datetime(2025, 6, 15, 12, tzinfo=UTC)
    assert utc(plus2) == datetime(2025, 6, 15, 10, tzinfo=UTC)
    assert utc(plus2).tzinfo is UTC
    assert utc(None) is None
    assert utc("x") == "x"


def test_from_local_reads_naive_in_zone_and_rebases_aware():
    plus2 = timezone(timedelta(hours=2))
    assert from_local(datetime(2025, 6, 15, 12), plus2) == datetime(2025, 6, 15, 10, tzinfo=UTC)
    minus5 = datetime(2025, 6, 15, 12, tzinfo=timezone(timedelta(hours=-5)))
    assert from_local(minus5, plus2) == datetime(2025, 6, 15, 17, tzinfo=UTC)
