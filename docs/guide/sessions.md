# :lucide-battery-charging: Charging Sessions

The sessions page (`/sessions`) is the primary way to browse your charging history.

<!-- TODO: Add screenshot of sessions page -->

## Session List

All charging sessions are displayed in a paginated table (25 per page) showing:

- Date and time
- Location name
- Charge type (AC/DC)
- Energy delivered (kWh)
- Cost
- Duration

## Filtering

The filter bar at the top of the page lets you narrow results. Filters update the list instantly without a full page reload.

### Date Range

Choose from presets or set a custom range:

| Preset | Description |
|--------|-------------|
| 7d | Last 7 days |
| 30d | Last 30 days |
| 90d | Last 90 days |
| YTD | Year to date |
| 1y | Last year |
| All | All sessions |
| Custom | Pick specific start and end dates |

### Charge Type

Filter by `AC` or `DC` charging.

### Location Type

Filter by `home`, `work`, or `public` locations.

All filters can be combined. The summary bar above the table updates to reflect the filtered totals.

## Session Detail

Click any row to open a slide-out drawer showing all available fields for that session, organized into sections:

- Session info (start/end time, duration, type)
- Energy (kWh delivered, SOC start/end, range added)
- Cost (calculated cost with network cost breakdown)
- Location (name, type, coordinates if available)
- Vehicle (VIN, device ID)
- Metadata (source, ingestion timestamp)

Use the prev/next arrows in the drawer to navigate between sessions without closing it.
