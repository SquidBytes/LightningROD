# Home Assistant Integration

LightningROD connects to [Home Assistant](https://www.home-assistant.io/) via WebSocket to ingest real-time FordPass vehicle data from the [ha-fordpass](https://github.com/marq24/ha_fordpass) integration. This is the primary method for automatic charging session detection.

## Prerequisites

- A running Home Assistant instance with [ha-fordpass](https://github.com/marq24/ha_fordpass) installed and configured
- A **long-lived access token** from Home Assistant (created at Profile > Security > Long-Lived Access Tokens)
- Network connectivity between LightningROD and your Home Assistant instance

## Configuration

Navigate to **Settings > Home Assistant** to configure the connection.

| Field | Description |
|-------|-------------|
| HA URL | Full URL to your Home Assistant instance (e.g., `http://homeassistant.local:8123`) |
| Access Token | Long-lived access token (displayed masked after saving) |
| VIN Override | Optional -- override the auto-detected VIN |
| Unit System | Auto-detect (default), Metric, or Imperial |
| Auto-connect | Connect automatically when the app starts (default: on) |

After saving, LightningROD connects to HA's WebSocket API, authenticates, and begins receiving events.

### VIN Auto-Detection

LightningROD automatically detects your vehicle's VIN by scanning FordPass entity IDs for the pattern `sensor.fordpass_{vin}_*`. The detected VIN is displayed in the connection status section. Use the VIN Override field if auto-detection picks the wrong vehicle.

### Unit System

LightningROD queries HA's configuration to detect whether your instance uses metric or imperial units. All values are normalized to metric (km, Celsius, kWh) for storage and converted back for display. You can override the detected unit system if needed.

## Connection Status

The status section below the configuration form shows live connection information, updated every 10 seconds:

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

- **Reconnect** -- Disconnect and reconnect the WebSocket
- **Disconnect** -- Stop the WebSocket connection entirely
- **Backfill History (30 days)** -- Fetch historical charging sessions (available when connected)

## How It Works

### Real-Time Data Flow

1. LightningROD opens a persistent WebSocket connection to HA
2. On connect, it fetches a full state snapshot of all entities
3. It subscribes to `state_changed` events for ongoing updates
4. Incoming sensor events are dispatched to handlers by entity type

### Reconnection

If the connection drops, LightningROD reconnects automatically with exponential backoff (1s, 2s, 4s, up to 60s max). On reconnect, a fresh state snapshot is fetched to fill any data gaps.

Authentication errors (bad token) stop reconnection entirely -- you'll need to update the token in settings.

### Charging Session Detection

Charging sessions are created automatically from `energytransferlogentry` events. Each event fires once per completed charge and contains rich data:

- Energy delivered (kWh)
- SOC at start and end
- Charge duration and plug-in duration
- Power stats (min, max, weighted average kW)
- Location with address, coordinates, and network name
- Charger type (AC Level 1, AC Level 2, DC Fast)

Sessions created from HA are tagged with a **HASS** data source badge on the sessions page.

**Duplicate detection** prevents the same session from being recorded twice. Sessions are matched on start time, energy delivered, and source system.

### Vehicle Telemetry

In addition to charging sessions, LightningROD ingests 29 FordPass sensors covering:

- **Vehicle status** -- Odometer, speed, ignition, gear position, brake status, temperatures, acceleration, deep sleep, connectivity
- **Battery** -- High-voltage SOC, range, 12V battery level, last energy consumed
- **Charging state** -- Plug status, charging status with station details
- **Tire pressure** -- All four wheels plus system state
- **GPS** -- Latitude and longitude (logged, not persisted)

Vehicle and battery telemetry is batched -- multiple sensor updates are accumulated and written as a single database record to reduce write volume.

## History Backfill

Click **Backfill History (30 days)** to fetch historical `energytransferlogentry` states from HA's REST API. This creates charging session records for charges that occurred before LightningROD was connected.

Duplicate detection applies during backfill -- sessions that already exist are skipped.

!!! tip "First-time setup"
    After configuring the HA connection for the first time, use the backfill button to import your recent charging history. Going forward, sessions are created automatically in real time.
