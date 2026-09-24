# Data Sources

**AUTO-GENERATED — DO NOT EDIT.** Run `uv run python scripts/gen_data_sources_doc.py` to refresh.

Every field ingested by LightningROD is declared in a source adapter's
`DATA_MAP` registry. This page reflects that registry at commit time and is
the observable record of how each value is mapped and converted.

## fcon

| Source Entity | Source Field | Source Unit | DB Table | DB Column | DB Unit | Notes |
|---|---|---|---|---|---|---|
| `telemetry` | `acceleratorPedalPosition` | `%` | `ev_vehicle_status` | `accelerator_position` | `%` |  |
| `telemetry` | `batteryLoadStatus` | `enum` | `ev_battery_status` | `lv_load_status` | `enum` | 12V |
| `telemetry` | `batteryStateOfCharge` | `%` | `ev_battery_status` | `lv_battery_level` | `%` | 12V |
| `telemetry` | `batteryVoltage` | `V` | `ev_battery_status` | `lv_battery_voltage` | `V` | 12V |
| `telemetry` | `brakeTorque` | `Nm` | `ev_vehicle_status` | `brake_torque` | `Nm` |  |
| `telemetry` | `doorLockStatus` | `list` | `ev_vehicle_status` | `door_lock_status` | `list` |  |
| `telemetry` | `gearLeverPosition` | `enum` | `ev_vehicle_status` | `gear_position` | `enum` |  |
| `telemetry` | `ignitionStatus` | `enum` | `ev_vehicle_status` | `ignition_status` | `enum` |  |
| `telemetry` | `odometer` | `km` | `ev_vehicle_status` | `odometer` | `km` | Clamped so it never runs backwards |
| `telemetry` | `outsideTemperature` | `degC` | `ev_vehicle_status` | `outside_temperature` | `degC` | Not ambientTemp, which is unreliable |
| `telemetry` | `seatBeltStatus` | `list` | `ev_vehicle_status` | `seatbelt_status` | `list` |  |
| `telemetry` | `speed` | `kmh` | `ev_vehicle_status` | `speed` | `km/h` |  |
| `telemetry` | `tirePressure` | `list` | `ev_vehicle_status` | `tire_pressure` | `list` | kPa per wheel |
| `telemetry` | `torqueAtTransmission` | `Nm` | `ev_vehicle_status` | `torque_at_transmission` | `Nm` |  |
| `telemetry` | `wheelTorqueStatus` | `enum` | `ev_vehicle_status` | `wheel_torque_status` | `enum` |  |
| `telemetry` | `xevBatteryActualStateOfCharge` | `%` | `ev_battery_status` | `hv_battery_actual_soc` | `%` |  |
| `telemetry` | `xevBatteryCapacity` | `kWh` | `ev_battery_status` | `hv_battery_capacity` | `kWh` |  |
| `telemetry` | `xevBatteryIoCurrent` | `A` | `ev_battery_status` | `hv_battery_amperage` | `A` | Negative while charging |
| `telemetry` | `xevBatteryMaximumRange` | `km` | `ev_battery_status` | `hv_battery_max_range` | `km` |  |
| `telemetry` | `xevBatteryPerformanceStatus` | `enum` | `ev_battery_status` | `performance_status` | `enum` |  |
| `telemetry` | `xevBatteryRange` | `km` | `ev_battery_status` | `hv_battery_range` | `km` |  |
| `telemetry` | `xevBatteryStateOfCharge` | `%` | `ev_battery_status` | `hv_battery_soc` | `%` |  |
| `telemetry` | `xevBatteryTemperature` | `degC` | `ev_battery_status` | `hv_battery_temperature` | `degC` |  |
| `telemetry` | `xevBatteryVoltage` | `V` | `ev_battery_status` | `hv_battery_voltage` | `V` |  |
| `telemetry` | `xevTractionMotorCurrent` | `A` | `ev_battery_status` | `motor_amperage` | `A` |  |
| `telemetry` | `xevTractionMotorVoltage` | `V` | `ev_battery_status` | `motor_voltage` | `V` |  |
| `telemetry` | `yawRate` | `deg/s` | `ev_vehicle_status` | `yaw_rate` | `deg/s` |  |

## ha_fordpass

| Source Entity | Source Field | Source Unit | DB Table | DB Column | DB Unit | Notes |
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

_No mappings registered._
