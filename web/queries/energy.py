"""Query helpers for energy."""

import statistics
from datetime import UTC, datetime, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.battery_status import EVBatteryStatus
from db.models.charging_session import EVChargingSession
from db.models.trip_metrics import EVTripMetrics
from web.queries.costs import build_time_filter

# Adjustable without hunting through code
MOVING_AVG_WINDOW = 10

# DB values -> display labels
CHARGE_TYPE_LABELS = {
    "AC": "AC (L1/L2)",
    "DC": "DC Fast",
    "Unknown": "Unknown",
}

# Synthetic charge curve — taper model constants
SYNTHETIC_CURVE_PLATEAU_SOC = 80.0
SYNTHETIC_CURVE_TAIL_FRACTION = 0.20  # kW at 100% SOC = max_kw * 0.20
SYNTHETIC_CURVE_SAMPLE_STEP = 2       # SOC step (%) — yields 51 points

# Shared Plotly modebar config — show minimal controls, hide logo
_PLOTLY_CONFIG = {
    "displayModeBar": "hover",
    "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"],
    "displaylogo": False,
}

_HOVER_LABEL = dict(bgcolor="#1f2937", font_color="#e5e7eb", bordercolor="#374151")


def _wrap_chart(html: str) -> str:
    """Wrap Plotly HTML in a container for modebar positioning."""
    return f'<div class="plotly-chart-wrap">{html}</div>'


def build_time_filter_trip(range_str: str):
    """Return a SQLAlchemy where clause for EVTripMetrics.start_time.

    Same logic as costs.build_time_filter but targets EVTripMetrics.start_time.
    Returns None for 'all' (no filter).
    Accepts: '7d', '30d', '90d', 'ytd', '1y', 'all'
    """
    if not range_str or range_str == "all":
        return None

    now = datetime.now(UTC)

    if range_str == "7d":
        cutoff = now - timedelta(days=7)
    elif range_str == "30d":
        cutoff = now - timedelta(days=30)
    elif range_str == "90d":
        cutoff = now - timedelta(days=90)
    elif range_str == "ytd":
        cutoff = datetime(now.year, 1, 1, tzinfo=UTC)
    elif range_str == "1y":
        cutoff = now - timedelta(days=365)
    else:
        return None

    return EVTripMetrics.start_time >= cutoff


