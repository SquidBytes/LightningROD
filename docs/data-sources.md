# Data Sources

**AUTO-GENERATED — DO NOT EDIT.** Run `uv run python scripts/gen_data_sources_doc.py` to refresh.

Every field ingested by LightningROD is declared in a source adapter's
`FIELD_CONTRACTS` registry. This page is a reflection of that registry at
commit time and serves as the observable unit contract.

## ha_fordpass

| Source Entity | Source Attribute | Source Unit | DB Table | DB Column | Target Unit | Notes |
|---|---|---|---|---|---|---|
| `sensor.fordpass_{vin}_elveh` | `tripEfficiency` | `km` | `ev_trip_metrics` | `efficiency` | `km` | Read-time fallback — elveh attribute in HA-preferred distance unit (km/kWh or mi/kWh). Adapter derives the per-event source_unit from new_state.attributes.unit_of_measurement at read-time. NOT from a process-global flag. This contract's declared source_unit is the DEFAULT when the event carries no uom; concrete conversion routes through adapter._resolve_source_unit(). |
| `sensor.fordpass_{vin}_elveh` | `tripRangeRegenerated` | `km` | `ev_trip_metrics` | `range_regenerated` | `km` | Read-time fallback — elveh attribute; see tripEfficiency contract |
| `sensor.fordpass_{vin}_energytransferlogentry` | `batteryTemperature` | `degC` | `ev_battery_status` | `hv_battery_temperature` | `degC` | Only exposed on energytransferlogentry payload; integration emits °C |
| `sensor.fordpass_{vin}_energytransferlogentry` | `batteryTemperature` | `degC` | `ev_charging_session` | `battery_temp_start` | `degC` | ha-fordpass emits °C on the energytransferlogentry payload |
| `sensor.fordpass_{vin}_energytransferlogentry` | `batteryTemperature` | `degC` | `ev_charging_session` | `battery_temp_end` | `degC` | ha-fordpass exposes a single snapshot value; start/end mirror until HA emits discrete timeseries snapshots |
| `sensor.fordpass_{vin}_energytransferlogentry` | `outsidetemp` | `degC` | `ev_charging_session` | `ambient_temp_start` | `degC` | ha-fordpass emits °C on the energytransferlogentry payload |
| `sensor.fordpass_{vin}_energytransferlogentry` | `outsidetemp` | `degC` | `ev_charging_session` | `ambient_temp_end` | `degC` | snapshot mirrored to start/end like battery_temp |
| `sensor.fordpass_{vin}_energytransferlogentry` | `plugDetails.totalDistanceAdded` | `km` | `ev_charging_session` | `distance_added` | `km` | Fixture audit: ha-fordpass emits plugDetails.totalDistanceAdded in km regardless of HA unit system (verified across all 4 29-00 fixtures). Kills 2026-03-21 bug (commit abd736b) that multiplied 103 km by 1.609344 producing 165.8 km. |
| `sensor.fordpass_{vin}_events` | `xev-key-off-trip-segment-data.ambient_temp` | `degC` | `ev_trip_metrics` | `ambient_temp` | `degC` | Canonical metric source; replaces elveh.tripAmbientTemp |
| `sensor.fordpass_{vin}_events` | `xev-key-off-trip-segment-data.cabin_temp` | `degC` | `ev_trip_metrics` | `cabin_temp` | `degC` | Canonical metric source; replaces elveh.tripCabinTemp |
| `sensor.fordpass_{vin}_events` | `xev-key-off-trip-segment-data.distance_traveled` | `km` | `ev_trip_metrics` | `distance` | `km` | Canonical source; replaces elveh.tripDistanceTraveled |
| `sensor.fordpass_{vin}_events` | `xev-key-off-trip-segment-data.energy_consumed` | `Wh` | `ev_trip_metrics` | `energy_consumed` | `kWh` | Canonical source; Wh -> kWh via to_metric |
| `sensor.fordpass_{vin}_events` | `xev-key-off-trip-segment-data.outside_air_temp` | `degC` | `ev_trip_metrics` | `outside_air_temp` | `degC` | Canonical metric source; replaces elveh.tripOutsideAirAmbientTemp |
| `sensor.fordpass_{vin}_metrics` | `xevBatteryMaximumRange` | `km` | `ev_battery_status` | `hv_battery_max_range` | `km` | Canonical metric source; replaces elveh.maximumBatteryRange |
| `sensor.fordpass_{vin}_metrics` | `xevBatteryRange` | `km` | `ev_battery_status` | `hv_battery_range` | `km` | Canonical metric source; replaces elveh state reading |
