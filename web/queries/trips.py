"""Trip analytics query layer and chart builders.

Provides paginated trip queries with filtering/sorting, efficiency trend
data with 7-day rolling average chart, and driving score radar chart.
"""

import logging
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from sqlalchemy import and_, asc, desc, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.battery_status import EVBatteryStatus
from db.models.location import EVLocation
from db.models.reference import EVLocationLookup
from db.models.trip_metrics import EVTripMetrics
from db.models.vehicle_status import EVVehicleStatus
from web.queries.dashboard import _HOVER_LABEL, _PLOTLY_CONFIG, _wrap_chart
from web.queries.locations import _find_geo_match
from web.queries.settings import get_app_settings_dict
from web.queries.time_window import resolve_time_window, window_clause

logger = logging.getLogger("lightningrod.queries.trips")

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


def build_trip_time_filter(
    time_range: str,
    date_from: str | None = None,
    date_to: str | None = None,
):
    """Return a SQLAlchemy where clause for EVTripMetrics.end_time.

    Accepts presets '7d', '30d', '90d', 'ytd', '1y', 'all' plus an optional
    custom yyyy-mm-dd window (applied when no preset is active).
    Returns None when unbounded.
    """
    start, end = resolve_time_window(time_range, date_from, date_to)
    return window_clause(EVTripMetrics.end_time, start, end)


# ---------------------------------------------------------------------------
# Hide-short-trips filter helper
# ---------------------------------------------------------------------------

# Canonical defaults: 5 minutes / 4.8 km (~3 mi)
TRIP_HIDE_DEFAULT_DURATION_S = 300.0
TRIP_HIDE_DEFAULT_DISTANCE_KM = 4.8


