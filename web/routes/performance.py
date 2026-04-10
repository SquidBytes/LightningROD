from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from web.dependencies import get_db
from web.queries.energy import (
    CHARGE_TYPE_LABELS,
    build_charge_type_donut_chart,
    build_efficiency_chart,
    build_monthly_energy_chart,
    build_synthetic_charge_curve_chart,
    has_real_charge_curve_data,
    query_energy_summary,
    query_monthly_energy,
    query_regen_for_chart,
    query_synthetic_curve_inputs,
)
from web.queries.settings import get_unit_context
from web.queries.vehicles import get_active_device_id, get_active_vehicle, get_all_vehicles
from web.unit_system import MI_PER_KM

router = APIRouter()
templates = Jinja2Templates(directory="web/templates")


@router.get("/performance", response_class=HTMLResponse)
async def performance(
    request: Request,
    db: AsyncSession = Depends(get_db),
    range: Optional[str] = "all",
    hx_request: Annotated[Optional[str], Header()] = None,
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
    summary = await query_energy_summary(db, time_range=time_range, device_id=active_device_id)

    # Apply unit conversion to efficiency values (convert ONCE here, not in template)
    if summary["avg_efficiency"] is not None:
        summary["avg_efficiency"] = summary["avg_efficiency"] * efficiency_factor
    if summary["best_efficiency"] is not None:
        summary["best_efficiency"] = summary["best_efficiency"] * efficiency_factor
    if summary["worst_efficiency"] is not None:
        summary["worst_efficiency"] = summary["worst_efficiency"] * efficiency_factor

    # Phase 25: AC/DC distribution donut (three metric variants — kWh/Sessions/Cost)
    donut_kwh = build_charge_type_donut_chart(summary["by_charge_type"], metric="kwh")
    donut_count = build_charge_type_donut_chart(summary["by_charge_type"], metric="count")
    donut_cost = build_charge_type_donut_chart(summary["by_charge_type"], metric="cost")

    # Phase 25: Synthetic DC charge curve fallback (only when no real detail data)
    synthetic_curve_chart = ""
    synthetic_meta = {"dc_session_count": 0, "median_peak_kw": None}
    if not await has_real_charge_curve_data(
        db, time_range=time_range, device_id=active_device_id
    ):
        synthetic_meta = await query_synthetic_curve_inputs(
            db, time_range=time_range, device_id=active_device_id
        )
        synthetic_curve_chart = build_synthetic_charge_curve_chart(
            max_kw=synthetic_meta["median_peak_kw"] or 0,
            dc_session_count=synthetic_meta["dc_session_count"],
        )

    # Build efficiency scatter chart (chart builder applies factors internally)
    regen_chart_data = await query_regen_for_chart(db, time_range=time_range, device_id=active_device_id)
    chart_html = build_efficiency_chart(
        sessions=summary["sessions_for_chart"],
        regen_data=regen_chart_data,
        unit_label=unit_ctx["units"]["efficiency_label"],
        efficiency_factor=efficiency_factor,
        range_factor=range_factor,
    )

    # Build monthly energy stacked area chart
    monthly_energy_data = await query_monthly_energy(db, time_range=time_range, device_id=active_device_id)
    monthly_energy_chart = build_monthly_energy_chart(monthly_energy_data)

    all_vehicles = await get_all_vehicles(db)

    context = {
        **unit_ctx,
        "summary": summary,
        "chart_html": chart_html,
        "monthly_energy_chart": monthly_energy_chart,
        "donut_kwh": donut_kwh,
        "donut_count": donut_count,
        "donut_cost": donut_cost,
        "synthetic_curve_chart": synthetic_curve_chart,
        "synthetic_meta": synthetic_meta,
        "active_range": time_range,
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
