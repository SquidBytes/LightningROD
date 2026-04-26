"""Trip analytics query layer and chart builders.

Provides paginated trip queries with filtering/sorting, efficiency trend
data with 7-day rolling average chart, and driving score radar chart.
"""

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from sqlalchemy import asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.battery_status import EVBatteryStatus
from db.models.location import EVLocation
from db.models.reference import EVLocationLookup
from db.models.trip_metrics import EVTripMetrics
from db.models.vehicle_status import EVVehicleStatus
from web.queries.dashboard import _HOVER_LABEL, _PLOTLY_CONFIG, _wrap_chart
from web.queries.locations import _find_geo_match

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


def build_trip_time_filter(time_range: str) -> datetime | None:
    """Return a cutoff datetime for trip queries.

    Maps preset strings to a UTC cutoff datetime.
    Returns None for 'all' (no filter).
    Accepts: '7d', '30d', '90d', 'ytd', '1y', 'all'
    """
    if not time_range or time_range == "all":
        return None

    now = datetime.now(UTC)

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
    device_id: str | None = None,
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
    device_id: str | None = None,
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
    tolerance_seconds = 30 * 60  # 30 minutes

    async def _find_nearest_gps(target_time: datetime) -> tuple[float, float] | None:
        stmt = (
            select(EVLocation.latitude, EVLocation.longitude)
            .where(
                EVLocation.device_id == device_id,
                EVLocation.latitude.isnot(None),
                EVLocation.longitude.isnot(None),
                func.abs(func.extract("epoch", EVLocation.recorded_at - target_time)) <= tolerance_seconds,
            )
            .order_by(func.abs(func.extract("epoch", EVLocation.recorded_at - target_time)))
            .limit(1)
        )
        result = await db.execute(stmt)
        row = result.one_or_none()
        if row:
            return (float(row.latitude), float(row.longitude))
        return None

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

    start_coords = await _find_nearest_gps(start_time)
    end_coords = await _find_nearest_gps(end_time)

    return (_resolve_name(start_coords), _resolve_name(end_coords))


# ---------------------------------------------------------------------------
# Chart builders — drawer charts
# ---------------------------------------------------------------------------

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
) -> str:
    """Build combined SOC + Speed + Range chart with dual Y-axes.

    `range` column (km) and `speed` column (km/h) are converted by
    distance_factor before plotting (1.0 for metric, MI_PER_KM for US).

    Returns HTML string. Empty string if both DataFrames are empty.
    """
    b_empty = battery_df.empty or battery_df.drop(columns=["time"], errors="ignore").isna().all().all()
    v_empty = vehicle_df.empty or vehicle_df.drop(columns=["time"], errors="ignore").isna().all().all()

    if b_empty and v_empty:
        return ""

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
) -> str:
    """Build temperature chart (outside + cabin) for trip time window.

    Returns HTML string. Empty string if no temperature data.
    """
    if vehicle_df.empty:
        return ""

    has_outside = "outside_temp" in vehicle_df.columns and vehicle_df["outside_temp"].notna().any()
    has_cabin = "cabin_temp" in vehicle_df.columns and vehicle_df["cabin_temp"].notna().any()

    if not has_outside and not has_cabin:
        return ""

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