async def get_trip_hide_settings(db: AsyncSession) -> dict:
    """Load hide-short-trips settings (canonical seconds / km) from app_settings."""
    raw = await get_app_settings_dict(
        db,
        ["trip_hide_enabled", "trip_hide_min_duration_s", "trip_hide_min_distance_km"],
    )

    def _num(value: str, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    return {
        "enabled": raw["trip_hide_enabled"] == "true",
        "min_duration_s": _num(raw["trip_hide_min_duration_s"], TRIP_HIDE_DEFAULT_DURATION_S),
        "min_distance_km": _num(raw["trip_hide_min_distance_km"], TRIP_HIDE_DEFAULT_DISTANCE_KM),
    }


def build_short_trip_filter(hide: dict, hidden: bool = False):
    """Where clause selecting visible trips (or, with hidden=True, the hidden ones).

    A trip is hidden when duration AND distance are each NULL or under their
    threshold — NULL counts as "under" so key-on rows with no data are hidden.
    Clearing either threshold keeps a trip visible. Returns None when disabled.
    """
    if not hide.get("enabled"):
        return None
    hidden_clause = and_(
        or_(
            EVTripMetrics.duration.is_(None),
            EVTripMetrics.duration < hide["min_duration_s"],
        ),
        or_(
            EVTripMetrics.distance.is_(None),
            EVTripMetrics.distance < hide["min_distance_km"],
        ),
    )
    return hidden_clause if hidden else not_(hidden_clause)


async def query_hidden_trip_count(
    db: AsyncSession,
    hide: dict,
    date_preset: str = "30d",
    device_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> int:
    """Count trips the hide-short-trips filter removes from the current view."""
    hidden_clause = build_short_trip_filter(hide, hidden=True)
    if hidden_clause is None:
        return 0
    stmt = select(func.count()).select_from(EVTripMetrics).where(hidden_clause)
    time_filter = build_trip_time_filter(date_preset, date_from, date_to)
    if time_filter is not None:
        stmt = stmt.where(time_filter)
    if device_id:
        stmt = stmt.where(EVTripMetrics.device_id == device_id)
    return (await db.execute(stmt)).scalar_one()


# ---------------------------------------------------------------------------
# Query functions
# ---------------------------------------------------------------------------


async def query_trips(
    db: AsyncSession,
    page: int = 1,
    per_page: int = 25,
    fetch_all: bool = False,
    date_preset: str = "30d",
    sort_by: str = "date",
    sort_dir: str = "desc",
    device_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    hide_filter=None,
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

    # Date preset / custom window filter
    time_filter = build_trip_time_filter(date_preset, date_from, date_to)
    if time_filter is not None:
        filters.append(time_filter)

    if hide_filter is not None:
        filters.append(hide_filter)

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
        func.count().label("trip_count"),
        func.sum(summary_subq.c.distance).label("total_distance"),
        func.sum(summary_subq.c.energy_consumed).label("total_energy"),
        func.avg(summary_subq.c.efficiency).label("avg_efficiency"),
    ).select_from(summary_subq)
    summary_result = await db.execute(summary_stmt)
    summary_row = summary_result.one()
    summary = {
        "count": summary_row.trip_count or 0,
        "total_distance": float(summary_row.total_distance) if summary_row.total_distance else 0.0,
        "total_energy": float(summary_row.total_energy) if summary_row.total_energy else 0.0,
        "avg_efficiency": float(summary_row.avg_efficiency) if summary_row.avg_efficiency else None,
    }

    # Data query. fetch_all (export) returns every matching row; otherwise
    # clamp per_page to the allowed page sizes and paginate.
    if fetch_all:
        data_stmt = stmt
    else:
        effective_per_page = per_page if per_page in VALID_PER_PAGE else PAGE_SIZE
        offset = (page - 1) * effective_per_page
        data_stmt = stmt.limit(effective_per_page).offset(offset)
    data_result = await db.execute(data_stmt)
    trips = list(data_result.scalars().all())

    return trips, total, summary


async def query_efficiency_trend(
    db: AsyncSession,
    time_range: str = "30d",
    device_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    hide_filter=None,
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

    time_filter = build_trip_time_filter(time_range, date_from, date_to)
    if time_filter is not None:
        stmt = stmt.where(time_filter)
    if device_id:
        stmt = stmt.where(EVTripMetrics.device_id == device_id)
    if hide_filter is not None:
        stmt = stmt.where(hide_filter)

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


def build_efficiency_trend_chart(
    data: list[dict],
    efficiency_factor: float = 1.0,
    efficiency_label: str = "km/kWh",
    mode: str = "full",
) -> str:
    """Build efficiency trend Plotly chart.

    mode="full" (default): per-trip scatter + 7-day rolling average overlay.
    mode="scatter": per-trip scatter only.
    mode="rolling": 7-day rolling average line only (smaller height for mini card).

    Input efficiency values are metric (km/kWh). Apply `efficiency_factor` to
    convert to display unit (MI_PER_KM for US, 1.0 for metric).

    Returns HTML string. Empty string if no data.
    """
    if not data:
        return ""

    pio.templates.default = "plotly_dark"

    df = pd.DataFrame(data)
    df = df.sort_values("date")
    df["date"] = pd.to_datetime(df["date"], utc=True)
    df["efficiency"] = df["efficiency"] * efficiency_factor

    rolling = (
        df.set_index("date")["efficiency"]
        .rolling("7D", min_periods=1)
        .mean()
        .reset_index()
    )

    fig = go.Figure()

    if mode in ("full", "scatter"):
        fig.add_trace(
            go.Scatter(
                x=df["date"],
                y=df["efficiency"],
                mode="markers",
                name="Trip Efficiency",
                marker=dict(color="#47A8E5", size=6),
                hovertemplate="<b>%{x|%b %d, %Y}</b><br>Efficiency: %{y:.2f} " + efficiency_label + "<extra></extra>",
            )
        )

    if mode in ("full", "rolling"):
        fig.add_trace(
            go.Scatter(
                x=rolling["date"],
                y=rolling["efficiency"],
                mode="lines",
                name="7-Day Avg",
                line=dict(color="#f97316", width=2),
                hovertemplate="<b>%{x|%b %d, %Y}</b><br>7-Day Avg: %{y:.2f} " + efficiency_label + "<extra></extra>",
            )
        )

    chart_height = 220 if mode in ("scatter", "rolling") else 350
    show_legend = mode == "full"

    fig.update_layout(
        height=chart_height,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#e5e7eb",
        margin=dict(l=20, r=20, t=20, b=20),
        xaxis=dict(title=""),
        yaxis=dict(title=efficiency_label),
        showlegend=show_legend,
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

    # Missing sub-scores drop out entirely (0 is ha-fordpass's "unmeasured"
    # sentinel) instead of rendering as real 0-score radar spokes.
    values = {
        k: float(v) for k, v in scores.items() if v is not None and float(v) != 0
    }
    if not values:
        return ""

    # A radar needs at least 3 axes to read as a shape. With fewer (e.g. only
    # the Overall score backfilled from the metrics sensor), render a compact
    # radial stat instead of a degenerate spike.
    if len(values) < 3:
        label, score = (
            ("Overall", values["Overall"]) if "Overall" in values
            else next(iter(values.items()))
        )
        return (
            '<div class="flex items-center gap-4 py-2">'
            f'<div class="radial-progress text-primary" role="progressbar" '
            f'style="--value:{score:.0f}; --size:4.5rem; --thickness:4px;">'
            f'<span class="text-lg font-bold">{score:.0f}</span></div>'
            f'<span class="text-sm text-base-content/60">{label} driving score</span>'
            "</div>"
        )

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


# ---------------------------------------------------------------------------
# Time-series query functions
# ---------------------------------------------------------------------------


async def query_trip_battery_series(
    db: AsyncSession,
    device_id: str,
    start_time: datetime,
    end_time: datetime,
) -> pd.DataFrame:
    """Query EVBatteryStatus for the trip time window.

    Returns DataFrame with columns: time, soc, range, kw, battery_temp, voltage.
    """
    cols = [
        EVBatteryStatus.recorded_at,
        EVBatteryStatus.hv_battery_soc,
        EVBatteryStatus.hv_battery_range,
        EVBatteryStatus.hv_battery_kw,
        EVBatteryStatus.hv_battery_temperature,
        EVBatteryStatus.hv_battery_voltage,
    ]
    stmt = (
        select(*cols)
        .where(
            EVBatteryStatus.device_id == device_id,
            EVBatteryStatus.recorded_at >= start_time,
            EVBatteryStatus.recorded_at <= end_time,
        )
        .order_by(EVBatteryStatus.recorded_at)
    )
    result = await db.execute(stmt)
    rows = result.all()

    col_names = ["time", "soc", "range", "kw", "battery_temp", "voltage"]
    if not rows:
        return pd.DataFrame(columns=col_names)

    df = pd.DataFrame(rows, columns=col_names)
    # Convert Decimal to float for numeric columns
    for c in col_names[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


async def query_trip_vehicle_series(
    db: AsyncSession,
    device_id: str,
    start_time: datetime,
    end_time: datetime,
) -> pd.DataFrame:
    """Query EVVehicleStatus for the trip time window.

    Returns DataFrame with columns: time, speed, outside_temp, cabin_temp, acceleration.
    """
    cols = [
        EVVehicleStatus.recorded_at,
        EVVehicleStatus.speed,
        EVVehicleStatus.outside_temperature,
        EVVehicleStatus.cabin_temperature,
        EVVehicleStatus.acceleration,
    ]
    stmt = (
        select(*cols)
        .where(
            EVVehicleStatus.device_id == device_id,
            EVVehicleStatus.recorded_at >= start_time,
            EVVehicleStatus.recorded_at <= end_time,
        )
        .order_by(EVVehicleStatus.recorded_at)
    )
    result = await db.execute(stmt)
    rows = result.all()

    col_names = ["time", "speed", "outside_temp", "cabin_temp", "acceleration"]
    if not rows:
        return pd.DataFrame(columns=col_names)

    df = pd.DataFrame(rows, columns=col_names)
    for c in col_names[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


# ---------------------------------------------------------------------------
# Interpolation helper
# ---------------------------------------------------------------------------


def _interpolate_series(
    df: pd.DataFrame,
    value_cols: list[str],
    max_gap_minutes: int = 5,
) -> pd.DataFrame:
    """Interpolate value columns, marking interpolated positions.

    For each column in value_cols:
    - Creates a {col}_interpolated boolean column (True where value was NaN)
    - Applies linear interpolation
    - Resets interpolated values back to NaN for gaps wider than max_gap_minutes

    Returns the modified DataFrame.
    """
    if df.empty or "time" not in df.columns:
        return df

    df = df.copy()

    # Compute time gaps
    time_diffs = df["time"].diff()
    gap_threshold = pd.Timedelta(minutes=max_gap_minutes)

    # Find indices where gap exceeds threshold
    gap_indices = df.index[time_diffs > gap_threshold].tolist()

    for col in value_cols:
        if col not in df.columns:
            continue

        # Mark where values are NaN before interpolation
        is_nan = df[col].isna()
        df[f"{col}_interpolated"] = is_nan

        # Interpolate
        df[col] = df[col].interpolate(method="linear")

        # Reset interpolated values in gap spans
        for gap_idx in gap_indices:
            # Find the position in the index
            pos = df.index.get_loc(gap_idx)
            prev_pos = pos - 1 if pos > 0 else 0

            # Walk backward from gap to find last real value before gap
            j = prev_pos
            while j >= 0 and is_nan.iloc[j]:
                df.loc[df.index[j], col] = np.nan
                j -= 1

            # Walk forward from gap to find first real value after gap
            k = pos
            while k < len(df) and is_nan.iloc[k]:
                df.loc[df.index[k], col] = np.nan
                k += 1

    return df


# ---------------------------------------------------------------------------
# Location auto-detection
# ---------------------------------------------------------------------------


GPS_MATCH_TOLERANCE_SECONDS = 30 * 60  # nearest GPS snapshot must be within 30 min


async def _nearest_gps(
    db: AsyncSession, device_id: str, target_time: datetime
) -> tuple[float, float] | None:
    """Closest GPS snapshot to target_time within the tolerance window, or None.

    Dialect-portable: SQL filters the window, Python picks the nearest (no
    PG-only epoch math, no SQLite-only julianday).
    """
    window = timedelta(seconds=GPS_MATCH_TOLERANCE_SECONDS)
    stmt = select(
        EVLocation.recorded_at, EVLocation.latitude, EVLocation.longitude
    ).where(
        EVLocation.device_id == device_id,
        EVLocation.latitude.isnot(None),
        EVLocation.longitude.isnot(None),
        EVLocation.recorded_at >= target_time - window,
        EVLocation.recorded_at <= target_time + window,
    )
    rows = (await db.execute(stmt)).all()
    if not rows:
        return None

    # SQLite returns naive datetimes; coerce both sides to UTC-aware to subtract.
    def _aware(ts: datetime) -> datetime:
        return ts if ts.tzinfo else ts.replace(tzinfo=UTC)

    target = _aware(target_time)
    nearest = min(rows, key=lambda r: abs((_aware(r.recorded_at) - target).total_seconds()))
    return (float(nearest.latitude), float(nearest.longitude))


async def detect_trip_locations(
    db: AsyncSession,
    device_id: str,
    start_time: datetime,
    end_time: datetime,
) -> tuple[str | None, str | None]:
    """Detect start and end locations for a trip from GPS data.

    Finds the closest GPS point to start_time and end_time (within 30 min),
    then matches against known EVLocationLookup entries.

    Returns (start_location_name, end_location_name).
    """
    # Load all known locations for geo matching
    loc_result = await db.execute(select(EVLocationLookup))
    all_locations = list(loc_result.scalars().all())

    def _resolve_name(coords: tuple[float, float] | None) -> str | None:
        if coords is None:
            return None
        lat, lon = coords
        match = _find_geo_match(all_locations, lat, lon)
        if match:
            return match.location_name
        return f"{lat:.2f}, {lon:.2f}"

    start_coords = await _nearest_gps(db, device_id, start_time)
    end_coords = await _nearest_gps(db, device_id, end_time)

    start_name = _resolve_name(start_coords)
    end_name = _resolve_name(end_coords)

    if start_name is None and end_name is None:
        logger.warning(
            "detect_trip_locations: no GPS within %ds for device %s "
            "between %s and %s; both endpoints render as em-dash",
            GPS_MATCH_TOLERANCE_SECONDS, device_id, start_time, end_time,
        )

    return (start_name, end_name)


async def detect_trip_location_ids(
    db: AsyncSession,
    device_id: str,
    start_time: datetime,
    end_time: datetime,
) -> tuple[int | None, int | None]:
    """Resolve start/end EVLocationLookup ids for a trip from GPS history.

    Same nearest-GPS logic as detect_trip_locations, but returns lookup ids
    only for endpoints that match a known location — the raw-coordinate
    fallback yields None so no FK is invented for an unknown place.
    """
    loc_result = await db.execute(select(EVLocationLookup))
    all_locations = list(loc_result.scalars().all())

    def _resolve_id(coords: tuple[float, float] | None) -> int | None:
        if coords is None:
            return None
        match = _find_geo_match(all_locations, coords[0], coords[1])
        return match.id if match else None

    start_coords = await _nearest_gps(db, device_id, start_time)
    end_coords = await _nearest_gps(db, device_id, end_time)
    return (_resolve_id(start_coords), _resolve_id(end_coords))


async def _location_name_by_id(
    db: AsyncSession, location_id: int | None
) -> str | None:
    """location_name for a stored EVLocationLookup id, or None when unset/missing."""
    if not location_id:
        return None
    result = await db.execute(
        select(EVLocationLookup.location_name).where(EVLocationLookup.id == location_id)
    )
    return result.scalar_one_or_none()


async def resolve_trip_location_names(
    db: AsyncSession, trip: EVTripMetrics
) -> tuple[str | None, str | None]:
    """Display names for a trip's endpoints, preferring stored FK over GPS detection.

    A repaired trip carries start_location_id/end_location_id pointing at an
    EVLocationLookup; render those names. Endpoints without a stored FK fall
    back to dynamic detect_trip_locations (only when the trip is timed).
    """
    start_name = await _location_name_by_id(db, trip.start_location_id)
    end_name = await _location_name_by_id(db, trip.end_location_id)
    if start_name is not None and end_name is not None:
        return (start_name, end_name)

    det_start, det_end = None, None
    if trip.start_time and trip.end_time:
        det_start, det_end = await detect_trip_locations(
            db, trip.device_id, trip.start_time, trip.end_time
        )
    return (start_name or det_start, end_name or det_end)


# ---------------------------------------------------------------------------
# Chart builders — drawer charts
# ---------------------------------------------------------------------------


def _build_trip_score_fallback(
    trip: EVTripMetrics, range_label: str = "km"
) -> str:
    """Render a horizontal bar chart of per-trip scores when no time-series exists.

    Used by `build_drive_graph` when both battery and vehicle DataFrames are
    empty for the trip window. Returns "" when the trip itself has no score
    or efficiency data either.
    """
    score_fields = [
        ("Driving", trip.driving_score),
        ("Speed", trip.speed_score),
        ("Acceleration", trip.acceleration_score),
        ("Deceleration", trip.deceleration_score),
    ]
    populated = [(label, float(val)) for label, val in score_fields if val is not None]
    if not populated:
        return ""

    pio.templates.default = "plotly_dark"
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=[v for _, v in populated],
            y=[label for label, _ in populated],
            orientation="h",
            marker=dict(color="#47A8E5"),
            hovertemplate="<b>%{y}</b><br>%{x:.0f}<extra></extra>",
        )
    )
    fig.update_layout(
        height=240,
        **_DARK_LAYOUT,
        margin=dict(l=80, r=20, t=20, b=20),
        xaxis=dict(title="Score (0-100)", range=[0, 100]),
    )
    return _wrap_chart(
        fig.to_html(full_html=False, include_plotlyjs=False, config=_PLOTLY_CONFIG)
    )


def _build_trip_temps_fallback(
    trip: EVTripMetrics, temp_factor_f: bool, temp_label: str
) -> str:
    """Render a bar chart of per-trip aggregate temperatures.

    Used by `build_environment_chart` when no time-series temperature
    data exists for the trip window. Returns "" when the trip itself
    has no ambient/cabin/outside_air values either.
    """
    temp_fields = [
        ("Outside Air", trip.outside_air_temp),
        ("Cabin", trip.cabin_temp),
        ("Ambient", trip.ambient_temp),
    ]
    populated = []
    for label, val in temp_fields:
        if val is None:
            continue
        v = float(val)
        if temp_factor_f:
            v = v * 9.0 / 5.0 + 32.0
        populated.append((label, v))
    if not populated:
        return ""

    pio.templates.default = "plotly_dark"
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=[label for label, _ in populated],
            y=[v for _, v in populated],
            marker=dict(color=["#47A8E5", "#f97316", "#a855f7"][: len(populated)]),
            hovertemplate="<b>%{x}</b><br>%{y:.1f} " + temp_label + "<extra></extra>",
        )
    )
    fig.update_layout(
        height=220,
        **_DARK_LAYOUT,
        margin=dict(l=50, r=20, t=20, b=30),
        yaxis=dict(title=f"Temperature ({temp_label})"),
    )
    return _wrap_chart(
        fig.to_html(full_html=False, include_plotlyjs=False, config=_PLOTLY_CONFIG)
    )


_DARK_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font_color="#e5e7eb",
    hovermode="x unified",
    hoverlabel=_HOVER_LABEL,
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
)


def _add_interpolated_traces(
    fig: go.Figure,
    df: pd.DataFrame,
    col: str,
    color: str,
    yaxis: str = "y1",
) -> None:
    """Add dashed overlay traces for interpolated segments."""
    interp_col = f"{col}_interpolated"
    if interp_col not in df.columns:
        return

    mask = df[interp_col] & df[col].notna()
    if not mask.any():
        return

    fig.add_trace(
        go.Scatter(
            x=df.loc[mask, "time"],
            y=df.loc[mask, col],
            mode="lines",
            line=dict(color=color, dash="dot", width=1),
            opacity=0.4,
            showlegend=False,
            yaxis=yaxis,
            hoverinfo="skip",
            connectgaps=False,
        )
    )


def build_drive_graph(
    battery_df: pd.DataFrame,
    vehicle_df: pd.DataFrame,
    distance_factor: float = 1.0,
    range_label: str = "km",
    speed_label: str = "km/h",
    trip: EVTripMetrics | None = None,
) -> str:
    """Build combined SOC + Speed + Range chart with dual Y-axes.

    `range` column (km) and `speed` column (km/h) are converted by
    distance_factor before plotting (1.0 for metric, MI_PER_KM for US).

    When both time-series DataFrames are empty but ``trip`` carries score
    or regen data, fall back to a horizontal bar chart of the per-trip
    scores so trips without point-by-point telemetry still get a chart.

    Returns HTML string. Empty string if neither time-series nor trip-side
    data exists.
    """
    b_empty = battery_df.empty or battery_df.drop(columns=["time"], errors="ignore").isna().all().all()
    v_empty = vehicle_df.empty or vehicle_df.drop(columns=["time"], errors="ignore").isna().all().all()

    if b_empty and v_empty:
        return _build_trip_score_fallback(trip, range_label=range_label) if trip else ""

    pio.templates.default = "plotly_dark"

    # Interpolate
    if not battery_df.empty:
        battery_df = _interpolate_series(battery_df, ["soc", "range", "kw"])
        if "range" in battery_df.columns:
            battery_df["range"] = battery_df["range"] * distance_factor
    if not vehicle_df.empty:
        vehicle_df = _interpolate_series(vehicle_df, ["speed"])
        if "speed" in vehicle_df.columns:
            vehicle_df["speed"] = vehicle_df["speed"] * distance_factor

    fig = go.Figure()

    # SOC trace (left y-axis)
    if not battery_df.empty and battery_df["soc"].notna().any():
        fig.add_trace(
            go.Scatter(
                x=battery_df["time"], y=battery_df["soc"],
                name="SOC %", line=dict(color="#47A8E5"),
                yaxis="y1", connectgaps=False,
            )
        )
        _add_interpolated_traces(fig, battery_df, "soc", "#47A8E5", "y1")

    # Range trace (left y-axis)
    if not battery_df.empty and battery_df["range"].notna().any():
        fig.add_trace(
            go.Scatter(
                x=battery_df["time"], y=battery_df["range"],
                name="Range", line=dict(color="#22c55e"),
                yaxis="y1", connectgaps=False,
            )
        )
        _add_interpolated_traces(fig, battery_df, "range", "#22c55e", "y1")

    # Speed trace (right y-axis)
    if not vehicle_df.empty and vehicle_df["speed"].notna().any():
        fig.add_trace(
            go.Scatter(
                x=vehicle_df["time"], y=vehicle_df["speed"],
                name="Speed", line=dict(color="#f97316"),
                yaxis="y2", connectgaps=False,
            )
        )
        _add_interpolated_traces(fig, vehicle_df, "speed", "#f97316", "y2")

    fig.update_layout(
        height=300,
        **_DARK_LAYOUT,
        margin=dict(l=20, r=60, t=20, b=20),
        yaxis=dict(title=f"SOC % / Range ({range_label})"),
        yaxis2=dict(title=f"Speed ({speed_label})", overlaying="y", side="right"),
    )

    return _wrap_chart(
        fig.to_html(full_html=False, include_plotlyjs=False, config=_PLOTLY_CONFIG)
    )


def build_environment_chart(
    vehicle_df: pd.DataFrame,
    temp_factor_f: bool = False,
    temp_label: str = "\u00b0C",
    trip: EVTripMetrics | None = None,
) -> str:
    """Build temperature chart (outside + cabin) for trip time window.

    When the time-series ``vehicle_df`` is empty but the trip row itself
    carries any of ambient_temp / cabin_temp / outside_air_temp, fall
    back to a small bar chart of the trip-aggregate temperatures so the
    drawer still shows something useful for trips without minute-by-minute
    telemetry.

    Returns HTML string. Empty string if no temperature data exists at
    either level.
    """
    if vehicle_df.empty:
        return _build_trip_temps_fallback(trip, temp_factor_f, temp_label) if trip else ""

    has_outside = "outside_temp" in vehicle_df.columns and vehicle_df["outside_temp"].notna().any()
    has_cabin = "cabin_temp" in vehicle_df.columns and vehicle_df["cabin_temp"].notna().any()

    if not has_outside and not has_cabin:
        return _build_trip_temps_fallback(trip, temp_factor_f, temp_label) if trip else ""

    pio.templates.default = "plotly_dark"

    vehicle_df = _interpolate_series(vehicle_df, ["outside_temp", "cabin_temp"])

    # Convert °C -> °F if requested
    if temp_factor_f:
        for col in ("outside_temp", "cabin_temp"):
            if col in vehicle_df.columns:
                vehicle_df[col] = vehicle_df[col] * 9 / 5 + 32

    fig = go.Figure()

    if has_outside:
        fig.add_trace(
            go.Scatter(
                x=vehicle_df["time"], y=vehicle_df["outside_temp"],
                name="Outside", line=dict(color="#47A8E5"),
                connectgaps=False,
            )
        )
        _add_interpolated_traces(fig, vehicle_df, "outside_temp", "#47A8E5")

    if has_cabin:
        fig.add_trace(
            go.Scatter(
                x=vehicle_df["time"], y=vehicle_df["cabin_temp"],
                name="Cabin", line=dict(color="#f97316"),
                connectgaps=False,
            )
        )
        _add_interpolated_traces(fig, vehicle_df, "cabin_temp", "#f97316")

    fig.update_layout(
        height=250,
        **_DARK_LAYOUT,
        margin=dict(l=20, r=20, t=20, b=20),
        yaxis=dict(title=f"Temperature ({temp_label})"),
    )

    return _wrap_chart(
        fig.to_html(full_html=False, include_plotlyjs=False, config=_PLOTLY_CONFIG)
    )


# ---------------------------------------------------------------------------
# Chart builders — expanded modal charts
# ---------------------------------------------------------------------------


def build_expanded_battery_chart(
    battery_df: pd.DataFrame,
    distance_factor: float = 1.0,
    range_label: str = "km",
    temp_factor_f: bool = False,
    temp_label: str = "\u00b0C",
) -> str:
    """Full battery detail chart: SOC, Range, Battery Temp, Voltage, kW.

    Dual Y-axes: left for SOC%/Range, right for kW/Voltage.
    Range (km) and battery_temp (°C) are converted to display units.
    """
    if battery_df.empty:
        return ""

    has_data = False
    for col in ["soc", "range", "kw", "battery_temp", "voltage"]:
        if col in battery_df.columns and battery_df[col].notna().any():
            has_data = True
            break

    if not has_data:
        return ""

    pio.templates.default = "plotly_dark"

    battery_df = _interpolate_series(
        battery_df, ["soc", "range", "kw", "battery_temp", "voltage"]
    )

    if "range" in battery_df.columns:
        battery_df["range"] = battery_df["range"] * distance_factor
    if temp_factor_f and "battery_temp" in battery_df.columns:
        battery_df["battery_temp"] = battery_df["battery_temp"] * 9 / 5 + 32

    fig = go.Figure()

    # Left axis: SOC, Range
    if battery_df["soc"].notna().any():
        fig.add_trace(go.Scatter(
            x=battery_df["time"], y=battery_df["soc"],
            name="SOC %", line=dict(color="#47A8E5"),
            yaxis="y1", connectgaps=False,
        ))
        _add_interpolated_traces(fig, battery_df, "soc", "#47A8E5", "y1")

    if battery_df["range"].notna().any():
        fig.add_trace(go.Scatter(
            x=battery_df["time"], y=battery_df["range"],
            name=f"Range ({range_label})", line=dict(color="#22c55e"),
            yaxis="y1", connectgaps=False,
        ))
        _add_interpolated_traces(fig, battery_df, "range", "#22c55e", "y1")

    # Right axis: kW, Voltage
    if battery_df["kw"].notna().any():
        fig.add_trace(go.Scatter(
            x=battery_df["time"], y=battery_df["kw"],
            name="Power (kW)", line=dict(color="#f97316"),
            yaxis="y2", connectgaps=False,
        ))
        _add_interpolated_traces(fig, battery_df, "kw", "#f97316", "y2")

    if battery_df["voltage"].notna().any():
        fig.add_trace(go.Scatter(
            x=battery_df["time"], y=battery_df["voltage"],
            name="Voltage (V)", line=dict(color="#a855f7"),
            yaxis="y2", connectgaps=False,
        ))
        _add_interpolated_traces(fig, battery_df, "voltage", "#a855f7", "y2")

    # Battery temp as legendonly toggle
    if battery_df["battery_temp"].notna().any():
        fig.add_trace(go.Scatter(
            x=battery_df["time"], y=battery_df["battery_temp"],
            name=f"Batt Temp ({temp_label})", line=dict(color="#ef4444"),
            yaxis="y1", connectgaps=False,
            visible="legendonly",
        ))
        _add_interpolated_traces(fig, battery_df, "battery_temp", "#ef4444", "y1")

    fig.update_layout(
        height=400,
        **_DARK_LAYOUT,
        margin=dict(l=30, r=70, t=20, b=30),
        yaxis=dict(title=f"SOC % / Range ({range_label})"),
        yaxis2=dict(title="kW / Voltage", overlaying="y", side="right"),
    )

    return _wrap_chart(
        fig.to_html(full_html=False, include_plotlyjs=False, config=_PLOTLY_CONFIG)
    )


def build_expanded_environment_chart(
    vehicle_df: pd.DataFrame,
    temp_factor_f: bool = False,
    temp_label: str = "\u00b0C",
) -> str:
    """Expanded environment chart -- same as drawer version but taller."""
    if vehicle_df.empty:
        return ""

    has_outside = "outside_temp" in vehicle_df.columns and vehicle_df["outside_temp"].notna().any()
    has_cabin = "cabin_temp" in vehicle_df.columns and vehicle_df["cabin_temp"].notna().any()

    if not has_outside and not has_cabin:
        return ""

    pio.templates.default = "plotly_dark"

    vehicle_df = _interpolate_series(vehicle_df, ["outside_temp", "cabin_temp"])

    if temp_factor_f:
        for col in ("outside_temp", "cabin_temp"):
            if col in vehicle_df.columns:
                vehicle_df[col] = vehicle_df[col] * 9 / 5 + 32

    fig = go.Figure()

    if has_outside:
        fig.add_trace(go.Scatter(
            x=vehicle_df["time"], y=vehicle_df["outside_temp"],
            name="Outside", line=dict(color="#47A8E5"),
            connectgaps=False,
        ))
        _add_interpolated_traces(fig, vehicle_df, "outside_temp", "#47A8E5")

    if has_cabin:
        fig.add_trace(go.Scatter(
            x=vehicle_df["time"], y=vehicle_df["cabin_temp"],
            name="Cabin", line=dict(color="#f97316"),
            connectgaps=False,
        ))
        _add_interpolated_traces(fig, vehicle_df, "cabin_temp", "#f97316")

    fig.update_layout(
        height=350,
        **_DARK_LAYOUT,
        margin=dict(l=30, r=30, t=20, b=30),
        yaxis=dict(title=f"Temperature ({temp_label})"),
    )

    return _wrap_chart(
        fig.to_html(full_html=False, include_plotlyjs=False, config=_PLOTLY_CONFIG)
    )


def build_expanded_driving_chart(
    vehicle_df: pd.DataFrame,
    distance_factor: float = 1.0,
    speed_label: str = "km/h",
) -> str:
    """Speed and acceleration over time with dual Y-axes."""
    if vehicle_df.empty:
        return ""

    has_speed = "speed" in vehicle_df.columns and vehicle_df["speed"].notna().any()
    has_accel = "acceleration" in vehicle_df.columns and vehicle_df["acceleration"].notna().any()

    if not has_speed and not has_accel:
        return ""

    pio.templates.default = "plotly_dark"

    vehicle_df = _interpolate_series(vehicle_df, ["speed", "acceleration"])

    if "speed" in vehicle_df.columns:
        vehicle_df["speed"] = vehicle_df["speed"] * distance_factor

    fig = go.Figure()

    if has_speed:
        fig.add_trace(go.Scatter(
            x=vehicle_df["time"], y=vehicle_df["speed"],
            name="Speed", line=dict(color="#47A8E5"),
            yaxis="y1", connectgaps=False,
        ))
        _add_interpolated_traces(fig, vehicle_df, "speed", "#47A8E5", "y1")

    if has_accel:
        fig.add_trace(go.Scatter(
            x=vehicle_df["time"], y=vehicle_df["acceleration"],
            name="Acceleration", line=dict(color="#f97316"),
            yaxis="y2", connectgaps=False,
        ))
        _add_interpolated_traces(fig, vehicle_df, "acceleration", "#f97316", "y2")

    fig.update_layout(
        height=350,
        **_DARK_LAYOUT,
        margin=dict(l=30, r=70, t=20, b=30),
        yaxis=dict(title=f"Speed ({speed_label})"),
        yaxis2=dict(title="Acceleration", overlaying="y", side="right"),
    )

    return _wrap_chart(
        fig.to_html(full_html=False, include_plotlyjs=False, config=_PLOTLY_CONFIG)
    )
