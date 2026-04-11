# Settings

The settings page (`/settings`) is organized into focused tabs. Most updates apply immediately after saving.

![settings](../assets/images/lr_settings.gif)

## Tab Overview

| Tab | What it manages |
|-----|-----------------|
| Vehicles | Vehicle profiles, battery presets, active vehicle selection |
| General | Comparison display toggles, gas price history, gas-price HA sensors, unit and timezone preferences |
| Networks | Networks, locations, charger stalls, subscription periods |
| CSV Import | Template download and bulk session import flow |
| Home Assistant | HA connection settings, status, reconnect/disconnect, history backfill |

## Vehicles Tab

Use this tab to manage EV profiles and select the active vehicle. The active vehicle is used for vehicle-scoped pages like Charging Sessions, Costs, Charging Performance, Battery Analytics, and Driving.

Vehicle fields include display name, make/model/year, battery capacity, VIN/device ID, and ICE comparison fields.

### Vehicle Presets

The Edit Vehicle modal offers cascading combo-box fields for **Make → Model → Trim → Battery Capacity**. Typing or selecting a Make narrows the Model options, selecting a Model narrows the Trim options, and picking a Trim auto-fills the battery capacity from the preset table. Ford is auto-selected since it's the only preset make today, and you can free-type any value for non-preset vehicles.

!!! info "Lariat / trim packages"
    The preset table currently treats "trim" as a battery variant (SR / ER / Flash). Marketing trim packages (Pro / XLT / Lariat / Platinum) are planned for a future release — for now, leave the trim blank or type your package name manually.

## Networks Tab

Networks are the primary organizational unit for charging locations and rates. Each network has:

| Field | Description |
|-------|-------------|
| Name | Network name (e.g., "Home", "Electrify America") |
| Cost per kWh | Base electricity rate for this network |
| Color | Hex color for badges and charts |
| Free | Whether this network charges nothing |
| Notes | Optional description |

The table shows each network with color badge, session count, and location count. Rows expand to show a read-only location summary.

### Network Edit Modal

The network modal has three tabs:

- **Details** -- Edit name, rate, color, free toggle, and notes. Includes **Recalculate Session Costs** for network-driven recalculation.
- **Locations** -- Manage locations under this network.
- **Subscription** -- Manage historical/current member-rate periods for this network.

### Locations

| Field | Description |
|-------|-------------|
| Name | Location name (e.g., "Main St Station") |
| Location type | Home, work, public, retail, destination, highway, other |
| Address | Street address |
| Latitude/Longitude | GPS coordinates |
| Cost per kWh | Optional override of network cost |
| Notes | Optional description |

Location `cost_per_kwh` overrides network `cost_per_kwh` when computing estimated costs.

![settings network](../assets/images/lr_settings_networks.gif)

### Charger Stalls

Each location can have multiple stalls with different hardware specs:

| Field | Description |
|-------|-------------|
| Label | User-defined name (e.g., "350kW CCS", "L2 West Wall") |
| Charger type | L1, L2, or DCFC |
| Rated kW | Maximum rated power |
| Voltage / Amperage | Typical electrical specs |
| Connector type | CCS, CHAdeMO, J1772, NACS, Tesla |
| Default | Auto-select this stall when the location is chosen |

When editing a session, selecting a stall can auto-fill EVSE fields (rated kW, voltage, amperage).

For supported networks, use **Pre-fill from [Network]** to load known stall templates quickly.

### Subscription Periods

Subscriptions let you model member pricing over time.

| Field | Description |
|-------|-------------|
| Member rate | Member $/kWh used during the subscription window |
| Monthly fee | Flat monthly subscription cost |
| Start / End date | Active period (`end_date` blank means currently active) |
| Notes | Optional plan metadata |

Subscription data powers member-vs-non-member savings on the Costs page.

## General Tab

### Comparison Display Options

Toggle which comparison sections appear on the Costs page:

- Comparison section
- Gasoline comparison
- Network rate comparison

### Gas Price History

Maintain month-by-month gas price history with two tracks:

- Station price (your usual station)
- Average price (regional average)

These values are used to compute savings ranges in gas comparison cards.

### Gas Price Sensors (Home Assistant)

Optional sensor entity IDs can be configured for station and average gas price feeds.

### Unit Preferences

LightningROD stores all distances, temperatures, efficiencies, and volumes in **metric** as the canonical database form (km, °C, km/kWh, L/100km, liters), and converts once at the display/input boundary. Unit preferences are split into two independent axes so you can mix them:

| Axis | Options | What it changes |
|------|---------|-----------------|
| **Distance** | US (mi, mi/kWh, MPG, gal, mph) / Metric (km, km/kWh, L/100km, L, km/h) | Distance, range, efficiency, fuel economy, speed labels |
| **Temperature** | US (°F) / Metric (°C) | Temperature displays and charts |

You can, for example, pick `mi/kWh` with `°C` if that's how you think about your vehicle.

### Timezone

Set your local timezone (e.g., `America/New_York`). All timestamps throughout the app are converted from UTC to your selected timezone for display. This is display-only -- stored data remains in UTC.

The timezone setting also serves as the default for CSV imports.

## Home Assistant Tab

Configure the connection to Home Assistant for automatic charging session detection and vehicle telemetry ingestion. See the dedicated [Home Assistant Integration](home-assistant.md) guide for full details.

The tab includes:

- **Connection settings** -- HA URL, long-lived access token, VIN override, unit system, auto-connect toggle
- **Connection status** -- Live status badge, event counters, detected VIN, and error display (polls every 10 seconds)
- **Controls** -- Reconnect, disconnect, and history backfill buttons

## CSV Import Tab

Use this tab for bulk imports. It supports template download, timezone selection, auto-mapped columns, preview, inline fixes, duplicate handling, and final import summary.

See the dedicated [CSV Import](csv-import.md) guide for the full flow.
