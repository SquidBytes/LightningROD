# Dashboard

The dashboard (`/`) gives you a quick overview across all vehicles.

![overview](../assets/images/lr_overview.gif)

## Vehicle Cards

The top section shows your configured vehicles, session counts, and last-charge dates. One vehicle can be marked active for vehicle-specific pages, but dashboard totals stay global.

## Summary Cards

Three cards at the top:

| Card | Shows | Detail |
|------|-------|--------|
| Total Sessions | Lifetime session count | Average cost per session |
| Total Energy | Lifetime kWh (switches to MWh above 1,000) | Average kWh per session |
| Total Cost | Lifetime charging cost | Average cost per kWh |

## Charging Efficiency

When sessions have EVSE data, an efficiency card shows aggregate metrics:

- **Avg Loss** -- Average percentage of energy lost between charger delivery and vehicle receipt
- **Total Loss** -- Cumulative kWh lost across sessions with EVSE data
- **Avg Utilization** -- Average percentage of charger rated capacity actually used

The card shows how many sessions contributed EVSE data. Sessions without EVSE data are excluded.

## Charts

Two charts in a side-by-side grid:

- **Monthly Energy by Network** -- Stacked monthly energy totals grouped by charging network
- **Energy by Network** -- Donut breakdown of total delivered energy by charging network

Charts are interactive with hover details and zoom controls.

## Empty State

If no sessions exist, the dashboard shows a prompt to import data through Settings.
