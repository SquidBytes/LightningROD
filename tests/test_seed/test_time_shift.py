"""Tests for scripts.seed.base time-shift helpers.

All tests use mocking — no real DB required.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from scripts.seed.base import (
    compute_global_offset,
    shift_datetime,
)


def _mock_result(scalar_value):
    """Return a MagicMock that mimics a SQLAlchemy scalar result."""
    r = MagicMock()
    r.scalar.return_value = scalar_value
    return r


def _make_db(*scalar_values):
    """Return an AsyncMock AsyncSession whose execute() returns scalars in order."""
    mock_db = AsyncMock(spec=AsyncSession)
    mock_db.execute = AsyncMock(side_effect=[_mock_result(v) for v in scalar_values])
    return mock_db


@pytest.mark.asyncio
async def test_offset_with_no_data():
    """Empty source list → offset=0, max_observed=None."""
    mock_db = AsyncMock(spec=AsyncSession)
    fixed_now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    result = await compute_global_offset(mock_db, sources=[], now=fixed_now)

    assert result.offset == timedelta(0)
    assert result.max_observed is None
    assert result.has_data is False
    assert result.now == fixed_now


@pytest.mark.asyncio
async def test_offset_uses_latest_across_sources():
    """Offset = now − latest timestamp across all sources."""
    dt_older = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    dt_newer = datetime(2026, 1, 1, 11, 0, 0, tzinfo=UTC)
    fixed_now = datetime(2026, 1, 1, 13, 0, 0, tzinfo=UTC)

    model_a = MagicMock()
    model_b = MagicMock()
    sources = [(model_a, "ts_col"), (model_b, "ts_col")]

    mock_db = _make_db(dt_older, dt_newer)

    result = await compute_global_offset(mock_db, sources=sources, now=fixed_now)

    assert result.offset == fixed_now - dt_newer
    assert result.max_observed == dt_newer
    assert result.has_data is True


@pytest.mark.asyncio
async def test_offset_naive_datetime_treated_as_utc():
    """Naive datetime returned by DB is treated as UTC — no timezone error."""
    naive_dt = datetime(2026, 1, 1, 10, 0, 0)
    fixed_now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    model_a = MagicMock()
    sources = [(model_a, "ts_col")]
    mock_db = _make_db(naive_dt)

    result = await compute_global_offset(mock_db, sources=sources, now=fixed_now)

    assert result.offset == timedelta(hours=2)
    assert result.max_observed is not None
    assert result.max_observed.tzinfo is not None


def test_shift_datetime_basic():
    """shift_datetime adds offset, handles None, normalizes naive datetimes."""
    offset = timedelta(hours=3)
    aware_dt = datetime(2026, 1, 1, 9, 0, 0, tzinfo=UTC)
    naive_dt = datetime(2026, 1, 1, 9, 0, 0)

    assert shift_datetime(aware_dt, offset) == datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    assert shift_datetime(None, offset) is None

    result_naive = shift_datetime(naive_dt, offset)
    assert result_naive == datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    assert result_naive.tzinfo is not None
