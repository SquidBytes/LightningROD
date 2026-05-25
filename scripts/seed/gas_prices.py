"""Seed module: gas price history (monthly) and recent daily readings."""

from __future__ import annotations

import logging
import random
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.reference import GasPriceHistory, GasPriceReading
from web.unit_system import to_metric_price_per_volume

logger = logging.getLogger(__name__)

_ENTITY_ID = "sensor.demo_station_gas_price"
_SOURCE = "manual"


def _month_subtract(d: date, months: int) -> date:
    """Return the first of the month that is `months` before `d`'s month."""
    total_months = d.year * 12 + (d.month - 1) - months
    year, month = divmod(total_months, 12)
    return date(year, month + 1, 1)


async def seed(db: AsyncSession) -> int:
    """Insert gas price history (18 months) and readings (30 days).

    Idempotent:
    - GasPriceHistory: skips (year, month) pairs already present.
    - GasPriceReading: skips dates already present (by recorded_at day).

    Returns total rows inserted.
    """
    rng = random.Random(42)
    today = date.today()
    inserted = 0

    # ── GasPriceHistory ───────────────────────────────────────────────────────
    existing_history: set[tuple[int, int]] = set(
        (row.year, row.month)
        for row in (await db.execute(select(GasPriceHistory))).scalars().all()
    )

    for i in range(18):
        month_date = _month_subtract(today, i)
        key = (month_date.year, month_date.month)
        if key in existing_history:
            continue

        # Pick user-friendly $/gal numbers, then convert to $/L for canonical
        # storage. Without this conversion the read path multiplies again at
        # display, producing 3.78× drift.
        station_price_gal = round(rng.uniform(2.85, 4.20), 2)
        average_price_gal = round(station_price_gal + rng.uniform(-0.15, 0.15), 2)
        station_price = to_metric_price_per_volume(station_price_gal, "us")
        average_price = to_metric_price_per_volume(average_price_gal, "us")

        db.add(
            GasPriceHistory(
                year=month_date.year,
                month=month_date.month,
                station_price=station_price,
                average_price=average_price,
                source=_SOURCE,
            )
        )
        inserted += 1

    logger.info("gas_prices: inserted %d GasPriceHistory rows", inserted)

    # ── GasPriceReading ───────────────────────────────────────────────────────
    existing_reading_dates: set[date] = set(
        row.recorded_at.date()
        if isinstance(row.recorded_at, datetime)
        else row.recorded_at
        for row in (await db.execute(select(GasPriceReading))).scalars().all()
    )

    reading_count = 0
    for i in range(30):
        day = today - timedelta(days=i)
        if day in existing_reading_dates:
            continue

        db.add(
            GasPriceReading(
                entity_id=_ENTITY_ID,
                price=to_metric_price_per_volume(round(rng.uniform(2.85, 4.20), 2), "us"),
                recorded_at=datetime(
                    day.year, day.month, day.day, 12, 0, 0, tzinfo=UTC
                ),
            )
        )
        reading_count += 1

    logger.info("gas_prices: inserted %d GasPriceReading rows", reading_count)
    inserted += reading_count
    return inserted
