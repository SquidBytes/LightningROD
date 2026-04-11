# Driving

The **Driving** section covers everything that happens between charges: individual trips, efficiency vs temperature, and regen recovery.

It has two pages under the sidebar's `DRIVING` group:

- **Trip Sessions** at `/driving/sessions` — full trip list and detail drawer
- **Analytics** at `/driving/performance` — efficiency correlations and regen trends

!!! note "Renamed from Trips"
    In earlier releases this lived at `/trips`. v0.3 moved it under `/driving/*` so it's symmetrical with `/charging/*`. Trip data, detail drawers, and sidebar badges are unchanged — only the URL and sidebar grouping moved.

## Trip Sessions (`/driving/sessions`)

A paginated table of trips with ten columns: number, date, distance, duration, temperature, energy consumed, reported efficiency, calculated efficiency, driving score (as a gradient bar), and regeneration.

Click any row to open a slide-out detail drawer with:

- **Overview** — start/end location (auto-detected from GPS proximity), distance, duration, energy, temperature
- **Driving scores** — smooth/efficient/safe score breakdown
- **Environment chart** — temperature and altitude over the trip
- **Drive graphs** — SOC, speed, and range on a shared time axis (interpolated segments shown as dotted lines at 40% opacity)
- **Expand** — a full-screen modal with Battery, Environment, and Driving chart sections

Summary cards above the table — Total Trips, Avg Efficiency, Total Distance, Total Energy — respect the date range filter.

## Driving Performance (`/driving/performance`)

A new analytics page introduced in v0.3 for driving-side metrics.

### Summary Row

- **Range Recovered (Regen)** — total regen across trips in range (moved here from the old Energy page)
- Plus per-page summary tiles for total distance, average efficiency, etc.

### Driving Efficiency Trend

A time-series chart of mi/kWh per trip (or km/kWh in metric). The twin of the Charging Efficiency trend on `/charging/performance`.

### Efficiency vs Temperature

A scatter plot of individual trip efficiency against ambient temperature, with a fitted linear trendline. Useful for spotting cold-weather efficiency loss.

- **X-axis**: ambient temperature (°F or °C per unit preference)
- **Y-axis**: calculated mi/kWh or km/kWh
- **Trendline**: least-squares linear fit — only shown when enough trips have ambient temperature data (at least 5 for short ranges, 10 for longer)

### Regen Recovery

A dual-axis bar + line chart showing regen per trip:

- **Bars**: regen kWh per trip
- **Line**: regen percentage (regen kWh / gross kWh used) per trip
- Most recent trips first, scoped to the current date range

Hover any bar for trip date, regen kWh, gross kWh, regen %, and distance.

## Date Range Filter

Both pages use the same `?range=` filter (`7d` / `30d` / `90d` / `YTD` / `All`) as the other analytics pages.
