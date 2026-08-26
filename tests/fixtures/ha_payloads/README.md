# HA Payload Fixtures

PII-scrubbed Home Assistant state payloads that lock the **unit-ingestion matrix**.
Each fixture mirrors the shape returned by HA's `get_states` websocket API: a JSON object of the form
`{entity_id: state_dict, ...}` where each `state_dict` has `entity_id`, `state`,
`attributes`, `last_changed`, and `last_updated` keys.

## PII Policy

Per `CLAUDE.md`, **agents never commit PII**. These fixtures enforce the policy
with placeholders:

| Field type   | Placeholder                         |
| ------------ | ----------------------------------- |
| VIN          | `YOUR_VIN` (always — never a real 17-char VIN) |
| GPS lat/lon  | `0.0` / `0.0`                       |
| Address      | `"123 Example St, Anytown, XX"`     |
| Network name | `"ExampleNet"`, `"Example Charger"` |

A CI grep verifies no 17-character VIN-shaped strings leak into this directory.
Every fixture contains the literal `YOUR_VIN` in every entity key and friendly
name.

## Matrix Key

Filenames follow `{ha_unit_system}_ha_{vehicle_display}_vehicle.json`:

| Fixture                              | HA `unit_system.length` | Vehicle display / `_elveh` uom | Notes                                |
| ------------------------------------ | ----------------------- | ------------------------------ | ------------------------------------ |
| `metric_ha_metric_vehicle.json`      | `km`                    | `km`                           | Consistent metric (baseline)         |
| `metric_ha_imperial_vehicle.json`    | `km`                    | `mi` (**inconsistent**)        | **Reporter scenario (2026-04-19)** — the 1.6× double-conversion bug lives here |
| `imperial_ha_metric_vehicle.json`    | `mi`                    | `mi` (HA converted)            | HA has converted main states; vehicle display config metric |
| `imperial_ha_imperial_vehicle.json`  | `mi`                    | `mi`                           | Consistent imperial                  |

### Invariants applied across every fixture (D-B1 / D-B4)

- `sensor.fordpass_YOUR_VIN_metrics` — **always metric**, and **every attribute
  is value-wrapped**. ha-fordpass hands Ford's raw metrics dict to HA verbatim,
  so each attribute is `{"updateTime": ..., "value": <scalar>}` (some also carry
  `oemCorrelationId`) and the entity **state is `len(metrics)`**, an integer.
  `xevBatteryRange` and `xevBatteryMaximumRange` are km regardless of HA or
  vehicle config (raw API passthrough — ha-fordpass does not HA-convert
  metrics-entity attributes), so every metrics attribute holds the **same**
  value in all four fixtures; a value that differs between them is a bug. Pack current is `xevBatteryIoCurrent`; there is no
  `xevBatteryAmperage` and no pack-power metric at all — ha-fordpass derives
  `batterykW` itself from voltage x current.
- `sensor.fordpass_YOUR_VIN_events` — the state is `len(events)`, an integer.
- `sensor.fordpass_YOUR_VIN_outsidetemp` — the **state** is the outside
  temperature, in HA's display unit, with that unit stamped on the event. The
  `ambientTemp` attribute mirrors a Ford metric that goes stale (observed frozen
  for weeks on a live vehicle) and is deliberately fixed here at a value the
  state never takes, so anything reading it fails loudly.
- `sensor.fordpass_YOUR_VIN_cabintemperature` — state only. The entity carries
  **no** `cabinTemperature` attribute.
- `sensor.fordpass_YOUR_VIN_events` — **always metric**. The trip segment sits
  under `customEvents["xev-key-off-trip-segment-data"].oemData.trip_data.stringArrayValue`
  as a JSON **string**, and exposes `distance_traveled` (km), `energy_consumed`
  (Wh), `trip_duration` (s), `ambient_temperature` / `cabin_temperature` /
  `outside_air_ambient_temperature` (°C). Raw API passthrough — these are Ford
  API keys, not the `ambient_temp` / `cabin_temp` / `outside_air_temp` DB
  columns they land in.
- `sensor.fordpass_YOUR_VIN_energytransferlogentry.plugDetails.totalDistanceAdded`
  — **HA-system-converted**. ha-fordpass calls `localize_distance` on this
  field inside `get_energy_transfer_log_attrs`, so the fixture value reflects
  HA's configured unit system: **metric HA → km (103), imperial HA → mi (64)**.
  Every other attribute on the energytransferlogentry payload (batteryTemperature,
  outsidetemp, stateOfCharge, power, energyConsumed, chargerType) is raw
  passthrough and stays in its native SI unit across HA unit systems.
- `sensor.fordpass_YOUR_VIN_elveh` **attributes** — **HA-unit-system-converted**.
  ha-fordpass builds `tripDistanceTraveled`, `tripEfficiency`,
  `tripRangeRegenerated`, `maximumBatteryRange` via `localize_distance` and the
  trip temps via `localize_temperature` (per fordpass_handler.py), so fixture
  values follow the HA unit system: metric HA → km/°C, imperial HA → mi/°F.
  `tripDuration` is `str(timedelta)` (e.g. `"0:30:00"`); `tripEnergyConsumed`
  is always kWh. NOTE: the elveh **state** `unit_of_measurement` tracks the
  vehicle display system, NOT the HA system — `metric_ha_imperial` carries
  km-valued trip attrs under a `mi` state uom on purpose; resolving trip attrs
  from the state uom is the historical double-conversion bug.
- `sensor.fordpass_YOUR_VIN_elveh.state` — main state; HA has already performed
  unit conversion per `unit_of_measurement`. 260 km ↔ 162 mi is the 2026 F-150
  Lightning reporter scenario anchor.

## Reporter Oracle Values

The 2026-04-19 reporter scenario is encoded in
`metric_ha_imperial_vehicle.json`:

- 19 km trip — `events.xev-key-off-trip-segment-data.distance_traveled = 19`
- 64 mi / 103 km charge added —
  `energytransferlogentry.plugDetails.totalDistanceAdded`:
  - metric-HA fixtures → `103` (km, ha-fordpass left it as km)
  - imperial-HA fixtures → `64` (mi, ha-fordpass converted via `localize_distance`)
- 260 mi / ~418 km max range — `metrics.xevBatteryMaximumRange = 418`

Failure to store each of these as its documented metric value is what the
reporter regression tests lock against.

## How to Add a New Fixture

1. Capture a real HA `get_states` payload from your install. The `metrics`,
   `events`, `states` and `vehicles` entities ship disabled
   (`entity_registry_enabled_default=False`); enable the ones you need in the
   HA entity registry before capturing.
2. **Scrub PII**: replace the 17-char VIN with `YOUR_VIN` in every entity id
   and friendly name. Zero GPS (`0.0`/`0.0`). Generic address.
3. Place the new file under `tests/fixtures/ha_payloads/`.
4. Add a row to the matrix table above with unit-system + vehicle-display config.
5. Run `grep -rE '[0-9][A-HJ-NPR-Z0-9]{16}' tests/fixtures/` — must
   return zero matches before committing.
6. Update any matrix-tests that enumerate fixtures (`test_unit_ingestion_matrix.py`).
