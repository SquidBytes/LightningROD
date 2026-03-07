from datetime import datetime, timezone, timedelta
from typing import Optional

import pandas as pd
import plotly.express as px
import plotly.io as pio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.charging_session import EVChargingSession
from db.models.reference import EVChargingNetwork, EVLocationLookup

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


def build_time_filter(range_str: str):
    """Return a SQLAlchemy where clause for EVChargingSession.session_start_utc.

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

    return EVChargingSession.session_start_utc >= cutoff


def compute_session_cost(
    session,
    network=None,
    location=None,
    *,
    networks_by_name: dict = None,
) -> dict:
    """Compute display cost for a session using the cost hierarchy cascade.

    Supports both new-style and old-style call signatures:
    - New: compute_session_cost(session, network=net_obj, location=loc_obj)
    - Old: compute_session_cost(session, networks_by_name)  (positional dict)
    - Old: compute_session_cost(session, networks_by_name=name_dict)  (keyword)

    Cost cascade order:
    1. Session is_free flag
    2. Stored user cost (cost_source='manual' or 'imported')
    3. Location cost_per_kwh override (if location has cost_per_kwh set)
    4. Network cost_per_kwh (from network FK)
    5. No cost data available

    NOTE: Callers in sessions.py, comparisons.py, dashboard.py still use
    old positional dict signature. Plan 03 will update them.

    Returns dict with keys:
    - display_cost: float|None
    - cost_source: str|None
    - is_free: bool
    - cost_per_kwh: float|None
    - calculation: str|None
    - estimated_cost: float|None  (always calculated from hierarchy)
    - actual_cost_per_kwh: float|None  (session.cost / energy_kwh when both exist)
    - cost_difference: float|None  (session.cost - estimated_cost when both exist)
    """
    # Backward compat: if network arg is actually a dict, treat as networks_by_name
    if isinstance(network, dict):
        networks_by_name = network
        network = None

    # Resolve network from networks_by_name if not passed directly
    if network is None and networks_by_name is not None:
        if session.location_name and session.location_name in networks_by_name:
            network = networks_by_name[session.location_name]

    result = {
        "display_cost": None,
        "cost_source": None,
        "is_free": False,
        "cost_per_kwh": None,
        "calculation": None,
        "estimated_cost": None,
        "actual_cost_per_kwh": None,
        "cost_difference": None,
    }

    energy_kwh = float(session.energy_kwh or 0)

    # --- Compute estimated_cost from hierarchy (location -> network -> none) ---
    estimated_cost = None
    estimated_source = None

    if location and location.cost_per_kwh:
        cost_val = float(location.cost_per_kwh)
        estimated_cost = energy_kwh * cost_val
        estimated_source = "location"
    elif network and not network.is_free and network.cost_per_kwh:
        cost_val = float(network.cost_per_kwh)
        estimated_cost = energy_kwh * cost_val
        estimated_source = "network"

    if estimated_cost is not None:
        result["estimated_cost"] = round(estimated_cost, 4)

    # --- Compute actual_cost_per_kwh from user cost ---
    if session.cost is not None and energy_kwh > 0:
        result["actual_cost_per_kwh"] = round(float(session.cost) / energy_kwh, 4)

    # --- Compute cost_difference ---
    if session.cost is not None and estimated_cost is not None:
        result["cost_difference"] = round(float(session.cost) - estimated_cost, 4)

    # --- Determine display_cost using cascade ---

    # (a) Session-level is_free flag
    if session.is_free:
        result["display_cost"] = 0.0
        result["cost_source"] = "calculated"
        result["is_free"] = True
        return result

    # (b) Stored cost (manual or imported) always takes priority for display
    if session.cost is not None:
        result["display_cost"] = float(session.cost)
        result["cost_source"] = session.cost_source or "imported"
        return result

    # (c) Network-level is_free
    if network and network.is_free:
        result["display_cost"] = 0.0
        result["cost_source"] = "calculated"
        result["is_free"] = True
        return result

    # (d) Location cost override
    if location and location.cost_per_kwh:
        cost_val = float(location.cost_per_kwh)
        result["display_cost"] = round(energy_kwh * cost_val, 4)
        result["cost_source"] = "calculated"
        result["cost_per_kwh"] = cost_val
        result["calculation"] = f"{energy_kwh} kWh x ${cost_val}/kWh (location)"
        return result

    # (e) Network cost
    if network and network.cost_per_kwh:
        cost_val = float(network.cost_per_kwh)
        result["display_cost"] = round(energy_kwh * cost_val, 4)
        result["cost_source"] = "calculated"
        result["cost_per_kwh"] = cost_val
        result["calculation"] = f"{energy_kwh} kWh x ${cost_val}/kWh"
        return result

    # (f) No cost data available
    return result


async def get_networks_by_name(db: AsyncSession) -> dict[str, EVChargingNetwork]:
    """Return dict of network_name -> EVChargingNetwork for all networks."""
    result = await db.execute(select(EVChargingNetwork))
    networks = result.scalars().all()
    return {network.network_name: network for network in networks}


async def get_networks_by_id(db: AsyncSession) -> dict[int, EVChargingNetwork]:
    """Return dict of network_id -> EVChargingNetwork for all networks."""
    result = await db.execute(select(EVChargingNetwork))
    networks = result.scalars().all()
    return {network.id: network for network in networks}


async def get_locations_by_id(
    db: AsyncSession, location_ids: list[int]
) -> dict[int, EVLocationLookup]:
    """Return dict of location_id -> EVLocationLookup for given IDs."""
    if not location_ids:
        return {}
    result = await db.execute(
        select(EVLocationLookup).where(EVLocationLookup.id.in_(location_ids))
    )
    locations = result.scalars().all()
    return {loc.id: loc for loc in locations}


async def get_session_cost_context(
    db: AsyncSession, session
) -> tuple[Optional[EVChargingNetwork], Optional[EVLocationLookup]]:
    """Load the network and location objects for a session's cost calculation.

    Returns (network, location) tuple, either or both may be None.
    """
    network = None
    location = None
    if session.network_id:
        result = await db.execute(
            select(EVChargingNetwork).where(EVChargingNetwork.id == session.network_id)
        )
        network = result.scalar_one_or_none()
    if session.location_id:
        result = await db.execute(
            select(EVLocationLookup).where(EVLocationLookup.id == session.location_id)
        )
        location = result.scalar_one_or_none()
    return network, location


async def query_cost_summary(db: AsyncSession, time_range: str = "all") -> dict:
    """Compute lifetime (or time-filtered) cost summary aggregated by network.

    Uses network_id FK lookup with location cost cascade.

    Returns dict with:
    - total_cost: float
    - free_total_kwh: float
    - free_session_count: int
    - by_network: list of dicts [{network, total_cost, session_count, total_kwh}, ...]
    - unconfigured_count: int
    - total_sessions: int
    - total_kwh: float
    """
    networks_by_id = await get_networks_by_id(db)

    stmt = select(EVChargingSession)
    time_filter = build_time_filter(time_range)
    if time_filter is not None:
        stmt = stmt.where(time_filter)

    result = await db.execute(stmt)
    sessions = result.scalars().all()

    # Pre-load locations for sessions that have location_id
    location_ids = [s.location_id for s in sessions if s.location_id]
    locations_by_id = await get_locations_by_id(db, location_ids)

    total_cost = 0.0
    free_total_kwh = 0.0
    free_session_count = 0
    unconfigured_count = 0
    total_sessions = 0
    total_kwh = 0.0
    actual_total_cost = 0.0
    estimated_total_cost = 0.0
    actual_session_count = 0
    estimated_session_count = 0
    by_network: dict[str, dict] = {}

    for s in sessions:
        network = networks_by_id.get(s.network_id) if s.network_id else None
        location = locations_by_id.get(s.location_id) if s.location_id else None
        cost_info = compute_session_cost(s, network=network, location=location)

        if cost_info["display_cost"] is None:
            unconfigured_count += 1
            continue

        # Session has a resolved cost (including $0 free)
        total_sessions += 1
        kwh = float(s.energy_kwh or 0)
        total_kwh += kwh
        total_cost += cost_info["display_cost"]

        if cost_info["is_free"]:
            free_total_kwh += kwh
            free_session_count += 1

        # Track actual vs estimated
        if s.cost is not None and s.cost_source in ("manual", "imported"):
            actual_total_cost += float(s.cost)
            actual_session_count += 1
        elif s.estimated_cost is not None:
            estimated_total_cost += float(s.estimated_cost)
            estimated_session_count += 1
        elif cost_info["display_cost"] is not None:
            # Calculated cost (from compute_session_cost network/location lookup)
            estimated_total_cost += cost_info["display_cost"]
            estimated_session_count += 1

        # Group by network name (from FK) or fallback
        net_name = network.network_name if network else (s.location_name or s.location_type or "Unknown")
        if net_name not in by_network:
            by_network[net_name] = {
                "network": net_name,
                "total_cost": 0.0,
                "session_count": 0,
                "total_kwh": 0.0,
            }
        by_network[net_name]["total_cost"] += cost_info["display_cost"]
        by_network[net_name]["session_count"] += 1
        by_network[net_name]["total_kwh"] += kwh

    return {
        "total_cost": total_cost,
        "free_total_kwh": free_total_kwh,
        "free_session_count": free_session_count,
        "by_network": list(by_network.values()),
        "unconfigured_count": unconfigured_count,
        "total_sessions": total_sessions,
        "total_kwh": total_kwh,
        "actual_total_cost": actual_total_cost,
        "estimated_total_cost": estimated_total_cost,
        "actual_session_count": actual_session_count,
        "estimated_session_count": estimated_session_count,
    }


async def query_monthly_costs(db: AsyncSession, time_range: str = "all") -> list[dict]:
    """Return monthly cost data grouped by month and network.

    Uses network_id FK lookup with location cost cascade.

    Returns list of dicts: [{"month": "2025-01", "network": "Home", "cost": 12.50}, ...]
    """
    networks_by_id = await get_networks_by_id(db)

    stmt = select(EVChargingSession)
    time_filter = build_time_filter(time_range)
    if time_filter is not None:
        stmt = stmt.where(time_filter)

    result = await db.execute(stmt)
    sessions = result.scalars().all()

    # Pre-load locations for sessions that have location_id
    location_ids = [s.location_id for s in sessions if s.location_id]
    locations_by_id = await get_locations_by_id(db, location_ids)

    # Accumulate into {(month, network): cost}
    monthly: dict[tuple, float] = {}

    for s in sessions:
        network = networks_by_id.get(s.network_id) if s.network_id else None
        location = locations_by_id.get(s.location_id) if s.location_id else None
        cost_info = compute_session_cost(s, network=network, location=location)
        if cost_info["display_cost"] is None:
            continue
        if s.session_start_utc is None:
            continue

        month = s.session_start_utc.strftime("%Y-%m")
        net_name = network.network_name if network else (s.location_name or s.location_type or "Unknown")
        key = (month, net_name)
        monthly[key] = monthly.get(key, 0.0) + cost_info["display_cost"]

    return [
        {"month": month, "network": network, "cost": cost}
        for (month, network), cost in sorted(monthly.items())
    ]


def build_network_cost_chart(by_network: list[dict], network_colors: dict[str, str] = None) -> str:
    """Build a Plotly horizontal bar chart of cost by network, returning HTML div string.

    Args:
        by_network: List of dicts with keys: network, total_cost, session_count, total_kwh.
        network_colors: Optional dict mapping network name to hex color string.
    """
    if not by_network:
        return ""

    pio.templates.default = "plotly_dark"

    df = pd.DataFrame(by_network)
    kwargs = dict(x="total_cost", y="network", color="network", orientation="h")
    if network_colors:
        kwargs["color_discrete_map"] = network_colors
    fig = px.bar(df, **kwargs)
    fig.update_traces(hovertemplate="<b>%{y}</b><br>$%{x:.2f}<extra></extra>")
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#e5e7eb",
        showlegend=False,
        margin=dict(l=20, r=20, t=10, b=20),
        xaxis_tickprefix="$",
        yaxis_title="",
        hoverlabel=_HOVER_LABEL,
    )
    return _wrap_chart(fig.to_html(full_html=False, include_plotlyjs=False, config=_PLOTLY_CONFIG))


def build_monthly_cost_chart(monthly_data: list[dict], network_colors: dict[str, str] = None) -> str:
    """Build a Plotly stacked bar chart of cost by month and network.

    Args:
        monthly_data: List of dicts with keys: month, network, cost.
        network_colors: Optional dict mapping network name to hex color string.
    """
    if not monthly_data:
        return ""

    pio.templates.default = "plotly_dark"

    df = pd.DataFrame(monthly_data)
    kwargs = dict(x="month", y="cost", color="network", barmode="stack")
    if network_colors:
        kwargs["color_discrete_map"] = network_colors
    fig = px.bar(df, **kwargs)
    fig.update_traces(hovertemplate="<b>%{data.name}</b><br>%{x}: $%{y:.2f}<extra></extra>")
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#e5e7eb",
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=-0.25, xanchor="center", x=0.5),
        margin=dict(l=20, r=20, t=10, b=20),
        yaxis_tickprefix="$",
        xaxis_title="",
        yaxis_title="Cost ($)",
        hovermode="x unified",
        hoverlabel=_HOVER_LABEL,
    )
    return _wrap_chart(fig.to_html(full_html=False, include_plotlyjs=False, config=_PLOTLY_CONFIG))
