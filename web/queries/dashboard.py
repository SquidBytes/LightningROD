"""Dashboard query layer and chart builders.

Provides summary aggregation for the landing dashboard page.
"""
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.charging_session import EVChargingSession
from web.queries.costs import (
    compute_session_cost,
    get_networks_by_name,
    query_cost_summary,
)

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


async def query_charging_efficiency(db: AsyncSession) -> dict:
    """Aggregate charging efficiency metrics from sessions with EVSE data.

    Returns dict with:
    - sessions_with_evse: int (count of sessions with evse_energy_kwh)
    - total_loss_kwh: float (sum of evse_energy_kwh - energy_kwh)
    - avg_loss_pct: float | None (average loss percentage)
    - avg_utilization_pct: float | None (average max_power/charger_rated_kw)
    """
    # Loss metrics: sessions with both evse_energy_kwh and energy_kwh
    loss_result = await db.execute(
        select(EVChargingSession).where(
            and_(
                EVChargingSession.evse_energy_kwh.isnot(None),
                EVChargingSession.energy_kwh.isnot(None),
                EVChargingSession.evse_energy_kwh > 0,
            )
        )
    )
    loss_sessions = loss_result.scalars().all()

    sessions_with_evse = len(loss_sessions)
    total_loss_kwh = 0.0
    loss_pct_sum = 0.0

    for s in loss_sessions:
        evse_e = float(s.evse_energy_kwh)
        veh_e = float(s.energy_kwh)
        loss = evse_e - veh_e
        total_loss_kwh += loss
        loss_pct_sum += (loss / evse_e) * 100

    avg_loss_pct = loss_pct_sum / sessions_with_evse if sessions_with_evse > 0 else None

    # Utilization metrics: sessions with max power and charger_rated_kw
    util_result = await db.execute(
        select(EVChargingSession).where(
            and_(
                EVChargingSession.charger_rated_kw.isnot(None),
                EVChargingSession.charger_rated_kw > 0,
            )
        )
    )
    util_sessions = util_result.scalars().all()

    util_pct_sum = 0.0
    util_count = 0
    for s in util_sessions:
        max_pwr = float(s.evse_max_power_kw) if s.evse_max_power_kw is not None else (
            float(s.max_power) if s.max_power is not None else None
        )
        if max_pwr is not None:
            util_pct_sum += (max_pwr / float(s.charger_rated_kw)) * 100
            util_count += 1

    avg_utilization_pct = util_pct_sum / util_count if util_count > 0 else None

    return {
        "sessions_with_evse": sessions_with_evse,
        "total_loss_kwh": total_loss_kwh,
        "avg_loss_pct": avg_loss_pct,
        "avg_utilization_pct": avg_utilization_pct,
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
        font_color="#e5e7eb",
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5),
        margin=dict(l=20, r=20, t=20, b=40),
        hovermode="closest",
        hoverlabel=_HOVER_LABEL,
    )
    return _wrap_chart(fig.to_html(full_html=False, include_plotlyjs=False, config=_PLOTLY_CONFIG))


def build_monthly_energy_by_network_chart(
    sessions: list,
    network_id_to_name: dict[int, str] | None = None,
    network_colors: dict[str, str] | None = None,
) -> str:
    """Build a stacked bar chart of monthly kWh broken down by network.

    Args:
        sessions: List of EVChargingSession ORM objects.
        network_id_to_name: Dict mapping network_id -> network name.
        network_colors: Dict mapping network name -> hex color string.

    Returns:
        HTML div string (include_plotlyjs=False). Empty string if no data.
    """
    if not sessions:
        return ""

    import plotly.express as px

    pio.templates.default = "plotly_dark"

    id_to_name = network_id_to_name or {}

    data_points = []
    for s in sessions:
        if s.session_start_utc is None or s.energy_kwh is None:
            continue
        network = id_to_name.get(s.network_id, "Unknown") if s.network_id else "Unknown"
        month = s.session_start_utc.strftime("%Y-%m")
        data_points.append({"month": month, "network": network, "kwh": float(s.energy_kwh)})

    if not data_points:
        return ""

    df = pd.DataFrame(data_points)
    df = df.groupby(["month", "network"], as_index=False)["kwh"].sum()
    df = df.sort_values("month")

    kwargs = dict(x="month", y="kwh", color="network", barmode="stack")
    if network_colors:
        kwargs["color_discrete_map"] = network_colors
    fig = px.bar(df, **kwargs)
    fig.update_traces(hovertemplate="<b>%{data.name}</b><br>%{x}: %{y:.1f} kWh<extra></extra>")
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#e5e7eb",
        margin=dict(l=20, r=20, t=10, b=20),
        yaxis_title="kWh",
        xaxis_title="",
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=-0.25, xanchor="center", x=0.5),
        hovermode="x unified",
        hoverlabel=_HOVER_LABEL,
    )

    return _wrap_chart(fig.to_html(full_html=False, include_plotlyjs=False, config=_PLOTLY_CONFIG))
