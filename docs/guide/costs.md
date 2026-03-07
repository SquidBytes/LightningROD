# Cost Analytics

The costs page (`/costs`) breaks down your charging expenses and shows savings compared to gas or other charging networks.

## Summary Cards

Cards at the top show:

- **Total cost** across all sessions (actual + estimated)
- **Breakdown by location type** -- home, work, public, and free charging
- **Per-network costs** -- spending at each charging network with color badges

## Time Range Filter

Filter costs by time period: 7d, 30d, 90d, YTD, 1y, or all-time. Summary cards and charts update together.

## Charts

Two interactive Plotly charts:

- **Monthly cost trend** -- Bar chart showing spending over time
- **Network breakdown** -- Cost distribution across charging networks, using each network's configured color

Charts use network-specific colors for consistent visual identification across the app.

## Savings Comparisons

Two comparison modes, each togglable from [Settings](settings.md):

### Gas Comparison

Calculates what you would have spent driving a gas vehicle over the same miles using your configured MPG and gas price, then shows the savings.

### Network Comparison

Shows what you would have paid if all sessions were charged at a reference network's cost. Select any configured network from the dropdown to compare.

## How Session Costs Work

Session costs follow a priority cascade:

1. **User-entered cost** -- If you manually set a cost on a session, that value is used
2. **Estimated cost** -- Calculated from the location's `cost_per_kwh` (if set), falling back to the network's `cost_per_kwh`, multiplied by `energy_kwh`
3. **Free** -- Sessions at free networks show $0

The cost page tracks actual and estimated costs separately so you can see how much of your cost data is real vs calculated.

When you change a network or location cost in settings, estimated costs can be recalculated for affected sessions.
