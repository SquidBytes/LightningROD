# Data Sources

**AUTO-GENERATED — DO NOT EDIT.** Run `uv run python scripts/gen_data_sources_doc.py` to refresh.

Every field ingested by LightningROD is declared in a source adapter's
`FIELD_CONTRACTS` registry. This page is a reflection of that registry at
commit time and serves as the observable unit contract.

## ha_fordpass

| Source Entity | Source Attribute | Source Unit | DB Table | DB Column | Target Unit | Notes |
|---|---|---|---|---|---|---|
| `sensor.fordpass_{vin}_cabintemperature` | `cabinTemperature` | `degC` | `ev_vehicle_status` | `cabin_temperature` | `degC` | Time-series cabin temp from per-sensor cabintemperature entity; ha-fordpass localizes per HA unit_system. |
| `sensor.fordpass_{vin}_elveh` | `tripAmbientTemp` | `degC` | `ev_trip_metrics` | `ambient_temp` | `degC` | elveh temp attr: HA localizes per unit_system |
| `sensor.fordpass_{vin}_elveh` | `tripCabinTemp` | `degC` | `ev_trip_metrics` | `cabin_temp` | `degC` | elveh temp attr: HA localizes per unit_system (imperial->degF, metric->degC) |
| `sensor.fordpass_{vin}_elveh` | `tripEfficiency` | `km` | `ev_trip_metrics` | `efficiency` | `km` | Read-time fallback — elveh attribute in HA-preferred distance unit (km/kWh or mi/kWh). Adapter derives the per-event source_unit from new_state.attributes.unit_of_measurement at read-time. NOT from a process-global flag. This contract's declared source_unit is the DEFAULT when the event carries no uom; concrete conversion routes through adapter._resolve_source_unit(). |
| `sensor.fordpass_{vin}_elveh` | `tripOutsideAirAmbientTemp` | `degC` | `ev_trip_metrics` | `outside_air_temp` | `degC` | elveh temp attr: HA localizes per unit_system |
| `sensor.fordpass_{vin}_elveh` | `tripRangeRegenerated` | `km` | `ev_trip_metrics` | `range_regenerated` | `km` | Read-time fallback — elveh attribute; see tripEfficiency contract |
| `sensor.fordpass_{vin}_elvehcharging` | `batteryTemperature` | `degC` | `ev_battery_status` | `hv_battery_temperature` | `degC` | Live charging battery temp; ha-fordpass localizes per unit_system |
| `sensor.fordpass_{vin}_elvehcharging` | `batteryTemperature` | `degC` | `ev_charging_session` | `battery_temp_start` | `degC` | Cached from elvehcharging; applied at session-write time |
| `sensor.fordpass_{vin}_elvehcharging` | `batteryTemperature` | `degC` | `ev_charging_session` | `battery_temp_end` | `degC` | Cached from elvehcharging; mirrored to start/end (single snapshot) |
| `sensor.fordpass_{vin}_energytransferlogentry` | `plugDetails.totalDistanceAdded` | `km` | `ev_charging_session` | `distance_added` | `km` | HA-converted per ha-fordpass source (fordpass_handler.py:get_energy_transfer_log_attrs calls localize_distance on this field). Imperial HA -> miles, metric HA -> km. The adapter resolves the source unit per-event from ha_config.unit_system; to_metric handles the mi->km conversion back to canonical. |
| `sensor.fordpass_{vin}_events` | `xev-key-off-trip-segment-data.ambient_temperature` | `degC` | `ev_trip_metrics` | `ambient_temp` | `degC` | Canonical metric source; Ford API key is ambient_temperature (raw °C) |
| `sensor.fordpass_{vin}_events` | `xev-key-off-trip-segment-data.cabin_temperature` | `degC` | `ev_trip_metrics` | `cabin_temp` | `degC` | Canonical metric source; Ford API key is cabin_temperature (raw °C) |
| `sensor.fordpass_{vin}_events` | `xev-key-off-trip-segment-data.distance_traveled` | `km` | `ev_trip_metrics` | `distance` | `km` | Canonical source; replaces elveh.tripDistanceTraveled |
| `sensor.fordpass_{vin}_events` | `xev-key-off-trip-segment-data.energy_consumed` | `Wh` | `ev_trip_metrics` | `energy_consumed` | `kWh` | Canonical source; Wh -> kWh via to_metric |
| `sensor.fordpass_{vin}_events` | `xev-key-off-trip-segment-data.outside_air_ambient_temperature` | `degC` | `ev_trip_metrics` | `outside_air_temp` | `degC` | Canonical metric source; Ford API key is outside_air_ambient_temperature (raw °C) |
| `sensor.fordpass_{vin}_metrics` | `acceleration` | `m/s2` | `ev_vehicle_status` | `acceleration` | `m/s2` | Longitudinal acceleration. SI passthrough. |
| `sensor.fordpass_{vin}_metrics` | `brakeTorque` | `Nm` | `ev_vehicle_status` | `brake_torque` | `Nm` | Brake torque. SI passthrough; no localization in ha-fordpass. |
| `sensor.fordpass_{vin}_metrics` | `odometer` | `km` | `ev_vehicle_status` | `odometer` | `km` | Cumulative odometer; ha-fordpass localizes per HA unit_system (imperial -> mi, metric -> km). |
| `sensor.fordpass_{vin}_metrics` | `speed` | `kmh` | `ev_vehicle_status` | `speed` | `kmh` | Instantaneous vehicle speed; ha-fordpass localizes per unit_system. Zero when parked. |
| `sensor.fordpass_{vin}_metrics` | `xevBatteryMaximumRange` | `km` | `ev_battery_status` | `hv_battery_max_range` | `km` | Canonical metric source; replaces elveh.maximumBatteryRange |
| `sensor.fordpass_{vin}_metrics` | `xevBatteryRange` | `km` | `ev_battery_status` | `hv_battery_range` | `km` | Canonical metric source; replaces elveh state reading |
| `sensor.fordpass_{vin}_metrics` | `yawRate` | `deg/s` | `ev_vehicle_status` | `yaw_rate` | `deg/s` | Yaw rate. Passthrough; no localization. |
| `sensor.fordpass_{vin}_outsidetemp` | `ambientTemp` | `degC` | `ev_charging_session` | `ambient_temp_start` | `degC` | Cached from outsidetemp sensor; applied at session-write time |
| `sensor.fordpass_{vin}_outsidetemp` | `ambientTemp` | `degC` | `ev_charging_session` | `ambient_temp_end` | `degC` | Cached from outsidetemp sensor; mirrored to start/end |
| `sensor.fordpass_{vin}_outsidetemp` | `ambientTemp` | `degC` | `ev_vehicle_status` | `outside_temperature` | `degC` | Time-series ambient temp from per-sensor outsidetemp entity; ha-fordpass localizes per HA unit_system. Note: shares the same source as ev_charging_session.ambient_temp_start/end (different cache discipline — vehicle_status writes per snapshot). |

## ha_gas_price

_No contracts registered._