async def query_energy_summary(db: AsyncSession, time_range: str = "all", device_id: str | None = None) -> dict:
    """Compute energy summary from EVChargingSession rows.

    Returns dict with:
    - total_kwh: float
    - total_sessions: int
    - avg_efficiency: float | None  (km/kWh, metric base unit)
    - best_efficiency: float | None  (km/kWh, metric base unit)
    - worst_efficiency: float | None  (km/kWh, metric base unit)
    - by_charge_type: list of dicts [{charge_type, kwh, session_count}, ...]
    - sessions_for_chart: list of dicts [{date, efficiency, charge_type}, ...]

    All efficiency values returned in km/kWh (metric base). The route handler
    applies unit conversion before passing to template/chart.

    NOTE: Efficiency computed as distance_added / energy_kwh (NOT the stored
    efficiency column from FordPass).
    """
    # Reuse build_time_filter from costs (targets EVChargingSession.session_start_utc)
    stmt = select(EVChargingSession).where(EVChargingSession.energy_kwh.isnot(None))
    time_filter = build_time_filter(time_range)
    if time_filter is not None:
        stmt = stmt.where(time_filter)
    if device_id:
        stmt = stmt.where(EVChargingSession.device_id == device_id)

    result = await db.execute(stmt)
    sessions = result.scalars().all()

    total_kwh = 0.0
    total_sessions = 0
    efficiencies: list[float] = []
    by_charge_type: dict[str, dict] = {}
    sessions_for_chart: list[dict] = []

    for s in sessions:
        total_sessions += 1
        kwh = float(s.energy_kwh or 0)
        total_kwh += kwh

        # Group by charge type — AC, DC, or Unknown (anything else/NULL)
        raw_ct = s.charge_type
        ct = raw_ct if raw_ct in ("AC", "DC") else "Unknown"
        if ct not in by_charge_type:
            by_charge_type[ct] = {
                "charge_type": ct,
                "kwh": 0.0,
                "session_count": 0,
                "total_cost": 0.0,
            }
        by_charge_type[ct]["kwh"] += kwh
        by_charge_type[ct]["session_count"] += 1
        if s.cost is not None:
            by_charge_type[ct]["total_cost"] += float(s.cost)

        # Compute efficiency — requires BOTH distance_added > 0 and energy_kwh > 0
        if (
            s.distance_added is not None
            and float(s.distance_added) > 0
            and kwh > 0
        ):
            eff = float(s.distance_added) / kwh  # km/kWh
            efficiencies.append(eff)
            sessions_for_chart.append({
                "date": s.session_start_utc,
                "efficiency": eff,
                "charge_type": ct,
            })

    avg_efficiency = sum(efficiencies) / len(efficiencies) if efficiencies else None
    best_efficiency = max(efficiencies) if efficiencies else None
    worst_efficiency = min(efficiencies) if efficiencies else None

    # Adaptive downsampling for chart data — reduce dense scatter plots
    # to daily averages when dataset is large (>200 points for all/1y ranges).
    # Summary totals above remain unchanged — only chart data is reduced.
    if len(sessions_for_chart) > 200 and time_range in ("all", "1y"):
        df = pd.DataFrame(sessions_for_chart)
        df["date"] = pd.to_datetime(df["date"], utc=True)
        df_chart = (
            df.groupby([df["date"].dt.date, "charge_type"])
            .agg(efficiency=("efficiency", "mean"))
            .reset_index()
        )
        # Convert date back to datetime for chart compatibility
        df_chart["date"] = pd.to_datetime(df_chart["date"])
        sessions_for_chart = df_chart.to_dict("records")

    return {
        "total_kwh": total_kwh,
        "total_sessions": total_sessions,
        "avg_efficiency": avg_efficiency,
        "best_efficiency": best_efficiency,
        "worst_efficiency": worst_efficiency,
        "by_charge_type": list(by_charge_type.values()),
        "sessions_for_chart": sessions_for_chart,
    }


async def monthly_energy_series(
    db: AsyncSession,
    time_range: str = "all",
    device_id: str | None = None,
) -> list[tuple[datetime, float]]:
    """Return (month_start, total_energy_kwh) per month in the filter range.

    Buckets sessions by calendar month of `session_start_utc`. Ordered ascending
    by month_start. Used for the Total Energy sparkline on /charging/performance.

    Returns an empty list when no sessions match — the route collapses this to
    a "No trend data" placeholder rather than a Plotly chart.
    """
    stmt = (
        select(
            func.date_trunc("month", EVChargingSession.session_start_utc).label("m"),
            func.coalesce(func.sum(EVChargingSession.energy_kwh), 0.0).label("kwh"),
        )
        .where(EVChargingSession.energy_kwh.isnot(None))
        .group_by("m")
        .order_by("m")
    )
    time_filter = build_time_filter(time_range)
    if time_filter is not None:
        stmt = stmt.where(time_filter)
    if device_id is not None:
        stmt = stmt.where(EVChargingSession.device_id == device_id)

    result = await db.execute(stmt)
    return [(row.m, float(row.kwh)) for row in result.all() if row.m is not None]


