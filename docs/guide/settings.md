# :lucide-sliders-horizontal: Settings

The settings page (`/settings`) controls charging networks, comparison parameters, and display preferences. All settings take effect immediately.

## Charging Networks

Manage your cost per kWh for each charging network. Each network has:

| Field | Description |
|-------|-------------|
| Name | Network name (e.g., "Home", "Electrify America") |
| Cost | Cost per kWh in dollars |
| Free | Whether this network charges nothing |

Network costs are used to calculate session costs throughout the application. Editing a network cost immediately updates all cost displays.

!!! tip
    The seed script populates default costs for common networks. Edit these to match your actual costs.

## Gas Comparison Settings

Parameters for the gas vehicle savings comparison on the costs page:

| Setting | Description | Example |
|---------|-------------|---------|
| Gas price ($/gallon) | Current gas price in your area | 3.50 |
| Vehicle MPG | The gas vehicle you're comparing against | 25 |

These values are used to calculate what you would have spent driving an equivalent gas vehicle over the same miles.

## Unit Preferences

Choose between US and EU display units:

| Preference | Efficiency unit | Used on |
|-----------|----------------|---------|
| US | mi/kWh | Energy dashboard |
| EU | km/kWh | Energy dashboard |

## Comparison Toggles

Control which comparison sections appear on the costs page:

- **Comparison section** -- Master toggle for the entire savings section
- **Gas comparison** -- Show/hide the gas vehicle comparison
- **Network comparison** -- Show/hide the network cost comparison

Disabling a comparison also skips the database queries for that section, so there's no performance cost for hidden comparisons.
