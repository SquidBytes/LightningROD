# Dashboard

The dashboard (`/`) provides an overview of your charging analytics.

## Summary Cards

Three cards at the top:

| Card | Shows | Detail |
|------|-------|--------|
| Total Sessions | Lifetime session count | Average cost per session |
| Total Energy | Lifetime kWh (auto-scales to MWh above 1,000) | Average kWh per session |
| Total Cost | Lifetime charging cost | Average cost per kWh |

## Charging Efficiency

When sessions have EVSE (charger-side) data, an efficiency card shows aggregate metrics:

- **Avg Loss** -- Average percentage of energy lost between charger delivery and vehicle receipt
- **Total Loss** -- Cumulative kWh lost across sessions with EVSE data
- **Avg Utilization** -- Average percentage of charger rated capacity actually used

The card shows how many sessions contributed EVSE data. Sessions without EVSE data are excluded from these calculations.

## Charts

Two charts in a side-by-side grid:

- **Energy Over Time** -- Cumulative energy consumption over your charging history
- **Energy by Network** -- Breakdown of energy delivered by each charging network, using network-assigned colors

Charts are interactive (Plotly) with hover details and zoom controls.

## Empty State

If no sessions exist, the dashboard shows a prompt to import data via Settings.