async def efficiency_over_time_series(
    db: AsyncSession,
    time_range: str = "all",
    device_id: str | None = None,
) -> list[tuple[datetime, float]]:
    """Return (session_start, km_per_kwh) per session with usable energy+distance.

    Efficiency returned in km/kWh (metric base unit), matching the convention
    established by `query_energy_summary` — route handlers apply MI_PER_KM when
    rendering for US distance units.

    Sessions with NULL or non-positive energy_kwh / distance_added are excluded.
    Ordered ascending by session_start_utc. Empty list when nothing qualifies.
    """
    stmt = (
        select(
            EVChargingSession.session_start_utc,
            EVChargingSession.distance_added,
            EVChargingSession.energy_kwh,
        )
        .where(EVChargingSession.energy_kwh.isnot(None))
        .where(EVChargingSession.energy_kwh > 0)
        .where(EVChargingSession.distance_added.isnot(None))
        .where(EVChargingSession.distance_added > 0)
        .where(EVChargingSession.session_start_utc.isnot(None))
        .order_by(EVChargingSession.session_start_utc)
    )
    time_filter = build_time_filter(time_range)
    if time_filter is not None:
        stmt = stmt.where(time_filter)
    if device_id is not None:
        stmt = stmt.where(EVChargingSession.device_id == device_id)

    result = await db.execute(stmt)
    return [
        (start, float(dist) / float(kwh))
        for start, dist, kwh in result.all()
    ]


async def query_regen_summary(db: AsyncSession, time_range: str = "all", device_id: str | None = None) -> dict | None:
    """Compute regen braking summary from EVTripMetrics.
    Returns None when ev_trip_metrics has no rows with range_regenerated data
    (triggers "No data available" card state in the template).
    Returns dict with:
    regen_total: float
    trip_count: int
    NOTE: range_regenerated units are ambiguous — likely "miles of range recovered"
    but not confirmed. Template uses generic "range units" label.
    TODO: Validate range_regenerated units against raw fordpass API response.
    PITFALL: SUM on empty/null data returns NULL not 0. Count-first guard prevents
    TypeError: float(None).
    """
    trip_filter = build_time_filter_trip(time_range)

    # Count-first guard: check rows with range_regenerated data
    count_stmt = select(func.count()).where(
        EVTripMetrics.range_regenerated.isnot(None)
    )
    if trip_filter is not None:
        count_stmt = count_stmt.where(trip_filter)
    if device_id:
        count_stmt = count_stmt.where(EVTripMetrics.device_id == device_id)

    count_result = await db.execute(count_stmt)
    row_count = count_result.scalar_one()

    if row_count == 0:
        return None

    # Safe to query SUM — we know rows exist
    sum_stmt = select(
        func.sum(EVTripMetrics.range_regenerated),
        func.count(),
    ).where(EVTripMetrics.range_regenerated.isnot(None))
    if trip_filter is not None:
        sum_stmt = sum_stmt.where(trip_filter)
    if device_id:
        sum_stmt = sum_stmt.where(EVTripMetrics.device_id == device_id)

    sum_result = await db.execute(sum_stmt)
    row = sum_result.one()
    regen_total, trip_count = row

    return {
        "regen_total": float(regen_total) if regen_total is not None else 0.0,
        "trip_count": int(trip_count),
    }


async def query_regen_for_chart(
    db: AsyncSession, time_range: str = "all", device_id: str | None = None,
) -> list[dict] | None:
    """Return per-trip regen data for chart secondary y-axis overlay.

    Separate from query_regen_summary because the chart needs per-row data, not totals.

    Returns None if no rows found.
    Returns list of dicts: [{date: start_time, range_regenerated: float}, ...]
    """
    trip_filter = build_time_filter_trip(time_range)

    stmt = select(EVTripMetrics).where(EVTripMetrics.range_regenerated.isnot(None))
    if trip_filter is not None:
        stmt = stmt.where(trip_filter)
    if device_id:
        stmt = stmt.where(EVTripMetrics.device_id == device_id)

    result = await db.execute(stmt)
    rows = result.scalars().all()

    if not rows:
        return None

    chart_data = [
        {
            "date": r.start_time,
            "range_regenerated": float(r.range_regenerated or 0),
        }
        for r in rows
    ]

    # Adaptive downsampling — aggregate to daily sums for large datasets
    if len(chart_data) > 200 and time_range in ("all", "1y"):
        df = pd.DataFrame(chart_data)
        df["date"] = pd.to_datetime(df["date"], utc=True)
        chart_data = (
            df.groupby(df["date"].dt.date)
            .agg(range_regenerated=("range_regenerated", "sum"))
            .reset_index()
        )
        chart_data["date"] = pd.to_datetime(chart_data["date"])
        chart_data = chart_data.to_dict("records")

    return chart_data


