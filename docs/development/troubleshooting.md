# Troubleshooting

## Replay and diff

`ha_raw_events` keeps every Home Assistant event with the unit system it
arrived under, so it can be replayed through the current ingestion code and
the result compared with what the live tables hold. `scripts/replay_diff.py`
does that against a scratch database and never writes to the live one.

On a Docker install the script ships in the `web` image, so run it inside the running container and save the two reports next to your `.env`:

```bash
docker compose exec -T web uv run python scripts/replay_diff.py --since 7d > replay.txt
docker compose exec -T web uv run python scripts/replay_diff.py --since 7d --json > replay.json
```

From a development checkout with `DATABASE_URL` pointing at the database to check:

```bash
uv run python scripts/replay_diff.py --since 7d
uv run python scripts/replay_diff.py --since 7d --device <device_id> --json
uv run python scripts/replay_diff.py --since 2026-11-01 --scratch-url postgresql+asyncpg://user:pass@localhost/lr_scratch
```

What it does:

1. Refuses to start if `--scratch-url` names the same database as `DATABASE_URL`. The default is a fresh SQLite file under the system temp directory; its path is printed.
2. Applies the migrations to the scratch database and checks the five derived tables are empty.
3. Reads the archive window from the live database in `recorded_at` order and replays each event through the ingestion entry point under its archived unit system. Events that need a unit system and recorded none are skipped and counted.
4. Compares `ev_battery_status`, `ev_vehicle_status`, `ev_location`, `ev_trip_metrics` and `ev_charging_session`: row counts on each side, rows present on one side only, and column values on matched rows. Status and location rows match on `(device_id, recorded_at)`, trips on `trip_id`, sessions on `(device_id, session_start_utc)`. `--tolerance` (default `0.01`) is the numeric difference treated as equal.

Exit status is `0` when the tables are identical, `1` when anything differs and `2` on a setup error. Device ids are shown by their last four characters, and coordinates and addresses are never printed.

Live rows are limited to `source_system = ha_fordpass` unless `--all-sources` is passed, because the replay cannot reproduce rows from other sources. Columns the scratch database cannot reproduce (surrogate ids, arrival stamps, anything joined from the user's reference tables such as networks, locations and cost) are listed under `ignored columns` for each table rather than compared.

### Expected differences

The archive is the oracle, so a difference the list below does not explain is a regression.

**Baseline (current ingestion).** Status rows are stamped with the wall clock when they are flushed, and the flush is driven by `lastrefresh` events or a 30 second timeout, so `ev_battery_status`, `ev_vehicle_status` and `ev_location` rows never match on `recorded_at`: expect them all to be reported as live-only and scratch-only, with the scratch `recorded_at` span lying at replay time. The report prints that span and flags it. Trips should match live except for rows that were edited by hand or that live ingestion missed; sessions should match on their start time.

**After the shared writers and the shared metrics parser.** Dense rows replace sparse rows in the two status tables: every `metrics` event yields one row with every column it carries, so scratch has more status rows than live, and matched rows gain values in columns that used to be empty.

**After Home Assistant parsing moves to the shared parsers.** `recorded_at` moves from arrival time to reading time, so the drift above disappears and status rows start matching on their key. Trips gain the coaching scores and electrical efficiency from `customMetrics`, so the score columns fill in on matched trips. Both tables should otherwise be identical.

Anything else, in particular a distance, temperature or energy value off by a unit conversion factor, a trip missing on one side, or a session with a different start time, needs to be explained before the change ships.
