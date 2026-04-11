# Battery Analytics

The battery analytics page (`/battery`) tracks long-term battery health, state of charge, charging behavior, and the 12V low-voltage system for the active vehicle.

## Date Range Filter

Pick `7d`, `30d`, `90d`, `YTD`, or `All` via the filter bar. The range is carried in the URL as `?range=…`.

## Health Summary Cards

Four health-focused tiles at the top:

- **Battery Health** — current usable capacity as a percentage of the vehicle's rated capacity (gauge-style)
- **Capacity** — current kWh vs rated kWh, with the delta
- **Range** — latest reported range vs the rated max range, with the delta
- **12V Battery** — latest low-voltage (starter) battery voltage and charge level

Cards read from the most recent `ev_battery_status` record. If your Home Assistant feed doesn't provide capacity data, the tiles show "no data" states.

## SOC Timeline

The main chart shows state-of-charge percentage over time, with:

- **Color-coded charging regions** — brighter green overlays highlight periods where power was positive (≥0.5 kW), so charging sessions are visually distinct from rest
- **Click-to-drill** — clicking a charging region filters the Charge Curve panel below to that session
- **Gap-aware** — data gaps are rendered as breaks, not connected lines, so you can tell when the vehicle was offline

Large ranges are automatically downsampled server-side (30-min buckets for 2k-10k rows, 1-hour for >10k) to keep the page fast even with years of data.

## Charge Curve

A flipped industry-standard charge curve: **SOC % on the X-axis, kW on the Y-axis**. Up to three lines are overlaid:

- **Reference curve** — a pre-configured model curve for the active vehicle (loaded from `reference_charge_curves/*.json`)
- **Average curve** — your own average across recent DC sessions
- **Session curve** — the specific session selected above, if any

A small temperature toggle on the legend lets you overlay battery temperature as a secondary axis.

!!! info "Fallback"
    When fewer than 3 detailed `battery_status` points exist for a session, the app falls back to a linear interpolation between start and end SOC. Accuracy is limited but the card still renders.

## Battery Degradation

A scatter plot of usable capacity against **odometer mileage** (km or mi depending on units) with a projected trend line. This is how TeslaMate-style views communicate long-term degradation; date-based degradation is kept as an internal fallback when odometer data is missing.

## 12V Battery Trend

A time-series chart of the low-voltage battery voltage across the current date range. Useful for catching parasitic drain or aging 12V batteries before they strand you.

## Performance Notes

- Secondary charts (degradation, charge curve, 12V) are **lazy-loaded** via HTMX — they render once you scroll to them, not on initial page load.
- "All"-range queries use SQL-level `date_trunc` downsampling (6h / 4h / 2h buckets depending on row count) to keep response times under 10 seconds even with years of data.
