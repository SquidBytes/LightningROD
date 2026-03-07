# Settings

The settings page (`/settings`) is organized into tabs for managing networks, preferences, and data import. All settings take effect immediately.

## Network Management

Networks are the primary organizational unit for charging locations and costs. Each network has:

| Field | Description |
|-------|-------------|
| Name | Network name (e.g., "Home", "Electrify America") |
| Cost per kWh | Default electricity rate for this network |
| Color | Hex color for badges and charts |
| Free | Whether this network charges nothing |
| Notes | Optional description |

The network table shows each network with its color badge, location count, and session count. Networks are read-only in the table -- click **Edit** to open the network modal.

### Network Edit Modal

The network modal has two tabs:

**Details** -- Edit name, cost per kWh, color, free toggle, and notes.

**Locations** -- Manage locations belonging to this network. Each location has:

| Field | Description |
|-------|-------------|
| Name | Location name (e.g., "Main St Station") |
| Location type | Home, work, public, retail, destination, highway, other |
| Address | Street address |
| Latitude/Longitude | GPS coordinates |
| Cost per kWh | Optional override of network cost |
| Notes | Optional description |

Locations can override the network's cost_per_kwh. When a location has its own cost, that takes priority over the network default for sessions at that location.

### Charger Stalls

Each location can have multiple charger stalls with different specs. Stalls are managed via a tab in the location edit area:

| Field | Description |
|-------|-------------|
| Label | User-defined name (e.g., "350kW CCS", "L2 West Wall") |
| Charger type | L1, L2, or DCFC |
| Rated kW | Maximum rated power |
| Voltage / Amperage | Typical electrical specs |
| Connector type | CCS, CHAdeMO, J1772, NACS, Tesla |
| Default | Auto-select this stall when the location is chosen |

When editing a session, selecting a location populates a stall dropdown. Choosing a stall auto-fills EVSE fields (rated kW, voltage, amperage) on the session.

Popular networks include pre-built charger templates. Click "Pre-fill from [Network]" when adding stalls to auto-populate known configurations.

## General Settings

### Gas Comparison

Parameters for the gas vehicle savings comparison on the costs page:

| Setting | Description | Example |
|---------|-------------|---------|
| Gas price ($/gallon) | Current gas price in your area | 3.50 |
| Vehicle MPG | The gas vehicle you're comparing against | 25 |

### Unit Preferences

| Preference | Efficiency unit | Used on |
|-----------|----------------|---------|
| US | mi/kWh | Energy dashboard |
| EU | km/kWh | Energy dashboard |

### Timezone

Set your local timezone (e.g., `America/New_York`). All timestamps throughout the app are converted from UTC to your selected timezone for display. This is display-only -- stored data remains in UTC.

The timezone setting also serves as the default for CSV imports.

### Comparison Toggles

Control which comparison sections appear on the costs page:

- **Comparison section** -- Master toggle for the entire savings section
- **Gas comparison** -- Show/hide the gas vehicle comparison
- **Network comparison** -- Show/hide the network cost comparison

Disabling a comparison skips its database queries.

## Home Assistant Tab

Configure the connection to Home Assistant for automatic charging session detection and vehicle telemetry ingestion. See the dedicated [Home Assistant Integration](home-assistant.md) guide for full details.

The tab includes:

- **Connection settings** -- HA URL, long-lived access token, VIN override, unit system, auto-connect toggle
- **Connection status** -- Live status badge, event counters, detected VIN, and error display (polls every 10 seconds)
- **Controls** -- Reconnect, disconnect, and history backfill buttons

## CSV Import Tab

See the dedicated [CSV Import](csv-import.md) guide.
