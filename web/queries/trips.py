"""Trip analytics query layer and chart builders.

Provides paginated trip queries with filtering/sorting, efficiency trend
data with 7-day rolling average chart, and driving score radar chart.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from sqlalchemy import asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.trip_metrics import EVTripMetrics
from web.queries.dashboard import _HOVER_LABEL, _PLOTLY_CONFIG, _wrap_chart

PAGE_SIZE = 25
VALID_PER_PAGE = {25, 50, 100}

SORTABLE_COLUMNS = {
    "date": EVTripMetrics.end_time,
    "distance": EVTripMetrics.distance,
    "efficiency": EVTripMetrics.efficiency,
    "duration": EVTripMetrics.duration,
    "temperature": EVTripMetrics.outside_air_temp,
    "score": EVTripMetrics.driving_score,
    "regen": EVTripMetrics.range_regenerated,
}


# ---------------------------------------------------------------------------
# Time filter helper
# ---------------------------------------------------------------------------


def build_trip_time_filter(time_range: str) -> Optional[datetime]:
    """Return a cutoff datetime for trip queries.

    Maps preset strings to a UTC cutoff datetime.
    Returns None for 'all' (no filter).
    Accepts: '7d', '30d', '90d', 'ytd', '1y', 'all'
    """
    if not time_range or time_range == "all":
        return None

    now = datetime.now(timezone.utc)

    if time_range == "7d":
        return now - timedelta(days=7)
    elif time_range == "30d":
        return now - timedelta(days=30)
    elif time_range == "90d":
        return now - timedelta(days=90)
    elif time_range == "ytd":
        return now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    elif time_range == "1y":
        return now - timedelta(days=365)

    return None


# ---------------------------------------------------------------------------
# Query functions
# ---------------------------------------------------------------------------


async def query_trips(
    db: AsyncSession,
    page: int = 1,
    per_page: int = 25,
    date_preset: str = "30d",
    sort_by: str = "date",
    sort_dir: str = "desc",
    device_id: Optional[str] = None,
) -> tuple[list, int, dict]:
    """Query trip metrics with optional filters, sorting, and pagination.

    Returns a tuple of (trips, total_count, summary_dict).
    summary_dict contains: count, total_distance, total_energy, avg_efficiency.
    """
    # Determine sort column and direction
    sort_col = SORTABLE_COLUMNS.get(sort_by, EVTripMetrics.end_time)
    if sort_dir == "asc":
        order_expr = asc(sort_col).nulls_last()
    else:
        order_expr = desc(sort_col).nulls_last()

    # Base statement
    stmt = select(EVTripMetrics).order_by(order_expr)

    # Accumulate filters
    filters = []

    if device_id:
        filters.append(EVTripMetrics.device_id == device_id)

    # Date preset filter
    cutoff = build_trip_time_filter(date_preset)
    if cutoff is not None:
        filters.append(EVTripMetrics.end_time >= cutoff)

    for f in filters:
        stmt = stmt.where(f)

    # Count query
    count_subq = stmt.subquery()
    count_stmt = select(func.count()).select_from(count_subq)
    total_result = await db.execute(count_stmt)
    total: int = total_result.scalar_one()

    # Summary query
    summary_subq = stmt.subquery()
    summary_stmt = select(
        func.count().label("count"),
        func.sum(summary_subq.c.distance).label("total_distance"),
        func.sum(summary_subq.c.energy_consumed).label("total_energy"),
        func.avg(summary_subq.c.efficiency).label("avg_efficiency"),
    ).select_from(summary_subq)
    summary_result = await db.execute(summary_stmt)
    summary_row = summary_result.one()
    summary = {
        "count": summary_row.count or 0,
        "total_distance": float(summary_row.total_distance) if summary_row.total_distance else 0.0,
        "total_energy": float(summary_row.total_energy) if summary_row.total_energy else 0.0,
        "avg_efficiency": float(summary_row.avg_efficiency) if summary_row.avg_efficiency else None,
    }

    # Data query with pagination
    effective_per_page = per_page if per_page in VALID_PER_PAGE else PAGE_SIZE
    offset = (page - 1) * effective_per_page
    data_stmt = stmt.limit(effective_per_page).offset(offset)
    data_result = await db.execute(data_stmt)
    trips = list(data_result.scalars().all())

    return trips, total, summary


async def query_efficiency_trend(
    db: AsyncSession,
    time_range: str = "30d",
    device_id: Optional[str] = None,
) -> list[dict]:
    """Query trip efficiency data for the trend chart.

    Returns list of dicts with keys: date, efficiency, distance.
    Only includes rows where efficiency is not None.
    """
    stmt = (
        select(
            EVTripMetrics.end_time,
            EVTripMetrics.efficiency,
            EVTripMetrics.distance,
        )
        .where(EVTripMetrics.efficiency.isnot(None))
        .order_by(EVTripMetrics.end_time)
    )

    cutoff = build_trip_time_filter(time_range)
    if cutoff is not None:
        stmt = stmt.where(EVTripMetrics.end_time >= cutoff)
    if device_id:
        stmt = stmt.where(EVTripMetrics.device_id == device_id)

    result = await db.execute(stmt)
    chart_data = [
        {
            "date": row.end_time,
            "efficiency": float(row.efficiency),
            "distance": float(row.distance) if row.distance else 0.0,
        }
        for row in result.all()
    ]

    # Adaptive downsampling — aggregate to daily averages for large datasets
    if len(chart_data) > 200:
        df = pd.DataFrame(chart_data)
        df["date"] = pd.to_datetime(df["date"], utc=True)
        chart_data = (
            df.groupby(df["date"].dt.date)
            .agg(efficiency=("efficiency", "mean"), distance=("distance", "sum"))
            .reset_index()
        )
        chart_data["date"] = pd.to_datetime(chart_data["date"])
        chart_data = chart_data.to_dict("records")

    return chart_data


# ---------------------------------------------------------------------------
# Chart builders
# ---------------------------------------------------------------------------


def build_efficiency_trend_chart(data: list[dict]) -> str:
    """Build efficiency trend Plotly chart with scatter points and 7-day rolling average.

    Returns HTML string. Empty string if no data.
    """
    if not data:
        return ""

    pio.templates.default = "plotly_dark"

    df = pd.DataFrame(data)
    df = df.sort_values("date")
    df["date"] = pd.to_datetime(df["date"], utc=True)

    # Calculate 7-day rolling average
    rolling = (
        df.set_index("date")["efficiency"]
        .rolling("7D", min_periods=1)
        .mean()
        .reset_index()
    )

    fig = go.Figure()

    # Individual trip points (markers only)
    fig.add_trace(
        go.Scatter(
            x=df["date"],
            y=df["efficiency"],
            mode="markers",
            name="Trip Efficiency",
            marker=dict(color="#47A8E5", size=6),
            hovertemplate="<b>%{x|%b %d, %Y}</b><br>Efficiency: %{y:.2f} mi/kWh<extra></extra>",
        )
    )

    # 7-day rolling average (line only)
    fig.add_trace(
        go.Scatter(
            x=rolling["date"],
            y=rolling["efficiency"],
            mode="lines",
            name="7-Day Avg",
            line=dict(color="#f97316", width=2),
            hovertemplate="<b>%{x|%b %d, %Y}</b><br>7-Day Avg: %{y:.2f} mi/kWh<extra></extra>",
        )
    )

    fig.update_layout(
        height=350,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#e5e7eb",
        margin=dict(l=20, r=20, t=20, b=20),
        xaxis=dict(title=""),
        yaxis=dict(title="mi/kWh"),
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
        hoverlabel=_HOVER_LABEL,
    )

    return _wrap_chart(
        fig.to_html(full_html=False, include_plotlyjs=False, config=_PLOTLY_CONFIG)
    )


def build_driving_score_radar(trip) -> str:
    """Build driving score radar (Scatterpolar) chart for a single trip.

    Accepts a trip object (EVTripMetrics instance or any object with
    driving_score, speed_score, acceleration_score, deceleration_score).
    Returns HTML string. Empty string if all scores are None or 0.
    """
    scores = {
        "Speed": getattr(trip, "speed_score", None),
        "Acceleration": getattr(trip, "acceleration_score", None),
        "Deceleration": getattr(trip, "deceleration_score", None),
        "Overall": getattr(trip, "driving_score", None),
    }

    # Convert to floats, default to 0
    values = {k: float(v) if v is not None else 0 for k, v in scores.items()}

    # Return empty if all are 0 or None
    if all(v == 0 for v in values.values()):
        return ""

    pio.templates.default = "plotly_dark"

    categories = list(values.keys())
    r_values = list(values.values())

    # Close the polygon
    categories.append(categories[0])
    r_values.append(r_values[0])

    fig = go.Figure()

    fig.add_trace(
        go.Scatterpolar(
            r=r_values,
            theta=categories,
            fill="toself",
            fillcolor="rgba(71, 168, 229, 0.2)",
            line=dict(color="#47A8E5"),
            hovertemplate="<b>%{theta}</b><br>Score: %{r:.0f}<extra></extra>",
        )
    )

    fig.update_layout(
        height=250,
        polar=dict(
            radialaxis=dict(range=[0, 100], visible=True),
        ),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#e5e7eb",
        margin=dict(l=40, r=40, t=20, b=20),
        showlegend=False,
    )

    return _wrap_chart(
        fig.to_html(full_html=False, include_plotlyjs=False, config=_PLOTLY_CONFIG)
    )
