# Data Capture Reference

LightningROD does not store everything Home Assistant exposes. It captures a
**curated, high-value subset** of the FordPass telemetry surfaced by the
[ha-fordpass](https://github.com/marq24/ha-fordpass) integration, converts each
value into canonical metric units, and lands it in a typed column used for
display and calculations.

This page is the map: for the data LightningROD cares about — high-voltage
battery, charging, trips, speed, temperatures, SOC, range, odometer — it shows
**which HA source feeds which database column**, whether that column is actually
being filled, and — at the end — the high-value FordPass data that is **not**
captured today.

!!! note "Related pages"
    - [Data Sources](../data-sources.md) is the auto-generated unit contract —
      the exact `entity.attribute → column` mappings with source and target
      units. This page complements it with the handler-written columns (GPS,
      tire pressure, charging session), plain-English meaning, and honest
      capture status.
    - [Home Assistant Integration](../guide/home-assistant.md) covers connecting
      LightningROD to HA in the first place.

## How to read the tables

**Captured** reflects what the ingestion code actually writes *and* what a live
database confirms is populated:

| Value | Meaning |
|-------|---------|
| **Yes** | A handler writes this column and real values are present. |
| **Intermittent** | Only populated in specific states (e.g. while driving or charging), or only when a fallback entity emits the attribute. |
| **No** | The column exists in the schema but no live ingestion path fills it (it may be written only by demo/seed data, or populated from a non-HA source such as the cost engine). |

All stored values are metric/SI. ha-fordpass localizes many fields to your HA
unit system before LightningROD sees them; the adapter converts them back to
canonical units on the way in (see [Data Sources](../data-sources.md)).

## Battery — `ev_battery_status`

High-voltage pack, 12V system, and motor telemetry. Rows come from the
`metrics`, `soc`, `elveh`, `battery`, and `elvehcharging` FordPass entities.

| Column | Source (entity.attribute) | Unit | Captured | Notes |
|--------|---------------------------|------|----------|-------|
| `hv_battery_soc` | `soc` state / `metrics.xevBatteryStateOfCharge` | % | Yes | Displayed SOC. |
| `hv_battery_actual_soc` | `metrics.xevBatteryActualStateOfCharge` / `elveh.batteryActualCharge` | % | Yes | True (unbuffered) SOC. |
| `hv_battery_range` | `metrics.xevBatteryRange` (canonical) / `soc.batteryRange` / `elveh` state | km | Yes | Estimated remaining range. |
| `hv_battery_max_range` | `metrics.xevBatteryMaximumRange` / `elveh.maximumBatteryRange` | km | Yes | Full-charge range estimate. |
| `hv_battery_capacity` | `metrics.xevBatteryCapacity` / `elveh.maximumBatteryCapacity` | kWh | Yes | Mixed Wh/kWh across installs; magnitude-autoscaled. Drives degradation. |
| `hv_battery_voltage` | `metrics.xevBatteryVoltage` / `elveh.batteryVoltage` | V | Yes | SI passthrough. |
| `hv_battery_amperage` | `metrics.xevBatteryAmperage` / `elveh.batteryAmperage` | A | Yes | SI passthrough. |
| `hv_battery_kw` | `metrics.xevBatteryPower` (W→kW) / `elveh.batterykW` | kW | Yes | Instantaneous pack power. |
| `hv_battery_temperature` | `elvehcharging.batteryTemperature` | °C | Intermittent | Live only while charging. |
| `lv_battery_level` | `battery` state | % | Yes | 12V state of charge. |
| `lv_battery_voltage` | `battery.batteryVoltage` | V | Yes | 12V voltage. |
| `motor_voltage` | `elveh.motorVoltage` | V | Yes | SI passthrough. |
| `motor_amperage` | `elveh.motorAmperage` | A | Yes | SI passthrough. |
| `motor_kw` | `elveh.motorkW` | kW | Yes | SI passthrough. |
| `performance_status` | — | — | No | Column exists; no ingestion path. |

## Vehicle status — `ev_vehicle_status`

Drivetrain, controls, dynamics, and cabin/outside environment. Fields are
batched per refresh cycle and flushed as one row on the `lastrefresh` signal.

| Column | Source (slug/attribute) | Unit | Captured | Notes |
|--------|-------------------------|------|----------|-------|
| `odometer` | `odometer` | km | Yes | Cumulative; also used to bound trips. |
| `speed` | `speed` | km/h | Intermittent | Non-zero only while driving. |
| `accelerator_position` | `acceleratorpedalposition` | % | Yes | |
| `brake_status` | `brakepedalstatus` | text | Yes | |
| `gear_position` | `gearleverposition` | text | Yes | |
| `parking_brake` | `parkingbrakestatus` | text | Yes | |
| `ignition_status` | `ignitionstatus` | text | Yes | |
| `torque_at_transmission` | `torqueattransmission` | Nm | Yes | |
| `brake_torque` | `braketorque` / `metrics.brakeTorque` | Nm | Yes | |
| `wheel_torque_status` | `wheeltorquestatus` | text | Yes | |
| `yaw_rate` | `yawrate` / `metrics.yawRate` | deg/s | Yes | |
| `acceleration` | `acceleration` / `metrics.acceleration` | m/s² | Yes | Longitudinal. |
| `deep_sleep_status` | `deepsleep` | text | Yes | |
| `device_connectivity` | `deviceconnectivity` | text | Yes | |
| `evcc_status` | `evccstatus` | text | Yes | |
| `outside_temperature` | `outsidetemp.ambientTemp` | °C | Yes | Time-series ambient. |
| `cabin_temperature` | `cabintemperature.cabinTemperature` | °C | Yes | |
| `tire_pressure` | `tirepressure` (JSON) | bar / text | Yes | Per-wheel pressures + system state. |
| `door_lock_status` | — | JSON | No | Column exists; **no live handler** — see caveats. |
| `indicators` | — | JSON | No | Column exists; **no live handler** — see caveats. |
| `remote_start_status` | — | text | No | Column exists; no handler slug. |
| `seatbelt_status` | — | text | No | Low value; column exists, no handler slug. |

!!! warning "`enginespeed` and `coolanttemp` are dropped"
    Both slugs are accepted by the vehicle-status handler but have **no
    destination column**, so their values are silently discarded. They are the
    only handled-but-unmapped FordPass signals.

## Trips — `ev_trip_metrics`

One row per completed drive. The canonical source is the `events` entity
(`xev-key-off-trip-segment-data`, raw metric); the `elveh` entity is a fallback
that also supplies driving scores. Odometer bounds are backfilled from the
nearest vehicle-status readings.

| Column | Source (entity.attribute) | Unit | Captured | Notes |
|--------|---------------------------|------|----------|-------|
| `start_time` / `end_time` | derived from event time + `trip_duration` | — | Yes | |
| `distance` | `events…distance_traveled` / `elveh.tripDistanceTraveled` | km | Yes | |
| `duration` | `events…trip_duration` / `elveh.tripDuration` | s | Yes | |
| `energy_consumed` | `events…energy_consumed` / `elveh.tripEnergyConsumed` | kWh | Yes | |
| `efficiency` | `elveh.tripEfficiency` | km/kWh | Yes | |
| `range_regenerated` | `metrics.tripXevBatteryRangeRegenerated` / `elveh.tripRangeRegenerated` | km | Yes | |
| `ambient_temp` | `events…ambient_temperature` / `elveh.tripAmbientTemp` | °C | Yes | |
| `cabin_temp` | `events…cabin_temperature` / `elveh.tripCabinTemp` | °C | Yes | |
| `outside_air_temp` | `events…outside_air_ambient_temperature` / `elveh.tripOutsideAirAmbientTemp` | °C | Yes | |
| `odometer_start` / `odometer_end` | nearest `ev_vehicle_status.odometer` | km | Yes | Derived, not a direct attribute. |
| `driving_score` | `metrics.tripXevBatteryChargeRegenerated` / `elveh.tripDrivingScore` | 0–100 | Intermittent | Absent when telemetry reports 0. |
| `electrical_efficiency` | `elveh.tripElectricalEfficiency` | 0–100 | Yes | |
| `speed_score` | `elveh.tripSpeed` | 0–100 | Intermittent | elveh-only; empty when the fallback entity is absent. |
| `acceleration_score` | `elveh.tripAcceleration` | 0–100 | Intermittent | elveh-only. |
| `deceleration_score` | `elveh.tripDeceleration` | 0–100 | Intermittent | elveh-only. |
| `brake_torque` | — | Nm | No | Column exists; not written on the trip path. |
| `start_location_id` / `end_location_id` | — | — | No | Not populated from the HA path. |

## Location — `ev_location`

GPS snapshots from the `gps` entity, deduplicated by time (60s) and distance
(50m).

| Column | Source | Unit | Captured | Notes |
|--------|--------|------|----------|-------|
| `latitude` / `longitude` | `gps.value.location.lat/lon` | ° | Yes | |
| `gps_accuracy` | `gps…accuracy` | m | Intermittent | Written when present; often absent. |
| `altitude` | — | m | No | Column exists; not read from GPS payload. |
| `compass_direction` | — | text | No | Column exists; not populated. |
| `address` / `location_type` | — | text | No | Populated for charging locations, not GPS snapshots. |

## Charging — `ev_charging_session`

One row per completed charge, built from the rich `energytransferlogentry`
payload. Thermal context is pulled from per-device caches populated by the
`elvehcharging` and `outsidetemp` entities just before the session is written.

| Column | Source (entity.attribute) | Unit | Captured | Notes |
|--------|---------------------------|------|----------|-------|
| `charge_type` | `energytransferlogentry.chargerType` | AC/DC | Yes | Normalized to AC/DC. |
| `energy_kwh` | `…energyConsumed` | kWh | Yes | |
| `start_soc` / `end_soc` | `…stateOfCharge.firstSOC` / `.lastSOC` | % | Yes | |
| `session_start_utc` / `session_end_utc` | `…energyTransferDuration.begin` / `.end` | — | Yes | |
| `charge_duration_seconds` | `…energyTransferDuration.totalTime` | s | Yes | |
| `plugged_in_duration_seconds` | `…plugDetails.totalPluggedInTime` | s | Yes | |
| `charging_kw` | `…power.weightedAverage` (W→kW) | kW | Yes | Session-average power. |
| `max_power` / `min_power` | `…power.max` / `.min` (W→kW) | kW | Yes | |
| `distance_added` | `…plugDetails.totalDistanceAdded` | km | Yes | HA-unit-localized; converted back. |
| `address` / `latitude` / `longitude` | `…location.*` | ° / text | Yes | |
| `location_name` | `…location.name` / address city | text | Yes | |
| `network_id` | resolved from `…location.network` | — | Yes | Via network resolver. |
| `battery_temp_start` / `battery_temp_end` | cached `elvehcharging.batteryTemperature` | °C | Intermittent | Single snapshot mirrored to start/end. |
| `ambient_temp_start` / `ambient_temp_end` | cached `outsidetemp.ambientTemp` | °C | Yes | Single snapshot mirrored to start/end. |
| `plug_status` / `charging_status` / `station_status` | (logged only) | text | No | Live plug/charge state is logged, not stored. |
| `charging_voltage` / `charging_amperage` | — | V / A | No | Only session-average kW is captured. |
| `cost`, `estimated_cost` | cost engine | currency | No (derived) | Calculated, not captured from HA. |
| `evse_*`, `charger_rated_kw`, `stall_id` | EVSE / stall association | — | No (derived) | Filled by charger association, not HA. |

## Captured but not surfaced

A few captured columns are stored but not shown directly in the UI or used in
calculations today:

- **Vehicle dynamics** — `yaw_rate`, `acceleration`, `brake_torque`,
  `wheel_torque_status`, `torque_at_transmission` are logged to
  `ev_vehicle_status` but not visualized.
- **Motor telemetry** — `motor_voltage`, `motor_amperage`, `motor_kw` are
  stored but not currently plotted.
- **`tire_pressure`** — captured as structured JSON but not shown on a
  dedicated page.

## Available from ha-fordpass but NOT captured

FordPass exposes considerably more than LightningROD ingests. Any entity slug
without a registered handler is **silently dropped** by the dispatcher. The
table below lists notable uncaptured data, flagged by rough value. Attributes
that cannot be confirmed without a live HA instance are marked *unverified*.

| Data | Rough value | Status | Note |
|------|-------------|--------|------|
| Door open/ajar + lock state (per door, hood, tailgate) | High | Not captured (*unverified* attribute names) | Would fill `door_lock_status`. |
| Remote start state | Medium | Not captured (*unverified*) | Column `remote_start_status` exists, unused. |
| Vehicle warning indicators (low fuel/washer, service due, check-engine) | Medium | Not captured (*unverified*) | Would fill `indicators`. |
| Window positions | Low–Med | Not captured (*unverified*) | |
| Alarm / guard-mode status | Medium | Not captured (*unverified*) | Security state. |
| Oil life / engine oil temp | Low (EV) | Not captured (*unverified*) | Mostly relevant to ICE/hybrid. |
| GPS altitude & heading | Low–Med | Not captured | Payload may carry them; columns exist, unread. |
| Vehicle messages / notifications | Low | Not captured (*unverified*) | |
| Zone lighting | Low | Not captured (*unverified*) | |
| Seatbelt status | Low | Not captured (*unverified*) | Column `seatbelt_status` exists, unused. |
| `enginespeed`, `coolanttemp` | Low (EV) | Handled but **discarded** | Accepted by a handler with no destination column. |

!!! warning "`door_lock_status` and `indicators`: schema-only"
    These JSON columns exist and appear non-empty in naive `COUNT(*)` checks,
    but every value is an empty JSON `null`: no ingestion handler writes them,
    and repository history shows no path that ever has. They are populated only
    by the development seed generator. Treat them as **not captured** until a
    door/lock or indicator handler is added. (This is the corrected finding —
    only `tire_pressure` among the JSON status columns is genuinely captured.)
