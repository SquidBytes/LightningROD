"""JSON / Uuid / DateTime round-trip per dialect (RESEARCH Pitfalls 3, 4)."""
import uuid
from datetime import UTC, datetime

import pytest


@pytest.mark.db
async def test_json_storage_roundtrip(db_session):
    """JSONStorage stores a dict on SQLite + read-back matches."""
    from db.models.vehicle_status import EVVehicleStatus

    payload = {"locked": True, "doors": ["LF", "RR"], "nested": {"a": 1}}
    obj = EVVehicleStatus(
        device_id="ROUNDTRIP_TEST_DEVICE",
        recorded_at=datetime.now(UTC),
        door_lock_status=payload,
    )
    db_session.add(obj)
    await db_session.flush()
    await db_session.refresh(obj)
    assert obj.door_lock_status == payload


@pytest.mark.db
async def test_uuid_roundtrip(db_session):
    """sa.Uuid round-trips Python uuid.UUID on SQLite (hex-without-dashes form)."""
    from db.models.charging_session import EVChargingSession

    sid = uuid.uuid4()
    sess = EVChargingSession(
        session_id=sid,
        device_id="UUID_RT_TEST",
        is_complete=False,
    )
    db_session.add(sess)
    await db_session.flush()
    await db_session.refresh(sess)
    assert sess.session_id == sid
    assert isinstance(sess.session_id, uuid.UUID)


@pytest.mark.db
async def test_datetime_tz_roundtrip(db_session):
    """sa.DateTime(timezone=True) round-trips a tz-aware datetime on SQLite.

    Per RESEARCH Pitfall 4, SQLite stores naive ISO strings — we assert match
    at the timestamp-component level so a naive-vs-aware return is surfaced as
    a known concern rather than a silent data loss.
    """
    from db.models.charging_session import EVChargingSession

    now = datetime.now(UTC)
    sess = EVChargingSession(
        session_id=uuid.uuid4(),
        device_id="TZ_RT_TEST",
        session_start_utc=now,
        is_complete=False,
    )
    db_session.add(sess)
    await db_session.flush()
    await db_session.refresh(sess)
    # Component-level comparison so the naive-vs-aware return is documented
    # rather than masked. If/when SQLAlchemy starts returning tz-aware on
    # SQLite, tighten this to ``sess.session_start_utc == now``.
    assert sess.session_start_utc.replace(tzinfo=None) == now.replace(tzinfo=None)
