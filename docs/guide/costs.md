# Cost Analytics

The costs page (`/costs`) breaks down your charging expenses and shows savings compared to gas or other charging networks.

![costs](../assets/images/lr_costs.gif)

## Summary Cards

Cards at the top show:

- **Total Spent** -- Sum of resolved session costs in scope
- **Total Energy** -- kWh covered by resolved-cost sessions
- **Free Charging** -- Free-session count and free kWh

Below that, LightningROD shows:

- **Actual vs Estimated** breakdown
- **Cost by Network** cards with network colors
- **Subscription Savings** when member-rate periods apply

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

Calculates what you would have spent driving a configured ICE comparison vehicle over the same miles, using your gas price history (station and/or average).

### Network Comparison

Shows what you would have paid if all sessions were charged at a reference network's cost. Select any configured network from the dropdown to compare.

## How Session Costs Work

Session costs follow a priority cascade:

1. **Session marked free** -- Display cost is `$0.00`
2. **Stored session cost** -- Manual/imported values are used for display
3. **Network marked free** -- Display cost is `$0.00`
4. **Location override rate** -- `energy_kwh * location.cost_per_kwh`
5. **Network rate** -- `energy_kwh * network.cost_per_kwh` (or active subscription member rate)
6. **No rate data** -- session is unconfigured for cost and excluded from resolved totals

Estimated cost is tracked separately from display cost to support actual-vs-estimated analysis.

When rates or subscriptions change in Settings, costs can be recalculated for affected sessions.
