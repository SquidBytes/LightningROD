# Battery Analytics

The battery analytics page (`/battery`) tracks long-term battery health, state of charge, charging behavior, and the 12V low-voltage system for the active vehicle.

## Date Range Filter

Pick `7d`, `30d`, `90d`, `YTD`, or `All` from the filter bar. The selected range is stored in the URL as `?range=…`.

## Health Summary Cards

Four summary tiles appear at the top:

- **Est. Degradation** — current pack capacity as a percentage of the vehicle's rated gross capacity
- **Pack Capacity** — current gross kWh vs rated gross kWh, with the delta
- **Range** — latest reported range vs the rated max range, with the delta
- **12V Battery** — latest low-voltage (starter) battery voltage and charge level

The cards use the most recent `ev_battery_status` record. If Home Assistant does not provide capacity data, the tiles show a "no data" state.

!!! info "Usable vs gross capacity"
    LightningROD tracks two capacity numbers per vehicle:

    - **Usable** capacity is the energy you can actually drive with.
    - **Gross** capacity is the full battery size reported by FordPass.

    The health card compares gross to gross so a fresh pack reads close to 100%. If the two values are mixed up, the percentages will be wrong. Both values live on the vehicle record in **Settings → Vehicles** and can be filled from the preset table.

## SOC Timeline

The main chart shows state-of-charge over time, with:

- **Color-coded charging regions** -- brighter green areas highlight periods where power was positive, so charging sessions stand out from rest
- **Click-to-drill** -- click a charging region to filter the Charge Curve panel below to that session
- **Gap-aware** -- data gaps appear as breaks instead of connected lines

Large ranges are simplified on the server to keep the page fast even with years of data.

## Charge Curve

A charge curve with **SOC % on the X-axis and kW on the Y-axis**. Up to three lines can appear:

- **Reference curve** — a pre-configured model curve for the active vehicle (loaded from `reference_charge_curves/*.json`)
- **Average curve** — your own average across recent DC sessions
- **Session curve** — the specific session selected above, if any

A temperature toggle in the legend lets you overlay battery temperature as a second axis.

!!! info "Fallback"
    When fewer than 3 detailed `battery_status` points exist for a session, the app draws a simple line between the start and end SOC. The chart still renders, but the result is less precise.

## Battery Degradation

A scatter plot of usable capacity against **odometer mileage** (km or mi depending on units) with a projected trend line. Date-based degradation is used only when odometer data is missing.

## 12V Battery Trend

A time-series chart of the low-voltage battery voltage across the current date range. It can help spot parasitic drain or an aging 12V battery early.

## Performance Notes

- Secondary charts (degradation, charge curve, 12V) load when you scroll to them instead of on the initial page load.
- "All"-range queries use server-side downsampling to keep response times reasonable, even with years of data.
