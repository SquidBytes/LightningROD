# Troubleshooting

How to collect what an agent (or a human) needs to work out why a reading is wrong, a trip is missing, or an event never arrived, without access to your instance.

## Turn up logging

Every log line carries a timestamp, level and logger name:

```
2026-11-07 10:00:01,234 INFO lightningrod.hass: event slug=soc device=5841 last_updated=2026-11-07T10:00:00+00:00 branch=fordpass raised=False
```

`LR_LOG_LEVEL` sets the level for the app's own loggers. The default, `INFO`, is what to send when something is wrong; you should not need to change it before collecting a log. Set it in `.env` and restart:

```sh
LR_LOG_LEVEL=DEBUG docker compose up -d
```

| Level | What you get |
|---|---|
| `INFO` (default) | One line per Home Assistant vehicle event: entity slug, last 4 characters of the device id, the event's `last_updated`, which branch handled it (`gas`, `fordpass`, `none`) and whether a handler raised. Errors with their traceback. Connection, reconnect and startup messages. Enough to follow one event from arrival to the table it should have landed in |
| `DEBUG` | Everything above plus every other Home Assistant entity, payloads and unit-conversion traces (`lightningrod.units.trace`). **DEBUG lines can contain raw sensor payloads and the full VIN.** Scrub before sharing. Only when asked for it |
| `WARNING` | Quiet: problems only, no per-event lines. Use it on a stable instance you never need to troubleshoot |

`APP_DEBUG=true` is separate: it echoes every SQL statement and is rarely what you want.

## Collect the last N hours

Capture the web container's log and replace your VIN before pasting it anywhere:

```sh
VIN=YOUR_VIN
docker compose logs web --since 2h --no-log-prefix 2>&1 \
  | sed "s/${VIN}/YOUR_VIN/gI" > lr-checkpoint.log
```

Adjust `--since` (`30m`, `6h`, `2026-11-07T08:00:00`) to cover the time the problem happened. Check the file for anything else you would not share, such as the Home Assistant URL.

## Database snapshots

Run these against the `db` container. `psql` prompts for the password from `.env`.

```sh
docker compose exec db psql -U lightningrod -d lightningrod
```

**Raw events by slug and day** -- shows which entities Home Assistant delivered and when. A slug with no rows for a day it should have fired is the first thing to check:

```sql
SELECT slug, date_trunc('day', recorded_at) AS day, count(*)
FROM ha_raw_events
WHERE recorded_at > now() - interval '7 days'
GROUP BY slug, day
ORDER BY day DESC, slug;
```

**Newest row per table** -- a table that stopped updating while raw events kept arriving points at the parser or writer, not the source:

```sql
SELECT 'ev_battery_status' AS t, max(recorded_at) AS newest, count(*) FROM ev_battery_status
UNION ALL SELECT 'ev_vehicle_status', max(recorded_at), count(*) FROM ev_vehicle_status
UNION ALL SELECT 'ev_location', max(recorded_at), count(*) FROM ev_location
UNION ALL SELECT 'ev_trip_metrics', max(end_time), count(*) FROM ev_trip_metrics
UNION ALL SELECT 'ev_charging_session', max(recorded_at), count(*) FROM ev_charging_session;
```

**Latest raw events** -- the last few payloads, for comparing what arrived with what was stored. Payloads contain the VIN; scrub before sharing:

```sql
SELECT recorded_at, slug, state
FROM ha_raw_events
ORDER BY recorded_at DESC
LIMIT 20;
```

## Connection health

The Home Assistant status panel on the Settings page polls `/settings/hass/status`. To capture it as text:

```sh
curl -s http://localhost:8000/settings/hass/status | sed 's/<[^>]*>//g' | grep -v '^\s*$'
```

It reports connection state, events processed, errors, the last event and last write times, and the archive health. `curl -s http://localhost:8000/healthz` answers whether the app and database are up at all.

## What to paste into an agent session

1. What you expected and what you saw, with the time it happened (and your timezone).
2. `lr-checkpoint.log` from the section above, VIN scrubbed.
3. The output of the three SQL snippets.
4. The connection health text.
5. The version from `/version`.

With these, an agent can follow one event from arrival (`ha_raw_events`) through dispatch (the per-event log line) to the table it should have landed in.

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
