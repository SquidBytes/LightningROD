"""Tooltip copy — single source of truth for analytics metric explanations.
Slug convention: `<page>_<metric>` where `<page>` matches the route group
(home, performance, costs, battery, driving_sessions, driving_performance)
and `<metric>` is the metric identifier (snake_case). Example:
`performance_avg_efficiency`.
Rules (locked):
- Exactly one sentence per tooltip.
- ≤15 words per tooltip (whitespace-separated tokens).
- No formulas, no units-of-measurement reasoning — use plain English.
- Copy changes MUST be made here; templates reference `{{ tooltips.<slug> }}`.

"""

from __future__ import annotations

TOOLTIPS: dict[str, str] = {
    # --- home ---
    "home_total_sessions": (
        "Count of charging sessions recorded across all vehicles in your history."
    ),
    "home_total_energy": (
        "Cumulative kWh delivered to all vehicles across every recorded session."
    ),
    "home_total_cost": (
        "Sum of every session's charging cost, actual where recorded and estimated otherwise."
    ),
    # --- performance (/charging/performance) ---
    "performance_avg_efficiency": (
        "Arithmetic mean of per-session mi/kWh, not total distance divided by total energy."
    ),
    "performance_total_energy": (
        "Total kWh delivered to this vehicle across sessions in the selected date range."
    ),
    "performance_ac_vs_dc": (
        "Share of charging by source — AC Level 1/2 versus DC fast charging."
    ),
    "performance_monthly_energy": (
        "Monthly kWh broken out by AC or DC source over the selected range."
    ),
    # --- costs (/charging/costs) ---
    "costs_avg_per_session": (
        "Average total charging cost per session in the selected date range."
    ),
    "costs_cost_per_mile": (
        "Total cost divided by total miles added, excluding sessions with no distance."
    ),
    "costs_cost_per_kwh": (
        "Total cost divided by total kWh delivered in the selected date range."
    ),
    "costs_actual_vs_estimated": (
        "Actual is your recorded cost; estimated is computed from rates when actual is missing."
    ),
    "costs_total_energy": (
        "Total kWh delivered across every session counted in this cost total."
    ),
    # --- battery (/battery) ---
    "battery_degradation": "Pack health vs factory capacity.",
    "battery_pack_capacity": "Gross kWh the pack currently reports.",
    "battery_range": "Latest range vs original rated range.",
    "battery_12v": "12V accessory battery voltage.",
    "battery_soc": "State of charge with charging regions shaded.",
    "battery_charge_curve": "Power delivered over the selected charging session.",
    "battery_degradation_trend": "Capacity trend as miles accumulate.",
    "battery_pack_temp": "Latest pack temperature.",
    "battery_temp_over_time": "Battery vs outside-air temp over time.",
    "battery_telemetry": "Latest HV-pack readings with 7-day trend.",
    "battery_voltage": "HV-pack voltage.",
    "battery_amperage": "HV-pack current draw (negative = charging).",
    "battery_power": "HV-pack power (negative = charging).",
    # --- driving_sessions (/driving/sessions) ---
    "driving_sessions_total_trips": (
        "Count of trips recorded in the selected date range."
    ),
    "driving_sessions_total_distance": (
        "Sum of trip distances across all trips in the selected date range."
    ),
    "driving_sessions_avg_efficiency": (
        "Arithmetic mean of per-trip mi/kWh, not total distance divided by total energy."
    ),
    "driving_sessions_total_energy": (
        "Sum of energy consumed across every trip in the selected date range."
    ),
    # --- settings/fuel ---
    "fuel_price_trend": (
        "Station and regional average fuel prices over time, in your configured units."
    ),
    # --- driving_performance (/driving/performance) ---
    "driving_performance_temp_scatter": (
        "Each trip's efficiency plotted against ambient temperature, with a linear regression trendline."
    ),
    "driving_performance_regen": (
        "Monthly kWh recovered via regenerative braking, also shown as a share of trip energy."
    ),
    "driving_performance_range_regenerated": (
        "Total distance recovered via regenerative braking."
    ),
    "driving_performance_energy_regenerated": (
        "Total energy recovered via regenerative braking, derived per trip as range_regenerated ÷ efficiency."
    ),
}
