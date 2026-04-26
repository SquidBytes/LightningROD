"""Sessions query layer validation tests.

Tests paginated session queries, filtering, and summary aggregation.
"""

from datetime import UTC, datetime, timedelta

import pytest

from db.models.charging_session import EVChargingSession
from web.queries.sessions import query_sessions

pytestmark = [pytest.mark.query, pytest.mark.db]


async def _create_sessions(db, count=15, device_id="TEST_VIN_SESS"):
    """Helper: create N sessions with sequential dates and known energy."""
    sessions = []
    base = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
    for i in range(count):
        s = EVChargingSession(
            device_id=device_id,
            energy_kwh=10.0 + i,
            charge_type="AC" if i % 2 == 0 else "DC",
            session_start_utc=base + timedelta(days=i),
            is_complete=True,
            source_system="test",
        )
        sessions.append(s)
    db.add_all(sessions)
    await db.flush()
    return sessions


async def test_sessions_paginated(db_session):
    """Create 15 sessions -> verify pagination returns correct page size."""
    await _create_sessions(db_session, count=15)

    sessions, total, summary = await query_sessions(
        db_session, page=1, per_page=25, date_preset="all"
    )

    assert total == 15
    assert len(sessions) == 15
    assert summary["count"] == 15


async def test_sessions_page_size_limit(db_session):
    """Verify per_page limits work correctly."""
    await _create_sessions(db_session, count=30)

    sessions_p1, total, _ = await query_sessions(
        db_session, page=1, per_page=25, date_preset="all"
    )
    sessions_p2, _, _ = await query_sessions(
        db_session, page=2, per_page=25, date_preset="all"
    )

    assert total == 30
    assert len(sessions_p1) == 25
    assert len(sessions_p2) == 5


async def test_sessions_filtered_by_device(db_session):
    """Verify device_id filtering returns only matching sessions."""
    await _create_sessions(db_session, count=5, device_id="VIN_A")
    await _create_sessions(db_session, count=3, device_id="VIN_B")

    sessions_a, total_a, _ = await query_sessions(
        db_session, page=1, per_page=25, date_preset="all", device_id="VIN_A"
    )
    sessions_b, total_b, _ = await query_sessions(
        db_session, page=1, per_page=25, date_preset="all", device_id="VIN_B"
    )

    assert total_a == 5
    assert total_b == 3
    assert all(s.device_id == "VIN_A" for s in sessions_a)
    assert all(s.device_id == "VIN_B" for s in sessions_b)


async def test_sessions_empty(db_session):
    """No sessions -> returns empty list and zero totals."""
    sessions, total, summary = await query_sessions(
        db_session, page=1, per_page=25, date_preset="all"
    )

    assert total == 0
    assert len(sessions) == 0
    assert summary["count"] == 0
    assert summary["total_kwh"] == 0.0