async def query_monthly_energy(db: AsyncSession, time_range: str = "all", device_id: str | None = None) -> list[dict]:
    """Return monthly kWh grouped by charge type for stacked area chart.

    Returns list of dicts: [{"month": "2025-01", "charge_type": "AC", "kwh": 45.2}, ...]
    """
    stmt = select(EVChargingSession).where(EVChargingSession.energy_kwh.isnot(None))
    time_filter = build_time_filter(time_range)
    if time_filter is not None:
        stmt = stmt.where(time_filter)
    if device_id:
        stmt = stmt.where(EVChargingSession.device_id == device_id)

    result = await db.execute(stmt)
    sessions = result.scalars().all()

    monthly: dict[tuple, float] = {}
    for s in sessions:
        if s.session_start_utc is None:
            continue
        month = s.session_start_utc.strftime("%Y-%m")
        raw_ct = (s.charge_type or "").strip()
        # Fold any AC Level 1/2 variant into a single "AC" bucket so the
        # monthly chart renders two series (AC, DC) rather than three.
        lowered = raw_ct.lower()
        if raw_ct == "DC":
            ct = "DC"
        elif raw_ct == "AC" or "level 2" in lowered or "level_2" in lowered or "level 1" in lowered or "level_1" in lowered or lowered.startswith("ac"):
            ct = "AC"
        else:
            ct = raw_ct or "Unknown"
        key = (month, ct)
        monthly[key] = monthly.get(key, 0.0) + float(s.energy_kwh or 0)

    return [
        {"month": month, "charge_type": ct, "kwh": kwh}
        for (month, ct), kwh in sorted(monthly.items())
    ]


def build_charge_type_donut_chart(
    by_charge_type: list[dict],
    metric: str = "kwh",  # "kwh" | "count" | "cost"
) -> str:
    """AC/DC/Unknown donut with three metric modes.

    Args:
        by_charge_type: rows from query_energy_summary, each
            {charge_type, kwh, session_count, total_cost}
        metric: which field to visualize ("kwh", "count", "cost").

    Returns "" when there's nothing to render. Rows with charge_type="Unknown"
    and the chosen metric value == 0 are filtered out, as are any rows whose
    metric value is zero (no slice to draw).
    """
    if not by_charge_type:
        return ""

    def _value(row: dict) -> float:
        if metric == "count":
            return float(row.get("session_count", 0) or 0)
        if metric == "cost":
            return float(row.get("total_cost", 0) or 0)
        return float(row.get("kwh", 0) or 0)

    filtered = [
        r for r in by_charge_type
        if not (r.get("charge_type") == "Unknown" and _value(r) == 0)
    ]
    filtered = [r for r in filtered if _value(r) > 0]
    if not filtered:
        return ""

    labels = [CHARGE_TYPE_LABELS.get(r["charge_type"], r["charge_type"]) for r in filtered]
    values = [_value(r) for r in filtered]

    if metric == "count":
        hover_unit = "sessions"
        value_fmt = "%{value:.0f}"
    elif metric == "cost":
        hover_unit = ""  # leading $ in format
        value_fmt = "$%{value:,.2f}"
    else:
        hover_unit = "kWh"
        value_fmt = "%{value:.1f}"

    color_map = {
        "AC (L1/L2)": "#60a5fa",
        "DC Fast": "#f97316",
        "Unknown": "#9ca3af",
    }
    colors = [color_map.get(lbl, "#6B7280") for lbl in labels]

    pio.templates.default = "plotly_dark"
    fig = go.Figure(data=[go.Pie(
        labels=labels,
        values=values,
        hole=0.5,
        marker=dict(colors=colors),
        textinfo="percent",
        hovertemplate=(
            "<b>%{label}</b><br>"
            + value_fmt + (" " + hover_unit if hover_unit else "")
            + " (%{percent})<extra></extra>"
        ),
    )])
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#e5e7eb",
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5),
        margin=dict(l=10, r=10, t=10, b=30),
        hoverlabel=_HOVER_LABEL,
    )
    return _wrap_chart(fig.to_html(full_html=False, include_plotlyjs=False, config=_PLOTLY_CONFIG))


