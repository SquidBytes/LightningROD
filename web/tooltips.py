"""Tooltip copy — single source of truth for analytics metric explanations.

Slug convention: `<page>_<metric>` where `<page>` matches the route group
(performance, costs, battery, driving, home) and `<metric>` is the metric
identifier (snake_case). Example: `performance_avg_efficiency`.

Rules (locked):
  - Exactly one sentence per tooltip.
  - ≤15 words per tooltip (whitespace-separated tokens).
  - No formulas, no units-of-measurement reasoning — use plain English.
  - Copy changes MUST be made here; templates reference `{{ tooltips.<slug> }}`.

The Avg Efficiency copy (`performance_avg_efficiency`) is locked verbatim
from Phase 26-04 and must not be altered without an explicit CONTEXT decision.

Phase 27-07 inventories and fills the remaining analytics pages.
"""

from __future__ import annotations

TOOLTIPS: dict[str, str] = {
    # /charging/performance
    "performance_avg_efficiency": (
        "Arithmetic mean of per-session mi/kWh, not total distance divided by total energy."
    ),
    "performance_total_energy": (
        "Total kWh delivered to this vehicle across sessions in the selected date range."
    ),
    # /charging/costs
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
}
