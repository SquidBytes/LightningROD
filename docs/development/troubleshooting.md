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
