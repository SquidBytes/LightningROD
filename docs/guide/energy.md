# :lucide-gauge: Energy Dashboard

The energy page (`/energy`) tracks your total energy consumption and charging efficiency over time.

<!-- TODO: Add screenshot of energy page -->

## Headline Cards

Three summary cards at the top:

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

## Regenerative Braking

If trip metrics data includes regeneration values, a regen section displays:

- Total lifetime energy recovered
- Regen as a percentage of total energy consumed

!!! info
    Regen data comes from `ev_trip_metrics`, which is currently empty in v1 (populated from CSV charging data only). This section will show data once live ingestion from Home Assistant is implemented, or if trip metric CSVs are loaded.

## Units

The energy dashboard reads the `efficiency_unit` setting:

| Setting | Efficiency | Display |
|---------|-----------|---------|
| US | mi/kWh | Miles per kilowatt-hour |
| EU | km/kWh | Kilometers per kilowatt-hour |

Change this at [Settings](settings.md) under Unit Preferences.
