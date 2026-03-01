# :lucide-dollar-sign: Cost Analytics

The costs page (`/costs`) breaks down your charging expenses and shows what you're saving compared to gas or different charging networks.

<!-- TODO: Add screenshot of costs page -->

## Lifetime Summary

Cards at the top show:

- **Total cost** across all sessions
- **Breakdown by location** -- home, work, public, and free charging
- **Per-network costs** -- spending at each charging network

## Time Range Filter

Filter costs by time period: 7d, 30d, 90d, YTD, 1y, or all-time. The summary cards and charts update to reflect the selected range.

## Charts

Two interactive Plotly charts:

- **Monthly cost trend** -- Bar chart showing spending over time
- **Network breakdown** -- Cost distribution across charging networks

## Savings Comparisons

Two comparison modes, each togglable from [Settings](settings.md):

### Gas Comparison

Shows what you would have spent driving a gas vehicle over the same miles. Uses your configured MPG and gas price to calculate equivalent fuel cost, then displays the savings.

### Network Comparison

Shows what you would have paid if all sessions were charged at a reference network cost (e.g., public DC fast charging). Select any configured network from the dropdown to see the comparison.

## Session Costs

Individual session costs are calculated at query time, not stored. When you change a network cost in settings, all displayed costs update immediately.

The calculation uses:

1. **Stored cost** -- If the session has an explicit cost value from the data source, that's used directly.
2. **Calculated cost** -- Otherwise, the session's energy (kWh) is multiplied by the network cost for its location.
3. **Free** -- Sessions at free locations show $0.

Cost indicators appear in the session list and the session detail drawer.