def build_monthly_energy_chart(monthly_data: list[dict]) -> str:
    """Build stacked area chart of monthly kWh by charge type.

    Args:
        monthly_data: List of dicts with keys: month, charge_type, kwh.

    Returns:
        HTML div string (include_plotlyjs=False). Empty string if no data.
    """
    if not monthly_data:
        return ""

    pio.templates.default = "plotly_dark"

    df = pd.DataFrame(monthly_data)
    color_map = {"AC": "#60a5fa", "DC": "#f97316", "Unknown": "#9ca3af"}

    fig = px.area(
        df,
        x="month",
        y="kwh",
        color="charge_type",
        color_discrete_map=color_map,
        labels={"kwh": "kWh", "month": "", "charge_type": "Type"},
    )

    fig.update_traces(hovertemplate="<b>%{data.name}</b><br>%{x}: %{y:.1f} kWh<extra></extra>")

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#e5e7eb",
        margin=dict(l=20, r=20, t=20, b=20),
        yaxis_title="kWh",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hoverlabel=_HOVER_LABEL,
    )

    return _wrap_chart(fig.to_html(full_html=False, include_plotlyjs=False, config=_PLOTLY_CONFIG))


def build_efficiency_chart(
    sessions: list[dict],
    regen_data: list[dict] | None,
    unit_label: str = "km/kWh",
    efficiency_factor: float = 1.0,
    range_factor: float = 1.0,
) -> str:
    """Build Plotly efficiency trend scatter chart with rolling average overlay.

    Args:
        sessions: List of {date, efficiency, charge_type} dicts from query_energy_summary.
                  Efficiency is in km/kWh (metric base).
        regen_data: Optional list of {date, range_regenerated} dicts for secondary y-axis.
                    range_regenerated is in km (metric base). Pass None when no regen data.
        unit_label: Axis label (e.g. 'mi/kWh' or 'km/kWh').
        efficiency_factor: Conversion multiplier applied to efficiency (1.0 metric, MI_PER_KM for US).
        range_factor: Conversion multiplier applied to range_regenerated (1.0 metric, MI_PER_KM for US).

    Returns:
        HTML string with embedded Plotly div (include_plotlyjs=False — Plotly CDN in base.html).
        Returns empty string if sessions is empty.

    NOTE: Do NOT use trendline='rolling' with color= grouping — it creates per-group MA lines.
    The add_trace approach gives a single overall MA line across all charge types.
    """
    if not sessions:
        return ""

    pio.templates.default = "plotly_dark"

    df = pd.DataFrame(sessions)
    df = df.sort_values("date").dropna(subset=["efficiency"])

    # Apply unit conversion factor (metric base -> display unit)
    df["efficiency"] = df["efficiency"] * efficiency_factor

    # Compute rolling average across all sessions (not per charge type)
    window = min(MOVING_AVG_WINDOW, len(df))
    df["rolling_avg"] = df["efficiency"].rolling(window=window, min_periods=1).mean()

    color_map = {"AC": "#60a5fa", "DC": "#f97316", "Unknown": "#9ca3af"}

    if regen_data is not None and len(regen_data) > 0:
        # Secondary y-axis: efficiency scatter + regen overlay
        from plotly.subplots import make_subplots

        fig = make_subplots(specs=[[{"secondary_y": True}]])

        # Add scatter traces per charge type manually
        for ct, color in color_map.items():
            ct_df = df[df["charge_type"] == ct]
            if ct_df.empty:
                continue
            fig.add_trace(
                go.Scatter(
                    x=ct_df["date"],
                    y=ct_df["efficiency"],
                    mode="markers",
                    name=CHARGE_TYPE_LABELS.get(ct, ct),
                    marker=dict(color=color),
                    hovertemplate=(
                        "<b>%{x|%b %d, %Y}</b><br>"
                        "%{y:.2f} " + unit_label + "<extra>%{data.name}</extra>"
                    ),
                ),
                secondary_y=False,
            )

        # Rolling average on primary y-axis
        fig.add_trace(
            go.Scatter(
                x=df["date"],
                y=df["rolling_avg"],
                mode="lines",
                name=f"{MOVING_AVG_WINDOW}-session avg",
                line=dict(color="#facc15", width=2, dash="dash"),
            ),
            secondary_y=False,
        )

        # Regen on secondary y-axis (convert km -> display range unit)
        regen_df = pd.DataFrame(regen_data).sort_values("date")
        regen_df["range_display"] = regen_df["range_regenerated"] * range_factor
        fig.add_trace(
            go.Scatter(
                x=regen_df["date"],
                y=regen_df["range_display"],
                mode="lines+markers",
                name="Range Recovered",
                line=dict(color="#4ade80", dash="dot", width=1.5),
                opacity=0.7,
            ),
            secondary_y=True,
        )

        fig.update_yaxes(title_text="Range Recovered", secondary_y=True, showgrid=False)

    else:
        # Simple case: no regen secondary axis (expected for seeded dataset)
        fig = px.scatter(
            df,
            x="date",
            y="efficiency",
            color="charge_type",
            color_discrete_map=color_map,
            labels={"efficiency": unit_label, "date": ""},
        )

        # Improve hover on scatter traces
        fig.update_traces(
            hovertemplate=(
                "<b>%{x|%b %d, %Y}</b><br>"
                "%{y:.2f} " + unit_label + "<extra>%{data.name}</extra>"
            )
        )

        # Add rolling average as a single overall line
        fig.add_trace(
            go.Scatter(
                x=df["date"],
                y=df["rolling_avg"],
                mode="lines",
                name=f"{MOVING_AVG_WINDOW}-session avg",
                line=dict(color="#facc15", width=2, dash="dash"),
            )
        )

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#e5e7eb",
        margin=dict(l=20, r=20, t=20, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        yaxis_title=unit_label,
        hoverlabel=_HOVER_LABEL,
    )

    return _wrap_chart(fig.to_html(full_html=False, include_plotlyjs=False, config=_PLOTLY_CONFIG))


