from datetime import datetime, timezone, timedelta
from typing import Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.charging_session import EVChargingSession
from db.models.trip_metrics import EVTripMetrics
from web.queries.costs import build_time_filter

# Adjustable without hunting through code
MOVING_AVG_WINDOW = 10

# DB values -> display labels
CHARGE_TYPE_LABELS = {"AC": "AC (L1/L2)", "DC": "DC Fast"}

# Shared Plotly modebar config — show minimal controls, hide logo
_PLOTLY_CONFIG = {
    "displayModeBar": True,
    "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"],
    "displaylogo": False,
}


def build_time_filter_trip(range_str: str):
    """Return a SQLAlchemy where clause for EVTripMetrics.start_time.

    Same logic as costs.build_time_filter but targets EVTripMetrics.start_time.
    Returns None for 'all' (no filter).
    Accepts: '7d', '30d', '90d', 'ytd', '1y', 'all'
    """
    if not range_str or range_str == "all":
        return None

    now = datetime.now(timezone.utc)

    if range_str == "7d":
        cutoff = now - timedelta(days=7)
    elif range_str == "30d":
        cutoff = now - timedelta(days=30)
    elif range_str == "90d":
        cutoff = now - timedelta(days=90)
    elif range_str == "ytd":
        cutoff = datetime(now.year, 1, 1, tzinfo=timezone.utc)
    elif range_str == "1y":
        cutoff = now - timedelta(days=365)
    else:
        return None

    return EVTripMetrics.start_time >= cutoff


async def query_energy_summary(db: AsyncSession, time_range: str = "all") -> dict:
    """Compute energy summary from EVChargingSession rows.

    Returns dict with:
    - total_kwh: float
    - total_sessions: int
    - avg_efficiency: float | None  (mi/kWh, base unit)
    - best_efficiency: float | None  (mi/kWh, base unit)
    - worst_efficiency: float | None  (mi/kWh, base unit)
    - by_charge_type: list of dicts [{charge_type, kwh, session_count}, ...]
    - sessions_for_chart: list of dicts [{date, efficiency_mi_kwh, charge_type}, ...]

    All efficiency values returned in mi/kWh (base unit).
    Route handler applies unit conversion factor before passing to template/chart.

    NOTE: Efficiency computed as miles_added / energy_kwh (NOT stored efficiency column).
    This gives 190/203 coverage vs 117/203 from the stored column.
    """
    # Reuse build_time_filter from costs (targets EVChargingSession.session_start_utc)
    stmt = select(EVChargingSession).where(EVChargingSession.energy_kwh.isnot(None))
    time_filter = build_time_filter(time_range)
    if time_filter is not None:
        stmt = stmt.where(time_filter)

    result = await db.execute(stmt)
    sessions = result.scalars().all()

    total_kwh = 0.0
    total_sessions = 0
    efficiencies: list[float] = []
    by_charge_type: dict[str, dict] = {}
    sessions_for_chart: list[dict] = []

    for s in sessions:
        total_sessions += 1
        kwh = float(s.energy_kwh)
        total_kwh += kwh

        # Group by charge type
        ct = s.charge_type if s.charge_type else "Unknown"
        if ct not in by_charge_type:
            by_charge_type[ct] = {"charge_type": ct, "kwh": 0.0, "session_count": 0}
        by_charge_type[ct]["kwh"] += kwh
        by_charge_type[ct]["session_count"] += 1

        # Compute efficiency — requires BOTH miles_added > 0 and energy_kwh > 0
        if (
            s.miles_added is not None
            and float(s.miles_added) > 0
            and kwh > 0
        ):
            eff = float(s.miles_added) / kwh
            efficiencies.append(eff)
            sessions_for_chart.append({
                "date": s.session_start_utc,
                "efficiency_mi_kwh": eff,
                "charge_type": ct,
            })

    avg_efficiency = sum(efficiencies) / len(efficiencies) if efficiencies else None
    best_efficiency = max(efficiencies) if efficiencies else None
    worst_efficiency = min(efficiencies) if efficiencies else None

    return {
        "total_kwh": total_kwh,
        "total_sessions": total_sessions,
        "avg_efficiency": avg_efficiency,
        "best_efficiency": best_efficiency,
        "worst_efficiency": worst_efficiency,
        "by_charge_type": list(by_charge_type.values()),
        "sessions_for_chart": sessions_for_chart,
    }


