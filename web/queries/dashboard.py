"""Dashboard query layer and chart builders.

Provides summary aggregation for the landing dashboard page.
Reuses chart builders from costs and energy queries where possible.
"""
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.charging_session import EVChargingSession
from web.queries.costs import (
    build_monthly_cost_chart,
    compute_session_cost,
    get_networks_by_name,
    query_cost_summary,
    query_monthly_costs,
)

MOVING_AVG_WINDOW = 10


async def query_dashboard_summary(db: AsyncSession) -> dict:
    """Aggregate lifetime charging data for dashboard summary cards.

    Returns dict with:
    - total_sessions: int   (all sessions in DB, regardless of cost resolution)
    - total_kwh: float      (sum of energy_kwh across all sessions)
    - total_cost: float     (sum of display_cost for sessions with a resolved cost)
    - avg_cost_per_session: float | None
    - avg_kwh_per_session: float | None
    """
    networks_by_name = await get_networks_by_name(db)

    result = await db.execute(select(EVChargingSession))
    sessions = result.scalars().all()

    total_sessions = len(sessions)
    total_kwh = sum(float(s.energy_kwh or 0) for s in sessions)

    # Cost totals only for sessions with a resolved cost
    total_cost = 0.0
    cost_session_count = 0
    for s in sessions:
        cost_info = compute_session_cost(s, networks_by_name)
        if cost_info["display_cost"] is not None:
            total_cost += cost_info["display_cost"]
            cost_session_count += 1

    avg_cost_per_session = (
        total_cost / cost_session_count if cost_session_count > 0 else None
    )
    avg_kwh_per_session = (
        total_kwh / total_sessions if total_sessions > 0 else None
    )

    return {
        "total_sessions": total_sessions,
        "total_kwh": total_kwh,
        "total_cost": total_cost,
        "avg_cost_per_session": avg_cost_per_session,
        "avg_kwh_per_session": avg_kwh_per_session,
    }


def build_energy_by_network_chart(
    by_network: list[dict],
    network_colors: dict[str, str] | None = None,
) -> str:
    """Build a Plotly donut chart showing kWh breakdown by network.

    Args:
        by_network: List of dicts from query_cost_summary — each has
                    {network, total_kwh, session_count, total_cost}.
        network_colors: Optional dict mapping network name -> hex color string.
                        When provided, chart markers use these colors.

    Returns:
        HTML div string (include_plotlyjs=False). Empty string if no data.
    """
    if not by_network:
        return ""

    # Filter out zero-kWh entries to keep donut readable
    filtered = [row for row in by_network if row.get("total_kwh", 0) > 0]
    if not filtered:
        return ""

    pio.templates.default = "plotly_dark"

    labels = [row["network"] for row in filtered]
    values = [row["total_kwh"] for row in filtered]

    marker_kwargs: dict = {}
    if network_colors:
        colors = [network_colors.get(label) for label in labels]
        # Only set colors if we have at least one resolved color
        if any(c for c in colors):
            # Replace None with a default gray
            colors = [c if c else "#6B7280" for c in colors]
            marker_kwargs["marker"] = dict(colors=colors)

    fig = go.Figure(
        data=[
            go.Pie(
                labels=labels,
                values=values,
                hole=0.45,
                textinfo="percent+label",
                **marker_kwargs,
            )
        ]
    )
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5),
        margin=dict(l=20, r=20, t=20, b=40),
    )
    return fig.to_html(full_html=False, include_plotlyjs=False)


def build_dashboard_efficiency_chart(
    sessions: list,
    unit_label: str = "mi/kWh",
    unit_factor: float = 1.0,
) -> str:
    """Build a Plotly scatter+line efficiency trend chart from raw session objects.

    Args:
        sessions: List of EVChargingSession ORM objects. Sessions without
                  miles_added or energy_kwh are skipped automatically.
        unit_label: Y-axis label (e.g. 'mi/kWh' or 'km/kWh').
        unit_factor: Conversion multiplier (1.0 for US, 1.60934 for EU).

    Returns:
        HTML div string (include_plotlyjs=False). Empty string if no valid sessions.
    """
    # Build data points from raw session objects
    data_points = []
    for s in sessions:
        if s.energy_kwh is None or float(s.energy_kwh) == 0:
            continue
        if s.miles_added is None:
            continue
        eff = float(s.miles_added) / float(s.energy_kwh) * unit_factor
        data_points.append(
            {
                "date": s.session_start_utc,
                "efficiency": eff,
            }
        )

    if not data_points:
        return ""

    pio.templates.default = "plotly_dark"

    df = pd.DataFrame(data_points)
    df = df.sort_values("date").dropna(subset=["efficiency"])

    window = min(MOVING_AVG_WINDOW, len(df))
    df["rolling_avg"] = df["efficiency"].rolling(window=window, min_periods=1).mean()

    fig = px.scatter(
        df,
        x="date",
        y="efficiency",
        labels={"efficiency": unit_label, "date": ""},
        color_discrete_sequence=["#60a5fa"],
    )

    # Add rolling average line
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

    return fig.to_html(full_html=False, include_plotlyjs=False)