# ---------------------------------------------------------------------------
# Synthetic DC charge curve fallback
# ---------------------------------------------------------------------------


def synthesize_curve(
    max_kw: float,
    plateau_soc: float = SYNTHETIC_CURVE_PLATEAU_SOC,
    tail_fraction: float = SYNTHETIC_CURVE_TAIL_FRACTION,
) -> list[dict]:
    """Piecewise synthetic DC-fast charge curve.

    Flat at abs(max_kw) from 0..plateau_soc, then linear interpolation down to
    abs(max_kw) * tail_fraction at SOC=100. No calibration to specific vehicles.

    Returns list of {"soc": int, "kw": float} sampled every
    SYNTHETIC_CURVE_SAMPLE_STEP percent (default: 51 points 0..100).
    """
    max_kw = abs(float(max_kw or 0))
    plateau_soc = float(plateau_soc)
    tail_fraction = float(tail_fraction)
    end_kw = max_kw * tail_fraction
    points: list[dict] = []
    for soc in range(0, 101, SYNTHETIC_CURVE_SAMPLE_STEP):
        if soc <= plateau_soc:
            kw = max_kw
        else:
            # Linear interpolate from max_kw at plateau_soc to end_kw at 100
            t = (soc - plateau_soc) / (100.0 - plateau_soc)
            kw = max_kw - (max_kw - end_kw) * t
        points.append({"soc": soc, "kw": kw})
    return points


