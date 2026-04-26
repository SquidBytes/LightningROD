# Charging Performance

The charging performance page (`/charging/performance`) tracks how you charge: energy totals, efficiency trends, AC vs DC split, and synthetic fast-charging curves for the active vehicle.

!!! note "Renamed from Energy"
    This page was `/charging/energy` in earlier releases. The route and sidebar label were renamed in v0.3 to better reflect what it shows — this is a **charging-side** analytics page, distinct from the new **Driving Performance** page.

## Date Range Filter

A filter bar at the top lets you scope everything on the page to `7d`, `30d`, `90d`, `YTD`, or `All`. The active range is passed in the URL as `?range=…` and shared with other analytics pages.

## Summary Row

Three summary tiles sit at the top:

- **Total Energy** — kWh delivered in the current range
- **AC vs DC Energy** — donut chart splitting energy by charge type. A small chip toggle above the donut switches the metric between **kWh**, **Sessions**, and **Cost**. This tile replaced the old "Range Recovered" card; range recovery now lives on the [Driving Performance](driving.md) page.
- **Avg Efficiency** — average mi/kWh (US) or km/kWh (metric) across sessions in range

## Charging Efficiency Trend

An interactive scatter chart showing efficiency per session over time:

- Individual session points colored by charge type
- Rolling 10-session average overlay
- Optional regen overlay on a secondary axis when trip regen data is available

!!! info "Renamed"
    Previously called "Efficiency Trend". Now explicitly "Charging Efficiency" so it's not confused with the "Driving Efficiency" chart on the Driving Performance page.

## Monthly Energy by Type

A stacked bar chart of monthly kWh totals, broken out by charge type (AC vs DC).

## Synthetic DC Charge Curve

Below the monthly chart, a **synthetic** aggregate DC charge curve shows average fast-charging behavior across DC sessions in the current range. This is an estimate based on peak power, taper points, and session count — not a measurement.

- **X-axis**: SOC %
- **Y-axis**: kW
- **Badge**: a small "Synthetic" badge clarifies that the curve is modeled, not recorded
- **Fallback only**: the curve renders only when no recorded DC sessions have detailed `battery_status` telemetry. If real curve points exist, the Battery Analytics page's per-session chart is the authoritative source.
- **DC only**: AC sessions are excluded (AC curves are effectively flat).

Use this card to get a quick sense of taper behavior even when you haven't captured full telemetry for every fast-charge.
