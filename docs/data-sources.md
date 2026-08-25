# Data Sources

**AUTO-GENERATED — DO NOT EDIT.** Run `uv run python scripts/gen_data_sources_doc.py` to refresh.

Every field ingested by LightningROD is declared in a source adapter's
`FIELD_CONTRACTS` registry. This page is a reflection of that registry at
commit time and serves as the observable unit contract.

## ha_fordpass

| Source Entity | Source Attribute | Source Unit | DB Table | DB Column | Target Unit | Notes |
|---|---|---|---|---|---|---|
| `sensor.fordpass_{vin}_cabintemperature` | `state` | `degC` | `ev_vehicle_status` | `cabin_temperature` | `degC` | Time-series cabin temp from the cabintemperature entity state; the entity carries no cabinTemperature attribute. |
| `sensor.fordpass_{vin}_elveh` | `maximumBatteryCapacity` | `Wh` | `ev_battery_status` | `hv_battery_capacity` | `kWh` | Elveh mirror of xevBatteryCapacity; same mixed Wh/kWh autoscale |
| `sensor.fordpass_{vin}_elveh` | `maximumBatteryRange` | `km` | `ev_battery_status` | `hv_battery_max_range` | `km` | Elveh fallback for metrics xevBatteryMaximumRange; localize_distance |
| `sensor.fordpass_{vin}_elveh` | `tripAmbientTemp` | `degC` | `ev_trip_metrics` | `ambient_temp` | `degC` | elveh temp attr: HA localizes per unit_system |
| `sensor.fordpass_{vin}_elveh` | `tripCabinTemp` | `degC` | `ev_trip_metrics` | `cabin_temp` | `degC` | elveh temp attr: HA localizes per unit_system (imperial->degF, metric->degC) |
| `sensor.fordpass_{vin}_elveh` | `tripDistanceTraveled` | `km` | `ev_trip_metrics` | `distance` | `km` | Legacy-fallback trip distance; ha-fordpass localizes per HA unit_system |
| `sensor.fordpass_{vin}_elveh` | `tripEfficiency` | `km` | `ev_trip_metrics` | `efficiency` | `km` | tripDistanceTraveled / tripEnergyConsumed as computed by ha-fordpass, so km/kWh on metric HA and mi/kWh on imperial HA; the km<->mi factor converts it to canonical km/kWh. |
| `sensor.fordpass_{vin}_elveh` | `tripOutsideAirAmbientTemp` | `degC` | `ev_trip_metrics` | `outside_air_temp` | `degC` | elveh temp attr: HA localizes per unit_system |
| `sensor.fordpass_{vin}_elveh` | `tripRangeRegenerated` | `km` | `ev_trip_metrics` | `range_regenerated` | `km` | From metrics tripXevBatteryRangeRegenerated via localize_distance |
| `sensor.fordpass_{vin}_elvehcharging` | `batteryTemperature` | `degC` | `ev_battery_status` | `hv_battery_temperature` | `degC` | Live charging battery temp; ha-fordpass localizes per unit_system |
| `sensor.fordpass_{vin}_elvehcharging` | `batteryTemperature` | `degC` | `ev_charging_session` | `battery_temp_start` | `degC` | Cached from elvehcharging; applied at session-write time |
| `sensor.fordpass_{vin}_elvehcharging` | `batteryTemperature` | `degC` | `ev_charging_session` | `battery_temp_end` | `degC` | Cached from elvehcharging; mirrored to start/end (single snapshot) |
| `sensor.fordpass_{vin}_energytransferlogentry` | `plugDetails.totalDistanceAdded` | `km` | `ev_charging_session` | `distance_added` | `km` | HA-converted per ha-fordpass source (fordpass_handler.py:get_energy_transfer_log_attrs calls localize_distance on this field). Imperial HA -> miles, metric HA -> km. The adapter resolves the source unit per-event from ha_config.unit_system; to_metric handles the mi->km conversion back to canonical. |
| `sensor.fordpass_{vin}_events` | `xev-key-off-trip-segment-data.ambient_temperature` | `degC` | `ev_trip_metrics` | `ambient_temp` | `degC` | Canonical metric source; Ford API key is ambient_temperature (raw °C) |
| `sensor.fordpass_{vin}_events` | `xev-key-off-trip-segment-data.cabin_temperature` | `degC` | `ev_trip_metrics` | `cabin_temp` | `degC` | Canonical metric source; Ford API key is cabin_temperature (raw °C) |
| `sensor.fordpass_{vin}_events` | `xev-key-off-trip-segment-data.distance_traveled` | `km` | `ev_trip_metrics` | `distance` | `km` | Canonical source; replaces elveh.tripDistanceTraveled |
| `sensor.fordpass_{vin}_events` | `xev-key-off-trip-segment-data.energy_consumed` | `Wh` | `ev_trip_metrics` | `energy_consumed` | `kWh` | Canonical source; Wh -> kWh via to_metric |
| `sensor.fordpass_{vin}_events` | `xev-key-off-trip-segment-data.outside_air_ambient_temperature` | `degC` | `ev_trip_metrics` | `outside_air_temp` | `degC` | Canonical metric source; Ford API key is outside_air_ambient_temperature (raw °C) |
| `sensor.fordpass_{vin}_metrics` | `tripXevBatteryRangeRegenerated` | `km` | `ev_trip_metrics` | `range_regenerated` | `km` | Raw API passthrough (km); backfills the newest trip row |
| `sensor.fordpass_{vin}_metrics` | `xevBatteryCapacity` | `Wh` | `ev_battery_status` | `hv_battery_capacity` | `kWh` | Mixed Wh/kWh across ha-fordpass versions; magnitude-autoscaled to kWh |
| `sensor.fordpass_{vin}_metrics` | `xevBatteryMaximumRange` | `km` | `ev_battery_status` | `hv_battery_max_range` | `km` | Canonical metric source; replaces elveh.maximumBatteryRange |
| `sensor.fordpass_{vin}_metrics` | `xevBatteryRange` | `km` | `ev_battery_status` | `hv_battery_range` | `km` | Canonical metric source; replaces elveh state reading |
| `sensor.fordpass_{vin}_outsidetemp` | `state` | `degC` | `ev_charging_session` | `ambient_temp_start` | `degC` | Cached from the outsidetemp state; applied at session-write time |
| `sensor.fordpass_{vin}_outsidetemp` | `state` | `degC` | `ev_charging_session` | `ambient_temp_end` | `degC` | Cached from the outsidetemp state; mirrored to start/end |
| `sensor.fordpass_{vin}_outsidetemp` | `state` | `degC` | `ev_vehicle_status` | `outside_temperature` | `degC` | Time-series ambient temp from the outsidetemp entity state. Shares its source with ev_charging_session.ambient_temp_start/end (different cache discipline — vehicle_status writes per snapshot). |

## ha_gas_price

_No contracts registered._
