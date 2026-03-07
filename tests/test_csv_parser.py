"""Tests for csv_parser transform_rows and detect_duplicates."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# transform_rows tests
# ---------------------------------------------------------------------------


def test_transform_rows_basic_mapping():
    """transform_rows applies column_mapping and parses values."""
    from web.services.csv_parser import transform_rows

    raw_rows = [
        {
            "start_time": "2026-01-15T10:30:00+00:00",
            "energy_consumed_kwh": "25.5",
            "location_name": "Home",
            "cost_total": "3.50",
        }
    ]
    mapping = {
        "start_time": "session_start_utc",
        "energy_consumed_kwh": "energy_kwh",
        "location_name": "location_name",
        "cost_total": "cost",
    }
    result = transform_rows(raw_rows, mapping)

    assert len(result) == 1
    row = result[0]
    assert row["energy_kwh"] == 25.5
    assert row["location_name"] == "Home"
    assert row["cost"] == 3.50
    assert row["session_start_utc"] is not None
    assert row["_status"] == "new"
    assert row["source_system"] == "csv_import"
    assert row["is_complete"] is True
    assert row["session_id"] is not None
    assert row["_row_index"] == 0


def test_transform_rows_skip_empty_mapping():
    """Columns mapped to empty string are skipped."""
    from web.services.csv_parser import transform_rows

    raw_rows = [
        {
            "start_time": "2026-01-15T10:30:00+00:00",
            "energy_consumed_kwh": "25.5",
            "notes": "some note",
        }
    ]
    mapping = {
        "start_time": "session_start_utc",
        "energy_consumed_kwh": "energy_kwh",
        "notes": "",  # skip
    }
    result = transform_rows(raw_rows, mapping)

    assert "notes" not in result[0]
    assert "energy_kwh" in result[0]


def test_transform_rows_error_status_when_missing_core_fields():
    """Row with no energy and no start time gets _status='error'."""
    from web.services.csv_parser import transform_rows

    raw_rows = [
        {
            "start_time": "",
            "energy_consumed_kwh": "",
            "location_name": "Work",
            "cost_total": "",
        }
    ]
    mapping = {
        "start_time": "session_start_utc",
        "energy_consumed_kwh": "energy_kwh",
        "location_name": "location_name",
        "cost_total": "cost",
    }
    result = transform_rows(raw_rows, mapping)

    assert len(result) == 1
    assert result[0]["_status"] == "error"
    assert "_error" in result[0]


def test_transform_rows_generates_session_id():
    """transform_rows generates deterministic session_id via make_session_id."""
    from web.services.csv_parser import transform_rows, make_session_id
    from datetime import datetime, timezone

    raw_rows = [
        {
            "start_time": "2026-01-15T10:30:00+00:00",
            "energy_consumed_kwh": "25.5",
            "location_name": "Home",
        }
    ]
    mapping = {
        "start_time": "session_start_utc",
        "energy_consumed_kwh": "energy_kwh",
        "location_name": "location_name",
    }
    result = transform_rows(raw_rows, mapping)

    row = result[0]
    # The session_id should be deterministic — same as calling make_session_id
    expected_id = make_session_id(
        row["session_start_utc"],
        row.get("location_name"),
        row.get("energy_kwh"),
    )
    assert str(row["session_id"]) == str(expected_id)


def test_transform_rows_sets_source_system():
    """transform_rows sets source_system to 'csv_import'."""
    from web.services.csv_parser import transform_rows

    raw_rows = [{"start_time": "2026-01-15T10:30:00+00:00", "energy_consumed_kwh": "10.0"}]
    mapping = {"start_time": "session_start_utc", "energy_consumed_kwh": "energy_kwh"}
    result = transform_rows(raw_rows, mapping)

    assert result[0]["source_system"] == "csv_import"


def test_transform_rows_is_complete_true():
    """transform_rows sets is_complete=True for all rows."""
    from web.services.csv_parser import transform_rows

    raw_rows = [{"start_time": "2026-01-15T10:30:00+00:00", "energy_consumed_kwh": "10.0"}]
    mapping = {"start_time": "session_start_utc", "energy_consumed_kwh": "energy_kwh"}
    result = transform_rows(raw_rows, mapping)

    assert result[0]["is_complete"] is True


def test_transform_rows_multiple_rows_indexed():
    """transform_rows assigns correct _row_index to each row."""
    from web.services.csv_parser import transform_rows

    raw_rows = [
        {"start_time": "2026-01-15T10:30:00+00:00", "energy_consumed_kwh": "10.0"},
        {"start_time": "2026-01-16T10:30:00+00:00", "energy_consumed_kwh": "20.0"},
    ]
    mapping = {"start_time": "session_start_utc", "energy_consumed_kwh": "energy_kwh"}
    result = transform_rows(raw_rows, mapping)

    assert result[0]["_row_index"] == 0
    assert result[1]["_row_index"] == 1


def test_transform_rows_duration_minutes_converts_to_seconds():
    """duration_minutes mapped to charge_duration_seconds is converted (* 60)."""
    from web.services.csv_parser import transform_rows

    raw_rows = [
        {
            "start_time": "2026-01-15T10:30:00+00:00",
            "energy_consumed_kwh": "25.5",
            "duration_minutes": "90",
        }
    ]
    mapping = {
        "start_time": "session_start_utc",
        "energy_consumed_kwh": "energy_kwh",
        "duration_minutes": "charge_duration_seconds",
    }
    result = transform_rows(raw_rows, mapping)

    assert result[0]["charge_duration_seconds"] == 90 * 60


# ---------------------------------------------------------------------------
# detect_duplicates tests (async, uses mocked DB session)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_duplicates_marks_exact_match():
    """detect_duplicates marks rows whose session_id exists in DB as 'duplicate'."""
    from web.services.csv_parser import detect_duplicates
    import uuid

    existing_session_id = uuid.uuid4()

    rows = [
        {
            "_status": "new",
            "_row_index": 0,
            "session_id": existing_session_id,
            "session_start_utc": None,
            "location_name": "Home",
            "energy_kwh": 25.5,
        }
    ]

    # Mock DB session
    mock_result = MagicMock()
    mock_result.fetchall.return_value = [(existing_session_id,)]

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    result = await detect_duplicates(rows, mock_db)

    assert result[0]["_status"] == "duplicate"


@pytest.mark.asyncio
async def test_detect_duplicates_new_row_stays_new():
    """detect_duplicates leaves rows without DB match as 'new'."""
    from web.services.csv_parser import detect_duplicates
    import uuid

    rows = [
        {
            "_status": "new",
            "_row_index": 0,
            "session_id": uuid.uuid4(),
            "session_start_utc": None,
            "location_name": "Home",
            "energy_kwh": 25.5,
        }
    ]

    mock_result = MagicMock()
    mock_result.fetchall.return_value = []  # no matches

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    result = await detect_duplicates(rows, mock_db)

    assert result[0]["_status"] == "new"


@pytest.mark.asyncio
async def test_detect_duplicates_error_rows_unchanged():
    """detect_duplicates does not alter 'error' status rows."""
    from web.services.csv_parser import detect_duplicates
    import uuid

    rows = [
        {
            "_status": "error",
            "_row_index": 0,
            "session_id": None,
            "session_start_utc": None,
            "location_name": None,
            "energy_kwh": None,
        }
    ]

    mock_result = MagicMock()
    mock_result.fetchall.return_value = []

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    result = await detect_duplicates(rows, mock_db)

    assert result[0]["_status"] == "error"


# ---------------------------------------------------------------------------
# import_rows tests (async, uses mocked DB session)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_rows_skip_action_does_not_insert():
    """import_rows with action 'skip' for a row does not insert and increments skipped."""
    from web.services.csv_parser import import_rows
    import uuid

    rows = [
        {
            "_row_index": 0,
            "_status": "duplicate",
            "session_id": uuid.uuid4(),
            "energy_kwh": 25.5,
            "source_system": "csv_import",
            "is_complete": True,
            "device_id": "TEST_DEVICE",
        }
    ]
    selected_indices = {0}
    duplicate_actions = {0: "skip"}

    mock_db = AsyncMock()
    mock_db.begin_nested = AsyncMock()

    result = await import_rows(rows, selected_indices, duplicate_actions, mock_db)

    assert result["skipped"] == 1
    assert result["added"] == 0
    assert result["updated"] == 0
    assert result["failed"] == 0


@pytest.mark.asyncio
async def test_import_rows_unselected_row_is_skipped():
    """import_rows skips rows not in selected_indices and increments skipped."""
    from web.services.csv_parser import import_rows
    import uuid

    rows = [
        {
            "_row_index": 0,
            "_status": "new",
            "session_id": uuid.uuid4(),
            "energy_kwh": 25.5,
            "source_system": "csv_import",
            "is_complete": True,
            "device_id": "TEST_DEVICE",
        }
    ]
    selected_indices = set()  # nothing selected
    duplicate_actions = {}

    mock_db = AsyncMock()

    result = await import_rows(rows, selected_indices, duplicate_actions, mock_db)

    assert result["skipped"] == 1
    assert result["added"] == 0


@pytest.mark.asyncio
async def test_import_rows_error_row_counted_as_failed():
    """import_rows counts rows with _status='error' as failed."""
    from web.services.csv_parser import import_rows
    import uuid

    rows = [
        {
            "_row_index": 0,
            "_status": "error",
            "_error": "Missing energy and start time",
            "session_id": None,
            "energy_kwh": None,
            "source_system": "csv_import",
            "is_complete": True,
            "device_id": "TEST_DEVICE",
        }
    ]
    selected_indices = {0}
    duplicate_actions = {}

    mock_db = AsyncMock()

    result = await import_rows(rows, selected_indices, duplicate_actions, mock_db)

    assert result["failed"] == 1
    assert result["added"] == 0


@pytest.mark.asyncio
async def test_import_rows_insert_new_row():
    """import_rows with action 'insert' adds an EVChargingSession and increments added."""
    from web.services.csv_parser import import_rows
    import uuid

    rows = [
        {
            "_row_index": 0,
            "_status": "new",
            "session_id": uuid.uuid4(),
            "energy_kwh": 25.5,
            "source_system": "csv_import",
            "is_complete": True,
            "device_id": "TEST_DEVICE",
        }
    ]
    selected_indices = {0}
    duplicate_actions = {}  # new row defaults to insert

    mock_savepoint = AsyncMock()
    mock_savepoint.__aenter__ = AsyncMock(return_value=mock_savepoint)
    mock_savepoint.__aexit__ = AsyncMock(return_value=False)

    mock_db = AsyncMock()
    mock_db.begin_nested = AsyncMock(return_value=mock_savepoint)
    mock_db.add = MagicMock()

    result = await import_rows(rows, selected_indices, duplicate_actions, mock_db)

    assert result["added"] == 1
    assert result["failed"] == 0
    mock_db.add.assert_called_once()


@pytest.mark.asyncio
async def test_import_rows_returns_correct_counts():
    """import_rows returns dict with added, skipped, updated, failed keys."""
    from web.services.csv_parser import import_rows

    rows = []
    selected_indices = set()
    duplicate_actions = {}

    mock_db = AsyncMock()

    result = await import_rows(rows, selected_indices, duplicate_actions, mock_db)

    assert "added" in result
    assert "skipped" in result
    assert "updated" in result
    assert "failed" in result
