from datetime import datetime, timezone, timedelta
from typing import Optional

import pandas as pd
import plotly.express as px
import plotly.io as pio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.charging_session import EVChargingSession
from db.models.reference import EVChargingNetwork


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


def compute_session_cost(session, networks_by_name: dict) -> dict:
    """Compute display cost for a session given a networks_by_name dict.

    Pure logic function — not a DB query.

    Returns dict with keys:
    - display_cost: float|None
    - cost_source: str|None
    - is_free: bool
    - cost_per_kwh: float|None
    - calculation: str|None
    """
    result = {
        "display_cost": None,
        "cost_source": None,
        "is_free": False,
        "cost_per_kwh": None,
        "calculation": None,
    }

    # (a) Manual entry — use stored cost
    if session.cost_source == "manual":
        result["display_cost"] = float(session.cost) if session.cost is not None else 0.0
        result["cost_source"] = "manual"
        return result

    # (b) Imported cost — use stored cost if present
    if session.cost_source == "imported" and session.cost is not None:
        result["display_cost"] = float(session.cost)
        result["cost_source"] = "imported"
        return result

    # (c) Network lookup by location_name
    if session.location_name and session.location_name in networks_by_name:
        network = networks_by_name[session.location_name]
        if network.is_free:
            result["display_cost"] = 0.0
            result["cost_source"] = "calculated"
            result["is_free"] = True
            return result
        else:
            kwh = float(session.energy_kwh or 0)
            cost_val = float(network.cost_per_kwh or 0)
            calculated = kwh * cost_val
            result["display_cost"] = calculated
            result["cost_source"] = "calculated"
            result["cost_per_kwh"] = cost_val
            result["calculation"] = f"{kwh} kWh x ${cost_val}/kWh"
            return result

    # (d) Session-level is_free flag
    if session.is_free:
        result["display_cost"] = 0.0
        result["cost_source"] = "calculated"
        result["is_free"] = True
        return result

    # (e) No network match — excluded from totals
    return result


async def get_networks_by_name(db: AsyncSession) -> dict[str, EVChargingNetwork]:
    """Return dict of network_name -> EVChargingNetwork for all networks."""
    result = await db.execute(select(EVChargingNetwork))
    networks = result.scalars().all()
    return {network.network_name: network for network in networks}


async def query_cost_summary(db: AsyncSession, time_range: str = "all") -> dict:
    """Compute lifetime (or time-filtered) cost summary aggregated by network.

    Returns dict with:
    - total_cost: float
    - free_total_kwh: float
    - free_session_count: int
    - by_network: list of dicts [{network, total_cost, session_count, total_kwh}, ...]
    - unconfigured_count: int
    - total_sessions: int
    - total_kwh: float
    """
    networks_by_name = await get_networks_by_name(db)

    stmt = select(EVChargingSession)
    time_filter = build_time_filter(time_range)
    if time_filter is not None:
        stmt = stmt.where(time_filter)

    result = await db.execute(stmt)
    sessions = result.scalars().all()

    total_cost = 0.0
    free_total_kwh = 0.0
    free_session_count = 0
    unconfigured_count = 0
    total_sessions = 0
    total_kwh = 0.0
    by_network: dict[str, dict] = {}

    for s in sessions:
        cost_info = compute_session_cost(s, networks_by_name)

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

        # Group by network (location_name or fallback)
        network = s.location_name or s.location_type or "Unknown"
        if network not in by_network:
            by_network[network] = {
                "network": network,
                "total_cost": 0.0,
                "session_count": 0,
                "total_kwh": 0.0,
            }
        by_network[network]["total_cost"] += cost_info["display_cost"]
        by_network[network]["session_count"] += 1
        by_network[network]["total_kwh"] += kwh

    return {
        "total_cost": total_cost,
        "free_total_kwh": free_total_kwh,
        "free_session_count": free_session_count,
        "by_network": list(by_network.values()),
        "unconfigured_count": unconfigured_count,
        "total_sessions": total_sessions,
        "total_kwh": total_kwh,
    }


async def query_monthly_costs(db: AsyncSession, time_range: str = "all") -> list[dict]:
    """Return monthly cost data grouped by month and network.

    Returns list of dicts: [{"month": "2025-01", "network": "Home", "cost": 12.50}, ...]
    """
    networks_by_name = await get_networks_by_name(db)

    stmt = select(EVChargingSession)
    time_filter = build_time_filter(time_range)
    if time_filter is not None:
        stmt = stmt.where(time_filter)

    result = await db.execute(stmt)
    sessions = result.scalars().all()

    # Accumulate into {(month, network): cost}
    monthly: dict[tuple, float] = {}

    for s in sessions:
        cost_info = compute_session_cost(s, networks_by_name)
        if cost_info["display_cost"] is None:
            continue
        if s.session_start_utc is None:
            continue

        month = s.session_start_utc.strftime("%Y-%m")
        network = s.location_name or s.location_type or "Unknown"
        key = (month, network)
        monthly[key] = monthly.get(key, 0.0) + cost_info["display_cost"]

    return [
        {"month": month, "network": network, "cost": cost}
        for (month, network), cost in sorted(monthly.items())
    ]


def build_network_cost_chart(by_network: list[dict]) -> str:
    """Build a Plotly bar chart of cost by network, returning HTML div string."""
    if not by_network:
        return ""

    pio.templates.default = "plotly_dark"

    df = pd.DataFrame(by_network)
    fig = px.bar(df, x="network", y="total_cost", color="network")
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
        margin=dict(l=20, r=20, t=20, b=20),
        yaxis_tickprefix="$",
    )
    return fig.to_html(full_html=False, include_plotlyjs=False)


def build_monthly_cost_chart(monthly_data: list[dict]) -> str:
    """Build a Plotly stacked bar chart of cost by month and network."""
    if not monthly_data:
        return ""

    pio.templates.default = "plotly_dark"

    df = pd.DataFrame(monthly_data)
    fig = px.bar(df, x="month", y="cost", color="network", barmode="stack")
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        showlegend=True,
        margin=dict(l=20, r=20, t=20, b=20),
        yaxis_tickprefix="$",
        xaxis_title="",
        yaxis_title="Cost ($)",
    )
    return fig.to_html(full_html=False, include_plotlyjs=False)
