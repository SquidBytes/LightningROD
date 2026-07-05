"""Charging-performance analytics routes."""

from typing import Annotated

import plotly.graph_objects as go
import plotly.io as pio
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from web.dependencies import get_db
from web.queries.energy import (
    CHARGE_TYPE_LABELS,
    build_charge_type_donut_chart,
    build_charging_speed_chart,
    build_efficiency_chart,
    build_efficiency_over_time_chart,
    build_energy_over_time_chart,
    build_monthly_energy_chart,
    build_range_regen_over_time_chart,
    build_synthetic_charge_curve_chart,
    charging_speed_series,
    efficiency_over_time_series,
    has_real_charge_curve_data,
    monthly_energy_series,
    query_energy_summary,
    query_monthly_energy,
    query_regen_for_chart,
    query_synthetic_curve_inputs,
)
from web.queries.settings import get_unit_context
from web.queries.vehicles import (
    get_active_device_id,
    get_active_vehicle,
    get_all_vehicles,
)
from web.unit_system import MI_PER_KM

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")

# Sparkline defaults — axes stripped, 72px tall, no modebar.
_SPARKLINE_HEIGHT = 72
_SPARKLINE_CONFIG = {"displayModeBar": False, "responsive": True}
_SPARKLINE_EMPTY_HTML = (
    '<p class="text-xs text-base-content/40">No trend data</p>'
)


def _build_sparkline(xs, ys, line_color: str = "#47A8E5") -> str:
    """Return a minimal Plotly HTML fragment for a 72px sparkline.

    Empty series collapses to a text placeholder rather than an empty Plotly
    container — keeps the card layout stable when the filter returns no rows.
    Uses `lines+markers` only when points are sparse (≤12) so short ranges
    retain visible data points.
    """
    if not xs or not ys:
        return _SPARKLINE_EMPTY_HTML

    pio.templates.default = "plotly_dark"
    mode = "lines+markers" if len(xs) <= 12 else "lines"
    fig = go.Figure(
        data=[
            go.Scatter(
                x=list(xs),
                y=list(ys),
                mode=mode,
                line=dict(color=line_color, width=2),
                marker=dict(color=line_color, size=4),
                hoverinfo="skip",
            )
        ]
    )
    fig.update_layout(
        height=_SPARKLINE_HEIGHT,
        margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        showlegend=False,
    )
    return fig.to_html(
        full_html=False,
        include_plotlyjs=False,
        config=_SPARKLINE_CONFIG,
    )


