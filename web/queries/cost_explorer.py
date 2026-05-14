"""Cost Explorer aggregator — single-query backend for the /charging/costs Cost Explorer card."""

from datetime import date
from typing import Any

import plotly.graph_objects as go
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from db.models.charging_session import EVChargingSession
from db.models.reference import EVNetworkSubscription
from web.queries.costs import (
    _HOVER_LABEL,
    _PLOTLY_CONFIG,
    _wrap_chart,
    build_time_filter,
    calculate_monthly_fees_in_range,  # noqa: F401  (re-exported for callers wanting the legacy total)
    compute_session_cost,
    find_active_subscription,  # noqa: F401
    get_locations_by_id,
    get_networks_by_id,
)
from web.queries.settings import get_all_subscriptions_by_network


def calculate_fee_breakdown(
    periods: list[EVNetworkSubscription],
    range_start: date,
    range_end: date,
) -> list[dict[str, Any]]:
    """Per-period fee breakdown for the Cost Explorer subscription block.

    Returns one dict per period overlapping [range_start, range_end]:
    `{"months": int, "fee_per_month": float, "fee_total": float, "period": EVNetworkSubscription}`.
    Months are counted with the same overlap rule as `calculate_monthly_fees_in_range`:
    any calendar month that has any overlap with both the range and the period counts
    as one full month.
    """
    breakdown: list[dict[str, Any]] = []
    for p in periods:
        if p.monthly_fee is None or float(p.monthly_fee) == 0.0:
            continue
        p_start = p.start_date
        p_end = p.end_date if p.end_date is not None else range_end
        window_start = max(p_start, range_start)
        window_end = min(p_end, range_end)
        if window_start > window_end:
            continue
        months = (window_end.year - window_start.year) * 12 + (window_end.month - window_start.month) + 1
        fee = float(p.monthly_fee)
        breakdown.append({
            "months": months,
            "fee_per_month": fee,
            "fee_total": round(months * fee, 2),
            "period": p,
        })
    return breakdown


def build_cost_explorer_monthly_chart(
    monthly_trend: list[dict[str, Any]],
    *,
    reference_network_name: str | None = None,
) -> str:
    """Two-trace line chart for the Cost Explorer monthly strip — solid actual, dashed reference.

    Returns "" when `monthly_trend` is empty so the template can use a truthiness check.
    """
    if not monthly_trend:
        return ""

    x_vals = [row["month"] for row in monthly_trend]
    paid_vals = [row["paid"] for row in monthly_trend]
    ref_vals = [row["at_reference"] for row in monthly_trend]

    ref_name = f"At reference ({reference_network_name})" if reference_network_name else "At reference rate"

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=x_vals,
            y=paid_vals,
            mode="lines+markers",
            name="You paid",
            line=dict(color="#47A8E5", width=2),
            marker=dict(size=4),
            hovertemplate="<b>%{x}</b><br>You paid: $%{y:.2f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=x_vals,
            y=ref_vals,
            mode="lines",
            name=ref_name,
            line=dict(color="#94a3b8", width=1.5, dash="dash"),
            hovertemplate="<b>%{x}</b><br>" + ref_name + ": $%{y:.2f}<extra></extra>",
        )
    )
    fig.update_layout(
        height=280,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#e5e7eb",
        margin=dict(l=20, r=20, t=10, b=20),
        xaxis=dict(title=""),
        yaxis=dict(title="Cost ($)", tickprefix="$"),
        hovermode="x unified",
        hoverlabel=_HOVER_LABEL,
        legend=dict(orientation="h", yanchor="bottom", y=-0.25, xanchor="center", x=0.5),
    )
    return _wrap_chart(fig.to_html(full_html=False, include_plotlyjs=False, config=_PLOTLY_CONFIG))
