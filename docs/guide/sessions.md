# Charging Sessions

The sessions page (`/sessions`) is the primary interface for viewing and managing your charging history.

## Session Table

Sessions are displayed in a paginated table with configurable page size (25, 50, or 100 per page). Columns shown:

- Date and time (displayed in your configured timezone)
- Location name
- Network (with color badge)
- Charge type (AC/DC)
- Energy delivered (kWh)
- Cost (actual or estimated with `~` prefix)
- Duration

### Sorting

Click any column header to sort. Headers cycle through three states: none, ascending, descending. An arrow indicator shows the current sort direction. Sort state persists through pagination and filter changes.

### Filtering

The filter bar supports multiple simultaneous filters:

| Filter | Options |
|--------|---------|
| Date range | Presets: 7d, 30d, 90d, YTD, 1y, All, or custom start/end dates |
| Charge type | AC, DC |
| Location type | Home, Work, Public, and other location types |
| Network | Multi-select checkboxes with color badges |

Active filters display as chips below the filter bar. The summary bar above the table updates to reflect filtered totals.

## Adding Sessions

Click the **Add Session** button above the table. A modal opens with fields for date, energy (kWh), cost, network, location, charge type, duration, SOC start/end, and notes.

Manually added sessions are tagged with a "Manual Entry" data source badge.

## Editing Sessions

Click any row to open the session detail drawer. From the drawer, click **Edit** to open the edit modal.

The edit modal is organized into three tabs:

| Tab | Fields |
|-----|--------|
| Basics | Date, network, energy, cost |
| Details | Power metrics, SOC, duration, connector, EVSE data, stall selection |
| Notes | Free-text notes |

A data source badge in the top-right corner shows the origin of the session data (Manual Entry, Imported, HASS, or Edited).

## Deleting Sessions

Open a session's edit modal and click **Delete**. A confirmation dialog prevents accidental deletion.

## Session Detail Drawer

Click any table row to open the slide-out drawer with full session details:

- Session info (date/time, duration, charge type, data source)
- Energy and SOC (kWh delivered, SOC start/end, range added)
- Cost breakdown card (actual cost, estimated cost, actual $/kWh, difference)
- Network and location info with color badge
- EVSE / Charger section (voltage, amperage, power, energy, max power, rated capacity, stall label)
- Charging loss and utilization metrics when EVSE data is available

Use the prev/next arrows to navigate between sessions without closing the drawer.

## Cost Display

Sessions display cost in two ways:

- **Actual cost** -- User-entered or imported cost, shown normally
- **Estimated cost** -- Calculated from network or location cost_per_kwh, shown with a `~` prefix and "Est." badge (e.g., `~$12.50`)

When both exist, the drawer shows a cost breakdown card with the difference between actual and estimated.
