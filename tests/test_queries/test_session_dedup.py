"""DB-backed integration tests for CSV session dedup + session-query duplicate flags.

Fills the gap where tests/test_csv_parser.py exercises detect_duplicates/import_rows
against mocks — these tests run the same functions against a real Postgres
database to verify:

- Exact-match (session_id) dedup against an existing row.
- Fuzzy-match (time window + location + ±10% energy) dedup.
- Idempotent re-import of the same CSV-shaped data.
- Query layer currently returns duplicate-flagged sessions (regression guard —
  if a duplicate filter is added later, this test will surface that).
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from db.models.charging_session import EVChargingSession
from tests.factories.sessions import ChargingSessionFactory
from tests.factories.vehicles import VehicleFactory
from web.queries.sessions import query_sessions
from web.services.csv_parser import (
    detect_duplicates,
    import_rows,
    make_session_id,
)

pytestmark = [pytest.mark.query, pytest.mark.db]


def _csv_row(
    *,
    start: datetime,
    location: str,
    energy: float,
    device_id: str = "TEST_VIN_001",
    row_index: int = 0,
) -> dict:
    """Build a transform_rows-shaped dict for dedup/import tests."""
    return {
        "_row_index": row_index,
        "_status": "new",
        "session_id": make_session_id(start, location, energy),
        "session_start_utc": start,
        "location_name": location,
        "energy_kwh": energy,
        "device_id": device_id,
        "source_system": "csv_import",
        "is_complete": True,
    }


async def test_csv_detect_duplicates_exact_session_id_match(db_session):
    """detect_duplicates flags a row whose session_id already exists in DB."""
    vehicle = await VehicleFactory.create(db_session)

    start = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
    location = "Home"
    energy = 25.5
    sid = make_session_id(start, location, energy)

    await ChargingSessionFactory.create(
        db_session,
        session_id=sid,
        device_id=vehicle.device_id,
        session_start_utc=start,
        location_name=location,
        energy_kwh=energy,
        source_system="csv_import",
    )

    rows = [_csv_row(start=start, location=location, energy=energy,
                     device_id=vehicle.device_id)]

    await detect_duplicates(rows, db_session)

    assert rows[0]["_status"] == "duplicate"


async def test_csv_detect_duplicates_fuzzy_match_within_window(db_session):
    """Within ±1h window + same location + energy within 10% -> fuzzy_duplicate."""
    vehicle = await VehicleFactory.create(db_session)
    start = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)

    await ChargingSessionFactory.create(
        db_session,
        device_id=vehicle.device_id,
        session_start_utc=start,
        location_name="Work",
        energy_kwh=20.0,
    )

    # CSV row: 30 min later, energy within 10%, different session_id
    rows = [_csv_row(
        start=start.replace(minute=30),
        location="Work",
        energy=20.8,
        device_id=vehicle.device_id,
    )]

    await detect_duplicates(rows, db_session)
    assert rows[0]["_status"] == "fuzzy_duplicate"
    assert rows[0]["_matched_id"] is not None


async def test_csv_detect_duplicates_distinct_row_stays_new(db_session):
    """Row with no DB match stays _status='new'."""
    vehicle = await VehicleFactory.create(db_session)

    rows = [_csv_row(
        start=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
        location="Destination Charger",
        energy=15.0,
        device_id=vehicle.device_id,
    )]

    await detect_duplicates(rows, db_session)
    assert rows[0]["_status"] == "new"


async def test_csv_import_is_idempotent(db_session):
    """Importing the same row twice results in exactly one DB row.

    First pass: detect -> new -> import adds it.
    Second pass: detect -> duplicate -> import (with default skip) does not add.
    """
    vehicle = await VehicleFactory.create(db_session)
    start = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)

    row = _csv_row(start=start, location="Home", energy=12.3,
                   device_id=vehicle.device_id)

    # First import
    await detect_duplicates([row], db_session)
    assert row["_status"] == "new"
    result1 = await import_rows([row], {0}, {}, db_session)
    assert result1["added"] == 1
    await db_session.flush()

    # Second attempt: fresh row (simulate re-parsing same CSV)
    row2 = _csv_row(start=start, location="Home", energy=12.3,
                    device_id=vehicle.device_id)
    await detect_duplicates([row2], db_session)
    assert row2["_status"] == "duplicate"

    # Import with no explicit duplicate action -> defaults to "skip"
    result2 = await import_rows([row2], {0}, {}, db_session)
    assert result2["added"] == 0
    assert result2["skipped"] == 1

    # Verify only one row on disk
    count = (await db_session.execute(
        select(EVChargingSession).where(
            EVChargingSession.device_id == vehicle.device_id,
            EVChargingSession.session_start_utc == start,
        )
    )).scalars().all()
    assert len(count) == 1


async def test_query_sessions_currently_includes_duplicate_flagged_rows(db_session):
    """Sessions flagged `duplicate_of_id` are STILL returned by query_sessions.

    This documents current behavior (no default duplicate filter). If a future
    phase adds `hide_duplicates=True`, this test should flip — the intent is
    to make that behavior change visible rather than silent.
    """
    vehicle = await VehicleFactory.create(db_session)

    canonical = await ChargingSessionFactory.create(
        db_session,
        device_id=vehicle.device_id,
        energy_kwh=25.0,
        source_system="manual",
    )
    await ChargingSessionFactory.create(
        db_session,
        device_id=vehicle.device_id,
        energy_kwh=25.0,
        source_system="home_assistant",
        duplicate_of_id=canonical.id,
        needs_review=True,
        review_type="duplicate",
    )

    rows, total, _summary = await query_sessions(
        db_session, device_id=vehicle.device_id
    )
    assert total == 2, (
        "query_sessions has no duplicate filter today — if this flips to 1, "
        "update the test and ensure callers pass a hide_duplicates flag."
    )