async def query_regen_summary(db: AsyncSession, time_range: str = "all") -> dict | None:
    """Compute regen braking summary from EVTripMetrics.

    Returns None when ev_trip_metrics has no rows with range_regenerated data
    (triggers "No data available" card state in the template).

    Returns dict with:
    - regen_total: float
    - trip_count: int

    NOTE: range_regenerated units are ambiguous — likely "miles of range recovered"
    but not confirmed. Template uses generic "range units" label.
    TODO: Validate range_regenerated units against raw fordpass API response.

    PITFALL: SUM() on empty/null data returns NULL not 0. Count-first guard prevents
    TypeError: float(None).
    """
    trip_filter = build_time_filter_trip(time_range)

    # Count-first guard: check rows with range_regenerated data
    count_stmt = select(func.count()).where(
        EVTripMetrics.range_regenerated.isnot(None)
    )
    if trip_filter is not None:
        count_stmt = count_stmt.where(trip_filter)

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

    sum_result = await db.execute(sum_stmt)
    row = sum_result.one()
    regen_total, trip_count = row

    return {
        "regen_total": float(regen_total) if regen_total is not None else 0.0,
        "trip_count": int(trip_count),
    }


async def query_regen_for_chart(
    db: AsyncSession, time_range: str = "all"
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

    result = await db.execute(stmt)
    rows = result.scalars().all()

    if not rows:
        return None

    return [
        {
            "date": r.start_time,
            "range_regenerated": float(r.range_regenerated),
        }
        for r in rows
    ]


async def query_monthly_energy(db: AsyncSession, time_range: str = "all") -> list[dict]:
    """Return monthly kWh grouped by charge type for stacked area chart.

    Returns list of dicts: [{"month": "2025-01", "charge_type": "AC", "kwh": 45.2}, ...]
    """
    stmt = select(EVChargingSession).where(EVChargingSession.energy_kwh.isnot(None))
    time_filter = build_time_filter(time_range)
    if time_filter is not None:
        stmt = stmt.where(time_filter)

    result = await db.execute(stmt)
    sessions = result.scalars().all()

    monthly: dict[tuple, float] = {}
    for s in sessions:
        if s.session_start_utc is None:
            continue
        month = s.session_start_utc.strftime("%Y-%m")
        ct = s.charge_type or "Unknown"
        key = (month, ct)
        monthly[key] = monthly.get(key, 0.0) + float(s.energy_kwh or 0)

    return [
        {"month": month, "charge_type": ct, "kwh": kwh}
        for (month, ct), kwh in sorted(monthly.items())
    ]


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
        margin=dict(l=20, r=20, t=20, b=20),
        yaxis_title="kWh",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )

    return fig.to_html(full_html=False, include_plotlyjs=False, config=_PLOTLY_CONFIG)


def build_efficiency_chart(
    sessions: list[dict],
    regen_data: list[dict] | None,
    unit_label: str = "mi/kWh",
    unit_factor: float = 1.0,
) -> str:
    """Build Plotly efficiency trend scatter chart with rolling average overlay.

    Args:
        sessions: List of {date, efficiency_mi_kwh, charge_type} dicts from query_energy_summary.
        regen_data: Optional list of {date, range_regenerated} dicts for secondary y-axis.
                    Pass None when no regen data available (expected for seeded dataset).
        unit_label: Axis label (e.g. 'mi/kWh' or 'km/kWh').
        unit_factor: Conversion multiplier (1.0 for US, 1.60934 for EU).

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
    df = df.sort_values("date").dropna(subset=["efficiency_mi_kwh"])

    # Apply unit conversion factor
    df["efficiency"] = df["efficiency_mi_kwh"] * unit_factor

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

        # Regen on secondary y-axis
        regen_df = pd.DataFrame(regen_data).sort_values("date")
        fig.add_trace(
            go.Scatter(
                x=regen_df["date"],
                y=regen_df["range_regenerated"],
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
        margin=dict(l=20, r=20, t=20, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        yaxis_title=unit_label,
    )

    return fig.to_html(full_html=False, include_plotlyjs=False, config=_PLOTLY_CONFIG)
