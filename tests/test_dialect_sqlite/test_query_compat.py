"""date_trunc_compat + portable_insert exercised on SQLite."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from db.dialect import date_trunc_compat
from db.portable_insert import portable_insert


@pytest.mark.db
async def test_date_trunc_compat_hour(db_session):
    """Single-hour bucket via _STRFTIME_FORMATS happy path."""
    from db.models.battery_status import EVBatteryStatus

    base = datetime(2026, 1, 1, 12, 30, tzinfo=UTC)
    rows = [
        EVBatteryStatus(
            device_id="DTC_HOUR",
            recorded_at=base + timedelta(minutes=i * 5),
            hv_battery_soc=50 + i,
        )
        for i in range(6)
    ]
    db_session.add_all(rows)
    await db_session.flush()

    bucket = date_trunc_compat(
        "hour", EVBatteryStatus.recorded_at, dialect=db_session.bind.dialect
    )
    stmt = (
        select(bucket.label("h"))
        .where(EVBatteryStatus.device_id == "DTC_HOUR")
        .group_by(bucket)
    )
    result = await db_session.execute(stmt)
    buckets = [r.h for r in result.all()]
    assert len(buckets) == 1, f"expected 1 hour bucket, got {len(buckets)}: {buckets}"


@pytest.mark.db
async def test_date_trunc_compat_multi_hour(db_session):
    """Multi-hour bucket via integer-arithmetic path."""
    from db.models.battery_status import EVBatteryStatus

    base = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    rows = [
        EVBatteryStatus(
            device_id="DTC_2H",
            recorded_at=base + timedelta(hours=i),
            hv_battery_soc=50 + i,
        )
        for i in range(6)  # 6 hours = 3 buckets of 2 hours
    ]
    db_session.add_all(rows)
    await db_session.flush()

    bucket = date_trunc_compat(
        "2 hours", EVBatteryStatus.recorded_at, dialect=db_session.bind.dialect
    )
    stmt = (
        select(bucket.label("b"))
        .where(EVBatteryStatus.device_id == "DTC_2H")
        .group_by(bucket)
    )
    result = await db_session.execute(stmt)
    buckets = [r.b for r in result.all()]
    assert len(buckets) == 3, f"expected 3 two-hour buckets, got {len(buckets)}"


@pytest.mark.db
async def test_portable_insert_upsert(db_session):
    """portable_insert.on_conflict_do_update works on SQLite."""
    from db.models.reference import AppSettings

    stmt = portable_insert(AppSettings, dialect=db_session.bind.dialect).values(
        key="test_key_pi", value="v1"
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["key"],
        set_={"value": "v2"},
    )
    await db_session.execute(stmt)
    await db_session.flush()

    # Run again with a different value — should update, not insert.
    stmt2 = portable_insert(AppSettings, dialect=db_session.bind.dialect).values(
        key="test_key_pi", value="v3"
    )
    stmt2 = stmt2.on_conflict_do_update(
        index_elements=["key"],
        set_={"value": "v3"},
    )
    await db_session.execute(stmt2)
    await db_session.flush()

    found = (
        await db_session.execute(
            select(AppSettings).where(AppSettings.key == "test_key_pi")
        )
    ).scalar_one()
    assert found.value == "v3"