@router.get("/performance", response_class=HTMLResponse)
async def performance(
    request: Request,
    db: AsyncSession = Depends(get_db),
    range: str | None = "all",
    date_from: str | None = None,
    date_to: str | None = None,
    hx_request: Annotated[str | None, Header()] = None,
):
    time_range = range or "all"

    # Vehicle scoping
    active_device_id = await get_active_device_id(db)
    active_vehicle = await get_active_vehicle(db)

    # Unit context: metric DB -> display conversion
    unit_ctx = await get_unit_context(db)
    distance_unit = unit_ctx["distance_unit"]
    # km/kWh (metric base) -> mi/kWh for "us", passthrough for "metric"
    efficiency_factor = MI_PER_KM if distance_unit == "us" else 1.0
    # km -> mi for "us"
    range_factor = MI_PER_KM if distance_unit == "us" else 1.0

    # Query energy data (all metric base)
    summary = await query_energy_summary(
        db, time_range=time_range, device_id=active_device_id,
        date_from=date_from, date_to=date_to,
    )

    # Apply unit conversion to efficiency values (convert ONCE here, not in template)
    if summary["avg_efficiency"] is not None:
        summary["avg_efficiency"] = summary["avg_efficiency"] * efficiency_factor
    if summary["best_efficiency"] is not None:
        summary["best_efficiency"] = summary["best_efficiency"] * efficiency_factor
    if summary["worst_efficiency"] is not None:
        summary["worst_efficiency"] = summary["worst_efficiency"] * efficiency_factor

    # AC/DC distribution donut (three metric variants — kWh/Sessions/Cost)
    donut_kwh = build_charge_type_donut_chart(summary["by_charge_type"], metric="kwh")
    donut_count = build_charge_type_donut_chart(summary["by_charge_type"], metric="count")
    donut_cost = build_charge_type_donut_chart(summary["by_charge_type"], metric="cost")

    # Synthetic DC charge curve fallback (only when no real detail data)
    synthetic_curve_chart = ""
    synthetic_meta = {"dc_session_count": 0, "median_peak_kw": None}
    if not await has_real_charge_curve_data(
        db, time_range=time_range, device_id=active_device_id,
        date_from=date_from, date_to=date_to,
    ):
        synthetic_meta = await query_synthetic_curve_inputs(
            db, time_range=time_range, device_id=active_device_id,
            date_from=date_from, date_to=date_to,
        )
        synthetic_curve_chart = build_synthetic_charge_curve_chart(
            max_kw=synthetic_meta["median_peak_kw"] or 0,
            dc_session_count=int(synthetic_meta["dc_session_count"] or 0),
        )

    # Build efficiency scatter chart (chart builder applies factors internally)
    regen_chart_data = await query_regen_for_chart(
        db, time_range=time_range, device_id=active_device_id,
        date_from=date_from, date_to=date_to,
    )
    chart_html = build_efficiency_chart(
        sessions=summary["sessions_for_chart"],
        regen_data=regen_chart_data,
        unit_label=unit_ctx["units"]["efficiency_label"],
        efficiency_factor=efficiency_factor,
        range_factor=range_factor,
    )

    # Build monthly energy stacked area chart
    monthly_energy_data = await query_monthly_energy(
        db, time_range=time_range, device_id=active_device_id,
        date_from=date_from, date_to=date_to,
    )
    monthly_energy_chart = build_monthly_energy_chart(monthly_energy_data)

    # Sparklines beneath Total Energy + Efficiency cards
    monthly_series = await monthly_energy_series(
        db, time_range=time_range, device_id=active_device_id,
        date_from=date_from, date_to=date_to,
    )
    efficiency_series = await efficiency_over_time_series(
        db, time_range=time_range, device_id=active_device_id,
        date_from=date_from, date_to=date_to,
    )
    monthly_energy_sparkline_html = _build_sparkline(
        xs=[m for m, _ in monthly_series],
        ys=[v for _, v in monthly_series],
    )
    # Apply unit conversion to the efficiency sparkline so its scale matches
    # the big number above it (both display km/kWh or mi/kWh per user setting).
    efficiency_sparkline_html = _build_sparkline(
        xs=[d for d, _ in efficiency_series],
        ys=[v * efficiency_factor for _, v in efficiency_series],
    )

    # Toggleable card charts in the left/right outer cards of the 3-card row.
    # All four reuse the page-level time_range/active_device_id filter so the
    # date-range bar at the top of /charging/performance controls the whole
    # surface (donut + scatter + the four card charts) consistently.
    speed_series = await charging_speed_series(
        db, time_range=time_range, device_id=active_device_id,
        date_from=date_from, date_to=date_to,
    )
    left_chart_speed_html = build_charging_speed_chart(speed_series)
    left_chart_energy_html = build_energy_over_time_chart(monthly_series)
    range_label = "mi" if distance_unit == "us" else "km"
    right_chart_efficiency_html = build_efficiency_over_time_chart(
        efficiency_series,
        efficiency_factor=efficiency_factor,
        unit_label=unit_ctx["units"]["efficiency_label"],
    )
    right_chart_rangeregen_html = build_range_regen_over_time_chart(
        regen_chart_data,
        range_factor=range_factor,
        range_label=range_label,
    )

    all_vehicles = await get_all_vehicles(db)

    context = {
        **unit_ctx,
        "summary": summary,
        "chart_html": chart_html,
        "monthly_energy_chart": monthly_energy_chart,
        "monthly_energy_sparkline_html": monthly_energy_sparkline_html,
        "efficiency_sparkline_html": efficiency_sparkline_html,
        "left_chart_speed_html": left_chart_speed_html,
        "left_chart_energy_html": left_chart_energy_html,
        "right_chart_efficiency_html": right_chart_efficiency_html,
        "right_chart_rangeregen_html": right_chart_rangeregen_html,
        "donut_kwh": donut_kwh,
        "donut_count": donut_count,
        "donut_cost": donut_cost,
        "synthetic_curve_chart": synthetic_curve_chart,
        "synthetic_meta": synthetic_meta,
        "active_range": time_range,
        "date_from": date_from,
        "date_to": date_to,
        "active_page": "performance",
        "page_title": "Performance",
        "unit_label": unit_ctx["units"]["efficiency_label"],
        "charge_type_labels": CHARGE_TYPE_LABELS,
        "active_vehicle": active_vehicle,
        "all_vehicles": all_vehicles,
    }

    if hx_request:
        return templates.TemplateResponse(request, "performance/partials/summary.html", context)
    return templates.TemplateResponse(request, "performance/index.html", context)