def build_synthetic_charge_curve_chart(
    max_kw: float,
    dc_session_count: int,
    plateau_soc: float = SYNTHETIC_CURVE_PLATEAU_SOC,
    tail_fraction: float = SYNTHETIC_CURVE_TAIL_FRACTION,
) -> str:
    """Aggregate synthetic DC charge curve chart.

    Returns "" when dc_session_count == 0 or max_kw <= 0 (no data to synthesize).
    X axis = State of Charge (%), Y axis = Power (kW).
    """
    if not dc_session_count or not max_kw or max_kw <= 0:
        return ""
    points = synthesize_curve(max_kw, plateau_soc, tail_fraction)
    pio.templates.default = "plotly_dark"
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=[p["soc"] for p in points],
        y=[p["kw"] for p in points],
        mode="lines",
        name="Synthetic DC curve",
        line=dict(color="#f97316", width=3),
        hovertemplate="<b>%{x:.0f}%% SOC</b><br>%{y:.1f} kW<extra></extra>",
        fill="tozeroy",
        fillcolor="rgba(249, 115, 22, 0.12)",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#e5e7eb",
        margin=dict(l=40, r=20, t=20, b=40),
        xaxis=dict(title="State of Charge (%)", range=[0, 100]),
        yaxis=dict(title="Power (kW)", range=[0, 200]),
        showlegend=False,
        hoverlabel=_HOVER_LABEL,
    )
    return _wrap_chart(
        fig.to_html(full_html=False, include_plotlyjs=False, config=_PLOTLY_CONFIG)
    )


async def query_synthetic_curve_inputs(
    db: AsyncSession,
    time_range: str = "all",
    device_id: str | None = None,
) -> dict:
    """Collect DC-session peak kW values for synthetic curve aggregation.

    Returns {"median_peak_kw": float|None, "dc_session_count": int}.
    Peak per session uses max_power, falling back to evse_max_power_kw,
    then charger_rated_kw. Sessions with no usable peak are skipped.

    Aggregation: MEDIAN across DC sessions (robust to outliers).
    Excludes AC sessions entirely.
    """
    stmt = select(
        EVChargingSession.max_power,
        EVChargingSession.evse_max_power_kw,
        EVChargingSession.charger_rated_kw,
    ).where(EVChargingSession.charge_type == "DC")

    time_filter = build_time_filter(time_range)
    if time_filter is not None:
        stmt = stmt.where(time_filter)
    if device_id is not None:
        stmt = stmt.where(EVChargingSession.device_id == device_id)

    result = await db.execute(stmt)
    peaks: list[float] = []
    for row in result.all():
        mp, evse, rated = row
        for candidate in (mp, evse, rated):
            if candidate is not None:
                peaks.append(abs(float(candidate)))
                break

    if not peaks:
        return {"median_peak_kw": None, "dc_session_count": 0}
    return {
        "median_peak_kw": float(statistics.median(peaks)),
        "dc_session_count": len(peaks),
    }


async def has_real_charge_curve_data(
    db: AsyncSession,
    time_range: str = "all",
    device_id: str | None = None,
) -> bool:
    """True iff any DC session in window has >= 3 EVBatteryStatus rows within its
    [session_start_utc, session_end_utc] span.
    Reuses 's "< 3 detailed points" threshold for fallback trigger:
    when True the synthetic curve is hidden and the real charge curve
    chart is rendered instead.
    """
    sess_stmt = select(
        EVChargingSession.id,
        EVChargingSession.session_start_utc,
        EVChargingSession.session_end_utc,
        EVChargingSession.device_id,
    ).where(EVChargingSession.charge_type == "DC")

    time_filter = build_time_filter(time_range)
    if time_filter is not None:
        sess_stmt = sess_stmt.where(time_filter)
    if device_id is not None:
        sess_stmt = sess_stmt.where(EVChargingSession.device_id == device_id)

    sess_rows = (await db.execute(sess_stmt)).all()
    if not sess_rows:
        return False

    for _sid, start, end, dev in sess_rows:
        if start is None or end is None:
            continue
        count_stmt = (
            select(func.count())
            .select_from(EVBatteryStatus)
            .where(
                EVBatteryStatus.recorded_at >= start,
                EVBatteryStatus.recorded_at <= end,
                EVBatteryStatus.device_id == dev,
            )
        )
        count = (await db.execute(count_stmt)).scalar_one()
        if count is not None and count >= 3:
            return True
    return False
