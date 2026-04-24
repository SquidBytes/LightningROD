# HA Payload Fixtures

PII-scrubbed Home Assistant state payloads that lock the **unit-ingestion matrix**
for Phase 29 (`.planning/phases/29-unit-ingestion-overhaul/`). Each fixture mirrors
the shape returned by HA's `get_states` websocket API: a JSON object of the form
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

- `sensor.fordpass_YOUR_VIN_metrics` — **always metric**. `xevBatteryRange` and
  `xevBatteryMaximumRange` are km regardless of HA or vehicle config (raw API
  passthrough — ha-fordpass does not HA-convert metrics-entity attributes).
- `sensor.fordpass_YOUR_VIN_events` — **always metric**. `xev-key-off-trip-segment-data`
  exposes `distance_traveled` (km), `energy_consumed` (Wh), `trip_duration` (s),
  `ambient_temp` / `cabin_temp` / `outside_air_temp` (°C). Raw API passthrough.
- `sensor.fordpass_YOUR_VIN_energytransferlogentry.plugDetails.totalDistanceAdded`
  — **HA-system-converted**. ha-fordpass calls `localize_distance` on this
  field inside `get_energy_transfer_log_attrs`, so the fixture value reflects
  HA's configured unit system: **metric HA → km (103), imperial HA → mi (64)**.
  Every other attribute on the energytransferlogentry payload (batteryTemperature,
  outsidetemp, stateOfCharge, power, energyConsumed, chargerType) is raw
  passthrough and stays in its native SI unit across HA unit systems.
- `sensor.fordpass_YOUR_VIN_elveh` **attributes** — per D-B4 (see 29-CONTEXT.md)
  these are **not read** by the new adapter. Attribute values (e.g.
  `tripDistanceTraveled=19`) stay labeled in source units (km) across fixtures
  specifically so a faulty adapter that reads them is caught by the matrix tests.
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
Phase 29 regression tests lock against (commit `abd736b`).

## How to Add a New Fixture

1. Capture a real HA `get_states` payload from your install.
2. **Scrub PII**: replace the 17-char VIN with `YOUR_VIN` in every entity id
   and friendly name. Zero GPS (`0.0`/`0.0`). Generic address.
3. Place the new file under `app-public/tests/fixtures/ha_payloads/`.
4. Add a row to the matrix table above with unit-system + vehicle-display config.
5. Run `grep -rE '[0-9][A-HJ-NPR-Z0-9]{16}' app-public/tests/fixtures/` — must
   return zero matches before committing.
6. Update any matrix-tests that enumerate fixtures (`test_unit_ingestion_matrix.py`).

## Phase 29 Plan References

- Plan 29-00 — this scaffolding (locks API + oracles, all tests fail by design)
- Plan 29-01 — `web.services.units.to_metric` + property tests turn green
- Plan 29-02 — `web.services.sources.ha_fordpass.adapter` + matrix & reporter
  regression tests turn green
- Plan 29-03 — auto-generated docs + `/admin/data-sources` endpoint
- Plan 29-04 — contract-coverage invariant + full integration verification
