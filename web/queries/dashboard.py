"""Dashboard query layer and chart builders.

Provides summary aggregation for the landing dashboard page.
"""
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.charging_session import EVChargingSession
from web.queries.costs import (
    compute_session_cost,
    get_networks_by_name,
    query_cost_summary,
)

# Shared Plotly modebar config — show minimal controls, hide logo
_PLOTLY_CONFIG = {
    "displayModeBar": True,
    "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"],
    "displaylogo": False,
}


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
                hovertemplate="<b>%{label}</b><br>%{value:.1f} kWh (%{percent})<extra></extra>",
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
        hovermode="closest",
    )
    return fig.to_html(full_html=False, include_plotlyjs=False, config=_PLOTLY_CONFIG)


def build_cumulative_energy_chart(
    sessions: list,
    network_colors: dict[str, str] | None = None,
) -> str:
    """Build a Plotly area chart showing cumulative kWh over time.

    Shows running total of energy consumed, colored by network when possible.

    Args:
        sessions: List of EVChargingSession ORM objects.
        network_colors: Optional dict (unused for area, reserved for future use).

    Returns:
        HTML div string (include_plotlyjs=False). Empty string if no data.
    """
    if not sessions:
        return ""

    pio.templates.default = "plotly_dark"

    data_points = []
    for s in sessions:
        if s.session_start_utc is None or s.energy_kwh is None:
            continue
        data_points.append({
            "date": s.session_start_utc,
            "kwh": float(s.energy_kwh),
        })

    if not data_points:
        return ""

    df = pd.DataFrame(data_points).sort_values("date")
    df["cumulative_kwh"] = df["kwh"].cumsum()

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df["date"],
            y=df["cumulative_kwh"],
            mode="lines",
            fill="tozeroy",
            name="Cumulative kWh",
            line=dict(color="#60a5fa", width=2),
            fillcolor="rgba(96, 165, 250, 0.15)",
            hovertemplate="<b>%{x|%b %d, %Y}</b><br>Total: %{y:.1f} kWh<extra></extra>",
        )
    )

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=20, r=20, t=20, b=20),
        yaxis_title="Total kWh",
        xaxis_title="",
        showlegend=False,
        hovermode="x unified",
    )

    return fig.to_html(full_html=False, include_plotlyjs=False, config=_PLOTLY_CONFIG)
