# Energy Dashboard

The energy page (`/energy`) tracks total energy consumption and charging efficiency over time.

## Summary Cards

Three cards at the top:

- **Total energy consumed** -- Lifetime kWh across all sessions
- **Average efficiency** -- mi/kWh (US) or km/kWh (EU), based on your unit preference
- **Charge type breakdown** -- Total kWh split by AC vs DC

## Efficiency Trend Chart

An interactive Plotly scatter chart showing efficiency per session over time, with:

- Individual session data points colored by charge type
- A rolling 10-session average overlay line
- Hover details showing date, efficiency, and energy for each session

!!! note
    Efficiency is computed as `miles_added / energy_kwh` at query time. Sessions missing either value are excluded from the chart.

## Charging Efficiency Card

When EVSE data is available on sessions, an aggregate efficiency card shows:

- **Average charging loss** -- Percentage of energy lost between EVSE delivery and vehicle receipt
- **Total loss** -- Cumulative kWh lost across all sessions with EVSE data
- **Average utilization** -- How much of the charger's rated capacity was used on average

Loss is calculated as `evse_energy_kwh - energy_kwh` for sessions where both values exist. Utilization is `max_power / charger_rated_kw`.

## Regenerative Braking

If trip metrics data includes regeneration values, a regen section displays:

- Total lifetime energy recovered
- Regen as a percentage of total energy consumed

!!! info
    Regen data comes from `ev_trip_metrics`. This section will show data once live ingestion from Home Assistant is implemented, or if trip metric CSVs are loaded.

## Units

The energy dashboard reads the `efficiency_unit` setting:

| Setting | Efficiency | Display |
|---------|-----------|---------|
| US | mi/kWh | Miles per kilowatt-hour |
| EU | km/kWh | Kilometers per kilowatt-hour |

Change this at [Settings](settings.md) under Unit Preferences.
