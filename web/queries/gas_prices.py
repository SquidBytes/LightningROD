"""Gas price history CRUD and lookup functions.

Provides operations for managing monthly gas prices with two tracks
(station-specific and regional average), plus HA sensor reading staging.
"""


from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.reference import GasPriceHistory, GasPriceReading


async def get_all_gas_prices(db: AsyncSession) -> list[GasPriceHistory]:
    """Return all gas price history entries ordered by year DESC, month DESC."""
    result = await db.execute(
        select(GasPriceHistory).order_by(
            GasPriceHistory.year.desc(), GasPriceHistory.month.desc()
        )
    )
    return list(result.scalars().all())


async def upsert_gas_price(
    db: AsyncSession,
    year: int,
    month: int,
    station_price: float | None = None,
    average_price: float | None = None,
    source: str = "manual",
) -> GasPriceHistory:
    """Insert or update a gas price entry for the given year/month.

    Only updates non-None price fields so that manual station_price
    doesn't overwrite HA-sourced average_price and vice versa.
    """
    # Check for existing entry
    result = await db.execute(
        select(GasPriceHistory).where(
            GasPriceHistory.year == year,
            GasPriceHistory.month == month,
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        if station_price is not None:
            existing.station_price = station_price
        if average_price is not None:
            existing.average_price = average_price
        existing.source = source
        await db.flush()
        await db.commit()
        await db.refresh(existing)
        return existing
    else:
        entry = GasPriceHistory(
            year=year,
            month=month,
            station_price=station_price,
            average_price=average_price,
            source=source,
        )
        db.add(entry)
        await db.flush()
        await db.commit()
        await db.refresh(entry)
        return entry


async def delete_gas_price(db: AsyncSession, price_id: int) -> bool:
    """Delete a gas price history entry by ID. Returns True if deleted."""
    result = await db.execute(
        select(GasPriceHistory).where(GasPriceHistory.id == price_id)
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        return False
    await db.delete(entry)
    await db.commit()
    return True


async def get_gas_price_for_date(
    db: AsyncSession, year: int, month: int
) -> tuple[float | None, float | None]:
    """Return (station_price, average_price) for the matching or nearest earlier month.

    Uses a simple strategy: find the entry where (year, month) <= the given date,
    ordered by recency. If no entries exist at all, returns (3.50, 3.50) as default.
    """
    # Find matching or nearest earlier entry
    result = await db.execute(
        select(GasPriceHistory)
        .where(
            (GasPriceHistory.year < year)
            | ((GasPriceHistory.year == year) & (GasPriceHistory.month <= month))
        )
        .order_by(GasPriceHistory.year.desc(), GasPriceHistory.month.desc())
        .limit(1)
    )
    entry = result.scalar_one_or_none()

    if entry is None:
        return (3.50, 3.50)

    station = float(entry.station_price) if entry.station_price is not None else None
    average = float(entry.average_price) if entry.average_price is not None else None
    return (station, average)


async def compute_monthly_averages(
    db: AsyncSession, entity_id: str
) -> dict[tuple[int, int], float]:
    """Query GasPriceReading for given entity_id, group by year+month.
    Returns {(year, month): avg_price}. Used for HA sensor integration.
    """
    result = await db.execute(
        select(
            func.extract("year", GasPriceReading.recorded_at).label("yr"),
            func.extract("month", GasPriceReading.recorded_at).label("mo"),
            func.avg(GasPriceReading.price).label("avg_price"),
        )
        .where(GasPriceReading.entity_id == entity_id)
        .group_by("yr", "mo")
    )
    rows = result.all()
    return {(int(row.yr), int(row.mo)): float(row.avg_price) for row in rows}


async def store_gas_price_reading(
    db: AsyncSession, entity_id: str, price: float, recorded_at
) -> None:
    """Insert a GasPriceReading row. Used for HA sensor integration."""
    reading = GasPriceReading(
        entity_id=entity_id,
        price=price,
        recorded_at=recorded_at,
    )
    db.add(reading)
    await db.flush()
    await db.commit()


async def store_gas_price_reading_if_new(
    db: AsyncSession, entity_id: str, price: float, recorded_at
) -> bool:
    """Insert a GasPriceReading only if (entity_id, recorded_at) is not already present.

    Used by the historical backfill path where re-running should be idempotent.
    Returns True if a new row was inserted, False if the reading already exists.
    The table has no unique constraint, so we guard with an explicit existence
    check instead of relying on ON CONFLICT.
    """
    existing = await db.execute(
        select(GasPriceReading.id).where(
            GasPriceReading.entity_id == entity_id,
            GasPriceReading.recorded_at == recorded_at,
        ).limit(1)
    )
    if existing.scalar_one_or_none() is not None:
        return False

    reading = GasPriceReading(
        entity_id=entity_id,
        price=price,
        recorded_at=recorded_at,
    )
    db.add(reading)
    await db.flush()
    await db.commit()
    return True


async def build_fuel_price_trend_chart(
    db: AsyncSession,
    distance_unit: str,
) -> str:
    """Build a Plotly HTML chart of station + average gas prices over time.

    Reads metric ($/L) values from gas_price_history; converts to user-display
    units ($/gal in US, $/L in metric) before plotting. Returns Plotly HTML for
    direct innerHTML swap into the chart slot. Empty data returns a centered
    placeholder div instead of an empty Plotly figure.
    """
    from datetime import date

    import plotly.graph_objects as go

    from web.queries.dashboard import _HOVER_LABEL, _PLOTLY_CONFIG, _wrap_chart
    from web.unit_system import convert_price_per_volume, get_units

    rows = await get_all_gas_prices(db)
    if not rows:
        return (
            '<div class="flex flex-col items-center py-8 text-center">'
            '<span class="text-base-content/40 text-sm">'
            'No fuel price history available. Add entries above to see the trend.'
            '</span></div>'
        )

    rows_sorted = sorted(rows, key=lambda r: (r.year, r.month))
    x = [date(r.year, r.month, 1) for r in rows_sorted]
    station = [
        convert_price_per_volume(float(r.station_price), distance_unit)
        if r.station_price is not None else None
        for r in rows_sorted
    ]
    average = [
        convert_price_per_volume(float(r.average_price), distance_unit)
        if r.average_price is not None else None
        for r in rows_sorted
    ]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x, y=station, name="Station", mode="lines+markers",
        line=dict(color="#47A8E5", width=2),
        connectgaps=False,
        hovertemplate="%{y:.3f}<extra>Station</extra>",
    ))
    fig.add_trace(go.Scatter(
        x=x, y=average, name="Average", mode="lines+markers",
        line=dict(color="#888", width=2, dash="dash"),
        connectgaps=False,
        hovertemplate="%{y:.3f}<extra>Average</extra>",
    ))
    fuel_volume_label = get_units(distance_unit=distance_unit)["fuel_volume_label"]
    fig.update_layout(
        margin=dict(l=40, r=20, t=20, b=40),
        height=260,
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        xaxis_title=None,
        yaxis_title=f"$/{fuel_volume_label}",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="rgba(255,255,255,0.7)"),
        hoverlabel=_HOVER_LABEL,
    )
    return _wrap_chart(
        fig.to_html(full_html=False, include_plotlyjs=False, config=_PLOTLY_CONFIG)
    )
