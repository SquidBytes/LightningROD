# Energy Dashboard

The energy page (`/energy`) tracks energy use, efficiency trends, and regenerative braking signals for the active vehicle.

![energy](../assets/images/lr_energy.gif)

## Summary Cards

Three cards at the top:

- **Total Energy** -- Lifetime kWh across sessions in scope
- **Range Recovered (Regen)** -- Summed `range_regenerated` from trip metrics (if available)
- **Avg Efficiency** -- mi/kWh (US) or km/kWh (EU), plus best/worst values

## Efficiency Trend Chart

An interactive Plotly scatter chart showing efficiency per session over time, with:

- Individual session data points colored by charge type
- A rolling 10-session average overlay line
- Optional regen overlay on a secondary axis when trip regen data exists

!!! note
    Efficiency is computed as `miles_added / energy_kwh` at query time. Sessions missing either value are excluded from the chart.

## Energy by Charge Type

A section below the summary cards shows kWh and session counts split by AC vs DC.

## Monthly Energy by Type

The monthly chart shows stacked energy totals by charge type over time.

## Regenerative Braking

If trip metrics data includes regeneration values, a regen section displays:

- Total lifetime range recovered
- Regen as a percentage of total energy consumed

!!! info
    Regen data comes from `ev_trip_metrics`. If no trip data is available, the card shows a no-data state.

## Units

The energy dashboard reads the `efficiency_unit` setting:

| Setting | Efficiency | Display |
|---------|-----------|---------|
| US | mi/kWh | Miles per kilowatt-hour |
| EU | km/kWh | Kilometers per kilowatt-hour |

Change this at [Settings](settings.md) under Unit Preferences.
