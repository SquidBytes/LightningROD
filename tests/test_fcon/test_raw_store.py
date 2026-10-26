"""fcon_raw_samples: fingerprint dedupe, raw-then-parse, and retention."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from db.models.fcon_raw_sample import FconRawSample
from tests.conftest import FixedSessionFactory
from tests.test_fcon.payloads import telemetry
from web.services.sources.fcon import ingest
from web.services.sources.fcon.identity import device_id_of
from web.services.sources.fcon.raw_store import FconRawArchive, fingerprint

pytestmark = pytest.mark.db

FETCHED = datetime(2026, 8, 24, 17, 0, tzinfo=UTC)


def _utc(ts: datetime) -> datetime:
    """SQLite hands timestamps back naive."""
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


async def _count(db) -> int:
    return await db.scalar(select(func.count()).select_from(FconRawSample))


def test_fingerprint_tracks_update_times_not_values():
    a = telemetry()
    b = telemetry()
    b["metrics"]["speed"]["value"] = 99.0
    assert fingerprint("telemetry", a) == fingerprint("telemetry", b)
    c = telemetry()
    c["metrics"]["speed"]["updateTime"] = "2026-08-24T17:05:00Z"
    assert fingerprint("telemetry", a) != fingerprint("telemetry", c)
    d = telemetry()
    d["metrics"]["tirePressure"][1]["updateTime"] = "2026-08-24T17:05:00Z"
    assert fingerprint("telemetry", a) != fingerprint("telemetry", d)


def test_fingerprint_separates_endpoints():
    assert fingerprint("garage", {"x": 1}) != fingerprint("wallbox", {"x": 1})


def test_device_id_is_lowercased_vin():
    assert device_id_of({"vin": "YOUR_VIN_ABC"}) == "your_vin_abc"
    assert device_id_of({}) is None


async def test_unchanged_poll_bumps_last_seen_only(db_session):
    archive = FconRawArchive()
    assert await archive.store(db_session, 1, "telemetry", telemetry(), FETCHED)
    later = FETCHED + timedelta(minutes=5)
    assert not await archive.store(db_session, 1, "telemetry", telemetry(), later)
    await db_session.commit()
    [row] = (await db_session.execute(select(FconRawSample))).scalars().all()
    assert _utc(row.last_seen_at) == later
    # recorded_at is the payload's own time, not fetch time.
    assert _utc(row.recorded_at) == datetime(2026, 8, 24, 16, 59, 31, tzinfo=UTC)
    assert row.endpoint == "telemetry"
    assert row.source_system == "fcon"


async def test_new_reading_is_a_new_row(db_session):
    archive = FconRawArchive()
    await archive.store(db_session, 1, "telemetry", telemetry(), FETCHED)
    assert await archive.store(
        db_session, 1, "telemetry", telemetry("2026-08-24T17:10:00Z"), FETCHED
    )
    # Same response for another config row is not a duplicate.
    assert await archive.store(db_session, 2, "telemetry", telemetry(), FETCHED)
    assert await _count(db_session) == 3


async def test_store_sample_parses_only_new_responses(db_session, monkeypatch):
    import web.services.ingestion.raw_archive as archive_module

    factory = FixedSessionFactory(db_session)
    monkeypatch.setattr(ingest, "AsyncSessionLocal", factory)
    monkeypatch.setattr(archive_module, "AsyncSessionLocal", factory)
    import web.services.sources.fcon.raw_store as raw_store

    monkeypatch.setattr(raw_store, "AsyncSessionLocal", factory)
    parsed: list[str] = []

    async def fake_process(endpoint, payload, db, source_config=None):
        parsed.append(endpoint)

    monkeypatch.setattr(ingest.adapter, "process_sample", fake_process)
    # Retention parked: the fixture is older than the default window.
    monkeypatch.setattr(ingest.fcon_raw_archive, "_prune_due_at", float("inf"))
    await ingest.store_sample(1, "telemetry", telemetry())
    await ingest.store_sample(1, "telemetry", telemetry())
    await ingest.store_sample(1, "garage", {"vin": "YOUR_VIN"})
    assert parsed == ["telemetry", "garage"]
    assert await _count(db_session) == 2


async def test_retention_prunes_old_rows(db_session, monkeypatch):
    import web.services.ingestion.raw_archive as archive_module
    import web.services.sources.fcon.raw_store as raw_store

    factory = FixedSessionFactory(db_session)
    monkeypatch.setattr(archive_module, "AsyncSessionLocal", factory)
    monkeypatch.setattr(raw_store, "AsyncSessionLocal", factory)
    archive = FconRawArchive()
    old = telemetry("2020-01-01T00:00:00Z")
    await archive.store(db_session, 1, "telemetry", old, FETCHED)
    fresh = telemetry(datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
    await archive.store(db_session, 1, "telemetry", fresh, FETCHED)
    await db_session.commit()
    await archive.prune()
    assert await _count(db_session) == 1


async def test_retention_estimate_falls_back_then_uses_observed_rate(db_session):
    from web.services.sources.fcon.retention import (
        TYPICAL_SAMPLE_BYTES,
        TYPICAL_SAMPLES_PER_DAY,
        estimate,
        human_size,
    )

    fresh = await estimate(db_session, 30)
    assert not fresh.observed
    factor = 0.45 if db_session.get_bind().dialect.name == "postgresql" else 1.0
    assert fresh.bytes == int(TYPICAL_SAMPLES_PER_DAY * TYPICAL_SAMPLE_BYTES * factor * 30)
    assert (await estimate(db_session, 0)).per_year

    archive = FconRawArchive()
    for i in range(60):
        await archive.store(db_session, 1, "garage", {"n": i}, FETCHED)
    await db_session.commit()
    rows = (await db_session.execute(select(FconRawSample))).scalars().all()
    for i, row in enumerate(rows):
        row.ingested_at = datetime.now(UTC) - timedelta(hours=i)
    await db_session.commit()
    seen = await estimate(db_session, 30)
    assert seen.observed
    assert 20 < seen.samples_per_day < 30
    assert human_size(38_000_000) == "38 MB"
