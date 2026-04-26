# Home Assistant Integration

LightningROD connects to [Home Assistant](https://www.home-assistant.io/) through WebSocket and reads FordPass vehicle data from the [ha-fordpass](https://github.com/marq24/ha_fordpass) integration. This is the main way automatic charging session detection works.


## Prerequisites

- A running Home Assistant instance with [ha-fordpass](https://github.com/marq24/ha_fordpass) installed and configured
- A **long-lived access token** from Home Assistant
- Network access between LightningROD and your Home Assistant instance

## Configuration

Go to **Settings > Home Assistant** to configure the connection.

| Field | Description |
|-------|-------------|
| HA URL | Full URL to your Home Assistant instance (e.g., `http://homeassistant.local:8123`) |
| Access Token | Long-lived access token (displayed masked after saving) |
| VIN Override | Optional override for the auto-detected VIN |
| Unit System | Auto-detect (default), Metric, or Imperial |
| Auto-connect | Connect automatically when the app starts (default: on) |

After saving, LightningROD connects to Home Assistant, authenticates, and starts receiving events.

### VIN Auto-Detection

LightningROD automatically detects your vehicle's VIN by looking for FordPass entity IDs that match `sensor.fordpass_{vin}_*`. The detected VIN appears in the connection status area. Use VIN Override if the wrong vehicle is picked.

### Unit System

LightningROD checks Home Assistant to see whether your instance uses metric or imperial units. Values are stored in metric and converted back for display. You can override the detected unit system if needed.

## Connection Status

The status section below the configuration form shows live connection information and updates every 10 seconds:

| Metric | Description |
|--------|-------------|
| Status badge | Connected, Connecting, Reconnecting, or Disconnected |
| Events Processed | Total sensor events received since last connect |
| Last Event | Timestamp of the most recent event |
| Errors | Count of processing errors |
| Last Successful Write | Timestamp of the last database write |
| Detected VIN | Auto-extracted vehicle identification |
| HA Unit System | Detected length and temperature units |

### Controls

- **Reconnect** -- Disconnect and reconnect
- **Disconnect** -- Stop the connection
- **Backfill History** -- Fetch historical charging sessions and gas price readings from Home Assistant's recorder

## How It Works

### Real-Time Data Flow

1. LightningROD opens a persistent WebSocket connection to Home Assistant
2. On connect, it fetches a full snapshot of all entities
3. It subscribes to `state_changed` events for ongoing updates
4. Incoming sensor events are routed to the right handlers

### Reconnection

If the connection drops, LightningROD reconnects automatically with increasing delays up to 60 seconds. When it reconnects, it fetches a fresh snapshot to fill any gaps.

If authentication fails, reconnection stops until you update the token in settings.

### Home Location Auto-Populate

On every successful connect, LightningROD pulls the `home` zone coordinates and name from Home Assistant and writes them into your app settings (`home_latitude`, `home_longitude`, `home_location_name`). If you already set home manually, it is left alone. These values help tag sessions that happened at home.

### Charging Session Detection

Charging sessions are created automatically from `energytransferlogentry` events. Each event fires once per completed charge and includes:

- Energy delivered (kWh)
- SOC at start and end
- Charge duration and plug-in duration
- Power stats (min, max, weighted average kW)
- Location with address, coordinates, and network name
- Charger type (AC Level 1, AC Level 2, DC Fast)

Sessions created from Home Assistant are tagged with a **HASS** data source badge on the sessions page.

Duplicate detection keeps the same session from being recorded twice. Sessions are matched on start time, energy delivered, and source system.

### Vehicle Telemetry

In addition to charging sessions, LightningROD ingests 29 FordPass sensors covering:

- **Vehicle status** -- Odometer, speed, ignition, gear position, brake status, temperatures, acceleration, deep sleep, connectivity
- **Battery** -- High-voltage SOC, range, 12V battery level, last energy consumed
- **Charging state** -- Plug status, charging status with station details
- **Tire pressure** -- All four wheels plus system state
- **GPS** -- Latitude and longitude (logged, not persisted)

Vehicle and battery telemetry is batched so multiple sensor updates are written as one database record.

## History Backfill

Click **Backfill History** to fetch historical states from Home Assistant's REST API. It pulls as much history as your recorder retains. It covers:

- `energytransferlogentry` — creates charging session records for charges before LightningROD was connected
- Configured **Gas Price Sensors** (station and/or average) — ingested into the gas price history used on the Costs page

Duplicate detection also applies during backfill, so existing sessions and gas readings are skipped automatically.

!!! tip "First-time setup"
    After you set up the connection for the first time, run the backfill to import your historical charging and gas price data. After that, new sessions and readings come in automatically.
