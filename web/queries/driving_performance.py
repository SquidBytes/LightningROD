"""Driving performance query layer.

Aggregates trip-side metrics for the /driving/performance page:
- Total distance, average driving efficiency, trip count (from EVTripMetrics)
- Regen recovery total (delegated to energy.query_regen_summary)
- Efficiency trend data (passthrough of trips.query_efficiency_trend)
- Temperature-vs-efficiency correlation + per-trip regen support.

Returns metric-base values. The route handler applies distance_factor conversion
before passing to templates / chart builders.
"""


import numpy as np
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.trip_metrics import EVTripMetrics
from web.queries.dashboard import _HOVER_LABEL, _PLOTLY_CONFIG, _wrap_chart
from web.queries.energy import query_regen_summary
from web.queries.trips import build_trip_time_filter, query_efficiency_trend
from web.unit_system import MI_PER_KM


def _min_points_for_range(time_range: str) -> int:
    """Use a lower point threshold for short windows.

    - 7d / 30d  → 5 points minimum (recent data is sparser).
    - 90d / ytd / 1y / all → 10 points minimum (larger window should have more data).
    """
    return 5 if time_range in ("7d", "30d") else 10


async def query_driving_performance_summary(
    db: AsyncSession,
    time_range: str = "all",
    device_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    """Aggregate driving-side metrics for /driving/performance page.

    Returns metric-base values. The route handler applies distance_factor
    conversion before passing to templates.

    Returns a dict with:
    - total_distance (km, metric base)
    - total_energy (kWh)
    - avg_driving_efficiency (km/kWh, metric base) — None when total_energy is 0/None
    - total_regen (km, metric base — from query_regen_summary; rendered as
      "Range Regenerated" tile)
    - trip_count (prefer regen trip_count, fall back to EVTripMetrics count)
    - efficiency_trend (passthrough from query_efficiency_trend)
    - temperature_correlation_data (reserved key, currently None)
    - regen_per_trip_data (reserved key, currently None)
    """
    # 1. Regen summary (can return None when no regen data exists)
    regen = await query_regen_summary(
        db, time_range=time_range, device_id=device_id,
        date_from=date_from, date_to=date_to,
    )

    # 2. Distance + energy + trip count from EVTripMetrics
    stmt = select(
        func.sum(EVTripMetrics.distance),
        func.sum(EVTripMetrics.energy_consumed),
        func.count(EVTripMetrics.id),
    )

    time_filter = build_trip_time_filter(time_range, date_from, date_to)
    if time_filter is not None:
        stmt = stmt.where(time_filter)
    if device_id:
        stmt = stmt.where(EVTripMetrics.device_id == device_id)

    result = await db.execute(stmt)
    row = result.one()
    total_distance = float(row[0]) if row[0] is not None else None
    total_energy = float(row[1]) if row[1] is not None else None
    trip_count_trips = int(row[2]) if row[2] is not None else 0

    # 3. Derive avg driving efficiency (None-safe)
    if total_distance is not None and total_energy is not None and total_energy > 0:
        avg_eff = total_distance / total_energy
    else:
        avg_eff = None

    # 4. Efficiency trend passthrough (used by Driving Efficiency chart)
    trend_data = await query_efficiency_trend(
        db, time_range=time_range, device_id=device_id,
        date_from=date_from, date_to=date_to,
    )

    return {
        "total_distance": total_distance,
        "total_energy": total_energy,
        "avg_driving_efficiency": avg_eff,
        "total_regen": regen.get("regen_total") if regen else None,
        "trip_count": (regen.get("trip_count") if regen else 0) or trip_count_trips or 0,
        "efficiency_trend": trend_data,
        # Reserved keys so callers can rely on stable response shape.
        "temperature_correlation_data": None,
        "regen_per_trip_data": None,
    }


async def query_temperature_correlation(
    db: AsyncSession,
    time_range: str = "all",
    device_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """Trips with populated ambient_temp + distance + energy_consumed in range window.

    Metric base — temperature in °C, distance in km, energy in kWh. The chart
    builder applies unit conversion (°C → °F and km/kWh → mi/kWh).

    Excludes rows where any of ambient_temp / distance / energy_consumed is
    NULL or where energy_consumed <= 0 (can't derive efficiency).

    Returns rows as dicts with: ambient_temp, distance, energy_consumed, start_time.
    """
    stmt = select(
        EVTripMetrics.ambient_temp,
        EVTripMetrics.distance,
        EVTripMetrics.energy_consumed,
        EVTripMetrics.start_time,
    ).where(
        EVTripMetrics.ambient_temp.is_not(None),
        EVTripMetrics.distance.is_not(None),
        EVTripMetrics.energy_consumed.is_not(None),
        EVTripMetrics.energy_consumed > 0,
    )

    time_filter = build_trip_time_filter(time_range, date_from, date_to)
    if time_filter is not None:
        stmt = stmt.where(time_filter)
    if device_id is not None:
        stmt = stmt.where(EVTripMetrics.device_id == device_id)

    rows = (await db.execute(stmt)).all()
    return [
        {
            "ambient_temp": float(r.ambient_temp),
            "distance": float(r.distance),
            "energy_consumed": float(r.energy_consumed),
            "start_time": r.start_time,
        }
        for r in rows
    ]


def build_temperature_correlation_chart(
    data: list[dict],
    distance_unit: str = "metric",
    min_points: int = 5,
    mode: str = "full",
) -> str:
    """Scatter of ambient temp vs derived mi/kWh (or km/kWh) with linear trendline.

    mode="full" (default): scatter + linear regression trendline.
    mode="scatter": scatter only.
    mode="trend": just the regression line (smaller height + slope annotation).

    Args:
        data: rows from query_temperature_correlation (metric base — °C, km, kWh).
        distance_unit: "us" converts temp to °F and efficiency to mi/kWh.
                       "metric" keeps °C and km/kWh.
        min_points: below this count, returns "" (empty-state handled by caller).

    Returns:
        HTML snippet of the Plotly figure, or "" when there is not enough data.
    """
    if not data or len(data) < min_points:
        return ""

    is_us = distance_unit == "us"
    temp_label = "°F" if is_us else "°C"
    eff_label = "mi/kWh" if is_us else "km/kWh"
    efficiency_factor = MI_PER_KM if is_us else 1.0

    temps: list[float] = []
    effs: list[float] = []
    for row in data:
        c_temp = float(row["ambient_temp"])
        temp_val = (c_temp * 9.0 / 5.0 + 32.0) if is_us else c_temp
        dist = float(row["distance"])
        energy = float(row["energy_consumed"])
        if energy <= 0:
            continue
        eff = (dist / energy) * efficiency_factor  # derived efficiency
        temps.append(temp_val)
        effs.append(eff)

    if len(temps) < min_points:
        return ""

    pio.templates.default = "plotly_dark"
    fig = go.Figure()

    if mode in ("full", "scatter"):
        fig.add_trace(
            go.Scatter(
                x=temps,
                y=effs,
                mode="markers",
                name="Trips",
                marker=dict(color="#47A8E5", size=7, opacity=0.75),
                hovertemplate=(
                    "<b>%{x:.1f} " + temp_label + "</b><br>"
                    "%{y:.2f} " + eff_label + "<extra></extra>"
                ),
            )
        )

    # Trendline always computed — referenced both in "full" and "trend" modes,
    # and used to compose the slope annotation in "trend".
    x_arr = np.array(temps, dtype=float)
    y_arr = np.array(effs, dtype=float)
    slope, intercept = np.polyfit(x_arr, y_arr, 1)
    x_sorted = np.sort(x_arr)
    trend_y = slope * x_sorted + intercept

    if mode in ("full", "trend"):
        fig.add_trace(
            go.Scatter(
                x=x_sorted.tolist(),
                y=trend_y.tolist(),
                mode="lines",
                name="Trend",
                line=dict(color="#facc15", width=2, dash="dash"),
                hoverinfo="skip",
            )
        )

    chart_height = 220 if mode in ("scatter", "trend") else None
    show_legend = mode == "full"

    layout_kwargs: dict = dict(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#e5e7eb",
        margin=dict(l=50, r=20, t=20, b=40),
        xaxis=dict(title=f"Ambient Temp ({temp_label})"),
        yaxis=dict(title=eff_label),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1
        ),
        hoverlabel=_HOVER_LABEL,
        showlegend=show_legend,
    )
    if chart_height is not None:
        layout_kwargs["height"] = chart_height

    if mode == "trend":
        layout_kwargs["annotations"] = [
            dict(
                x=0.02, y=0.98, xref="paper", yref="paper",
                text=f"slope: {slope:+.4f} {eff_label}/{temp_label}",
                showarrow=False, align="left",
                font=dict(color="#e5e7eb", size=12),
                bgcolor="rgba(0,0,0,0.4)", borderpad=4,
            )
        ]

    fig.update_layout(**layout_kwargs)
    return _wrap_chart(
        fig.to_html(full_html=False, include_plotlyjs=False, config=_PLOTLY_CONFIG)
    )


async def query_regen_per_trip(
    db: AsyncSession,
    time_range: str = "all",
    device_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """Per-trip regen with derived regen_kwh and regen_pct.

    ``range_regenerated`` is stored in range units (metric base km).
    Derive kWh-equivalent as:

        efficiency = distance / energy_consumed   # km/kWh
        regen_kwh  = range_regenerated / efficiency
        regen_pct  = regen_kwh / energy_consumed * 100

    Returns most-recent-first list (by start_time desc) for bar chart X-axis
    labelling. Excludes trips with null range_regenerated, or null/zero
    distance/energy_consumed (can't derive kWh-equivalent).

    Returned dict keys: trip_num, start_time, distance, energy_consumed,
    range_regenerated, regen_kwh, regen_pct.
    """
    stmt = select(
        EVTripMetrics.id,
        EVTripMetrics.start_time,
        EVTripMetrics.distance,
        EVTripMetrics.energy_consumed,
        EVTripMetrics.range_regenerated,
    ).where(
        EVTripMetrics.range_regenerated.is_not(None),
        EVTripMetrics.distance.is_not(None),
        EVTripMetrics.distance > 0,
        EVTripMetrics.energy_consumed.is_not(None),
        EVTripMetrics.energy_consumed > 0,
    ).order_by(EVTripMetrics.start_time.desc())

    time_filter = build_trip_time_filter(time_range, date_from, date_to)
    if time_filter is not None:
        stmt = stmt.where(time_filter)
    if device_id is not None:
        stmt = stmt.where(EVTripMetrics.device_id == device_id)

    rows = (await db.execute(stmt)).all()
    results: list[dict] = []
    for i, row in enumerate(rows, start=1):
        distance = float(row.distance)
        energy = float(row.energy_consumed)
        regen_range = float(row.range_regenerated)
        if distance <= 0 or energy <= 0:
            continue
        efficiency = distance / energy  # km/kWh
        if efficiency <= 0:
            continue
        regen_kwh = regen_range / efficiency
        regen_pct = (regen_kwh / energy) * 100.0 if energy > 0 else 0.0
        results.append(
            {
                "trip_num": i,
                "start_time": row.start_time,
                "distance": distance,
                "energy_consumed": energy,
                "range_regenerated": regen_range,
                "regen_kwh": regen_kwh,
                "regen_pct": regen_pct,
            }
        )
    return results


def build_regen_recovery_chart(trips: list[dict], mode: str = "full") -> str:
    """Per-trip regen chart.

    mode="full" (default): bars (kWh, primary axis) + line (%, secondary axis).
    mode="kwh": bars only on a single axis.
    mode="pct": line only on a single axis (smaller height for mini card).

    Args:
        trips: rows from query_regen_per_trip (already has trip_num, regen_kwh,
            regen_pct populated).

    Returns:
        HTML snippet of the Plotly figure, or "" when ``trips`` is empty.
    """
    if not trips:
        return ""

    pio.templates.default = "plotly_dark"

    labels = [f"#{t['trip_num']}" for t in trips]
    regen_kwh_values = [t["regen_kwh"] for t in trips]
    regen_pct_values = [t["regen_pct"] for t in trips]

    if mode == "full":
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(
            go.Bar(
                x=labels,
                y=regen_kwh_values,
                name="Regen kWh",
                marker_color="#4ade80",
                hovertemplate=(
                    "<b>Trip %{x}</b><br>"
                    "Regen: %{y:.2f} kWh<extra></extra>"
                ),
            ),
            secondary_y=False,
        )
        fig.add_trace(
            go.Scatter(
                x=labels,
                y=regen_pct_values,
                mode="lines+markers",
                name="Regen %",
                line=dict(color="#facc15", width=2),
                marker=dict(size=6),
                hovertemplate="<b>Trip %{x}</b><br>%{y:.1f}%<extra></extra>",
            ),
            secondary_y=True,
        )
        fig.update_yaxes(
            title_text="Regen (kWh)", secondary_y=False, rangemode="tozero"
        )
        fig.update_yaxes(
            title_text="Regen %",
            secondary_y=True,
            showgrid=False,
            rangemode="tozero",
        )
        chart_height = None
        show_legend = True
    else:
        fig = go.Figure()
        if mode == "kwh":
            fig.add_trace(
                go.Bar(
                    x=labels,
                    y=regen_kwh_values,
                    name="Regen kWh",
                    marker_color="#4ade80",
                    hovertemplate=(
                        "<b>Trip %{x}</b><br>"
                        "Regen: %{y:.2f} kWh<extra></extra>"
                    ),
                )
            )
            fig.update_yaxes(title_text="Regen (kWh)", rangemode="tozero")
        else:  # pct
            fig.add_trace(
                go.Scatter(
                    x=labels,
                    y=regen_pct_values,
                    mode="lines+markers",
                    name="Regen %",
                    line=dict(color="#facc15", width=2),
                    marker=dict(size=6),
                    hovertemplate="<b>Trip %{x}</b><br>%{y:.1f}%<extra></extra>",
                )
            )
            fig.update_yaxes(title_text="Regen %", rangemode="tozero")
        chart_height = 220
        show_legend = False

    fig.update_xaxes(title_text="Trip (most recent first)")
    layout_kwargs: dict = dict(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#e5e7eb",
        margin=dict(l=50, r=50, t=20, b=40),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1
        ),
        hoverlabel=_HOVER_LABEL,
        showlegend=show_legend,
    )
    if chart_height is not None:
        layout_kwargs["height"] = chart_height
    fig.update_layout(**layout_kwargs)
    return _wrap_chart(
        fig.to_html(full_html=False, include_plotlyjs=False, config=_PLOTLY_CONFIG)
    )
