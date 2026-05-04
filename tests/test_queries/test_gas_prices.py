"""Query-layer tests for gas_prices.py — the one module that previously
had no direct test file. Covers upsert, nearest-earlier lookup, monthly
averaging, and deduped reading storage.
"""
from datetime import UTC, datetime

import pytest

from db.models.reference import GasPriceReading
from web.queries.gas_prices import (
    compute_monthly_averages,
    delete_gas_price,
    get_all_gas_prices,
    get_gas_price_for_date,
    store_gas_price_reading,
    store_gas_price_reading_if_new,
    upsert_gas_price,
)

pytestmark = [pytest.mark.query, pytest.mark.db]


# ---------------------------------------------------------------------------
# upsert_gas_price / get_all_gas_prices / delete_gas_price
# ---------------------------------------------------------------------------


async def test_upsert_gas_price_inserts_new_row(db_session):
    entry = await upsert_gas_price(
        db_session, year=2025, month=6, station_price=4.00, average_price=4.20
    )
    assert entry.id is not None
    assert entry.year == 2025
    assert entry.month == 6
    assert float(entry.station_price) == 4.00
    assert float(entry.average_price) == 4.20


async def test_upsert_gas_price_updates_existing_without_clobbering(db_session):
    """upserting station_price alone must NOT null out average_price."""
    await upsert_gas_price(
        db_session, year=2025, month=7, station_price=4.10, average_price=4.25
    )
    updated = await upsert_gas_price(
        db_session, year=2025, month=7, station_price=4.30
    )
    assert float(updated.station_price) == 4.30
    assert float(updated.average_price) == 4.25  # preserved


async def test_get_all_gas_prices_ordered_desc(db_session):
    await upsert_gas_price(db_session, year=2025, month=1, station_price=3.80)
    await upsert_gas_price(db_session, year=2025, month=6, station_price=4.00)
    await upsert_gas_price(db_session, year=2024, month=12, station_price=3.90)

    rows = await get_all_gas_prices(db_session)
    keys = [(r.year, r.month) for r in rows]
    assert keys == sorted(keys, reverse=True)
    assert keys[0] == (2025, 6)


async def test_delete_gas_price_returns_false_when_missing(db_session):
    deleted = await delete_gas_price(db_session, price_id=999_999)
    assert deleted is False


async def test_delete_gas_price_removes_existing_row(db_session):
    entry = await upsert_gas_price(
        db_session, year=2025, month=8, station_price=4.15
    )
    deleted = await delete_gas_price(db_session, price_id=entry.id)
    assert deleted is True
    rows = await get_all_gas_prices(db_session)
    assert all(r.id != entry.id for r in rows)


# ---------------------------------------------------------------------------
# get_gas_price_for_date — nearest-earlier-month lookup
# ---------------------------------------------------------------------------


async def test_get_gas_price_for_date_returns_default_when_empty(db_session):
    station, average = await get_gas_price_for_date(db_session, 2025, 6)
    assert station == pytest.approx(3.50)
    assert average == pytest.approx(3.50)


async def test_get_gas_price_for_date_exact_match(db_session):
    await upsert_gas_price(
        db_session, year=2025, month=6, station_price=4.00, average_price=4.20
    )
    station, average = await get_gas_price_for_date(db_session, 2025, 6)
    assert station == pytest.approx(4.00)
    assert average == pytest.approx(4.20)


async def test_get_gas_price_for_date_uses_nearest_earlier_month(db_session):
    """When no entry exists for the requested month, the lookup must walk
    backwards to the most-recent earlier entry rather than returning the default."""
    await upsert_gas_price(
        db_session, year=2025, month=3, station_price=3.80, average_price=3.95
    )
    await upsert_gas_price(
        db_session, year=2025, month=5, station_price=4.00, average_price=4.10
    )
    # Ask for month 9 — should find the May 2025 entry, not the March one.
    station, average = await get_gas_price_for_date(db_session, 2025, 9)
    assert station == pytest.approx(4.00)
    assert average == pytest.approx(4.10)


async def test_get_gas_price_for_date_crosses_year_boundary(db_session):
    await upsert_gas_price(
        db_session, year=2024, month=11, station_price=3.70, average_price=3.85
    )
    station, average = await get_gas_price_for_date(db_session, 2025, 2)
    assert station == pytest.approx(3.70)
    assert average == pytest.approx(3.85)


# ---------------------------------------------------------------------------
# compute_monthly_averages / HA reading storage
# ---------------------------------------------------------------------------


async def test_store_gas_price_reading_inserts_row(db_session):
    await store_gas_price_reading(
        db_session,
        entity_id="sensor.test_gas",
        price=3.99,
        recorded_at=datetime(2025, 6, 1, 12, 0, tzinfo=UTC),
    )
    result = await db_session.execute(
        GasPriceReading.__table__.select().where(
            GasPriceReading.entity_id == "sensor.test_gas"
        )
    )
    rows = result.fetchall()
    assert len(rows) == 1


async def test_store_gas_price_reading_if_new_is_idempotent(db_session):
    ts = datetime(2025, 6, 1, 12, 0, tzinfo=UTC)
    inserted_first = await store_gas_price_reading_if_new(
        db_session, entity_id="sensor.test_gas", price=3.99, recorded_at=ts
    )
    inserted_second = await store_gas_price_reading_if_new(
        db_session, entity_id="sensor.test_gas", price=3.99, recorded_at=ts
    )
    assert inserted_first is True
    assert inserted_second is False


async def test_compute_monthly_averages_groups_by_year_month(db_session):
    entity = "sensor.test_gas_avg"
    readings = [
        (datetime(2025, 6, 1, 10, 0, tzinfo=UTC), 3.50),
        (datetime(2025, 6, 15, 10, 0, tzinfo=UTC), 3.60),  # avg June = 3.55
        (datetime(2025, 7, 1, 10, 0, tzinfo=UTC), 4.00),
    ]
    for ts, price in readings:
        await store_gas_price_reading(
            db_session, entity_id=entity, price=price, recorded_at=ts
        )

    averages = await compute_monthly_averages(db_session, entity_id=entity)
    assert averages[(2025, 6)] == pytest.approx(3.55)
    assert averages[(2025, 7)] == pytest.approx(4.00)


async def test_compute_monthly_averages_filters_by_entity_id(db_session):
    ts = datetime(2025, 6, 1, 10, 0, tzinfo=UTC)
    await store_gas_price_reading(
        db_session, entity_id="sensor.other", price=2.00, recorded_at=ts
    )
    averages = await compute_monthly_averages(db_session, entity_id="sensor.nope")
    assert averages == {}


# ---------------------------------------------------------------------------
# UNIT-02 metric-storage stubs (Wave 0 scaffold — Plan 05 fills bodies)
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_gas_price_metric_storage_round_trip():
    """UNIT-02: upsert_gas_price stores values verbatim (caller pre-converts to $/L).

    Wave 3: Plan 05 fills in. Asserts upsert_gas_price(year, month, station_price=1.058)
    reads back as 1.058 — i.e., the query layer does NOT secretly convert.
    Conversion responsibility lives in the route handler / event handler.
    """
    pytest.skip("Wave 3: Plan 05 verifies metric storage round-trip with conversion at the boundary")


@pytest.mark.db
async def test_gas_price_history_returns_metric_values():
    """UNIT-02: list_gas_prices returns raw metric ($/L) values; route handler does display conversion.

    Wave 3: Plan 05 fills in.
    """
    pytest.skip("Wave 3: Plan 05 verifies query-layer returns raw metric values")
