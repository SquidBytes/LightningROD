# Cost Analytics

The costs page (`/charging/costs`) breaks down your charging expenses and shows how they compare with gas or other charging networks.

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

Filter costs by time period: 7d, 30d, 90d, YTD, 1y, or all-time. The cards and charts update together.

## Charts

Two interactive Plotly charts:

- **Monthly cost trend** -- Bar chart showing spending over time
- **Network breakdown** -- Cost distribution across charging networks, using each network's configured color

Charts use the same network colors you see elsewhere in the app.

## Savings Comparisons

Two comparison modes, both controlled from [Settings](settings.md):

### Gas Comparison

Shows what you would have spent driving the configured ICE comparison vehicle over the same miles, using your gas price history.

### Network Comparison

Shows what you would have paid if every session had used a reference network's rate. Choose any configured network from the dropdown.

## How Session Costs Work

Session costs follow this order:

1. **Session marked free** -- Display cost is `$0.00`
2. **Stored session cost** -- Manual/imported values are used for display
3. **Network marked free** -- Display cost is `$0.00`
4. **Location override rate** -- `energy_kwh * location.cost_per_kwh`
5. **Network rate** -- `energy_kwh * network.cost_per_kwh` (or active subscription member rate)
6. **No rate data** -- session is unconfigured for cost and excluded from resolved totals

Estimated cost is tracked separately so LightningROD can compare actual and estimated values.

If rates or subscriptions change in Settings, you can recalculate costs for the affected sessions.
