# Data Repair

The Data Repair tab on the [settings page](settings.md) fixes historical trip data damaged by ingestion bugs that have since been patched. Each repair card shows a live count of affected rows, a **Preview** button that dry-runs the operation and shows the exact changes together with the evidence behind them, and an **Apply** button that snapshots the rows before touching them.

!!! info "What repairs will never touch"
    Repairs only modify rows ingested from Home Assistant. Trips you entered manually or imported from CSV are never changed, no matter what an operation finds.

## Back Up First

Per-repair snapshots cover only the rows each operation touches. Before your first repair session, take a full database backup:

- **SQLite** — click **Download database backup** at the top of the tab. It streams a consistent copy of the live database (safe while the app is running) named `lightningrod-backup-<date>.db`. To roll back completely, stop the app and put the file back in place of your database file.
- **PostgreSQL** (the default Docker setup) — dump from the host:

    ```bash
    docker compose exec db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' > lightningrod-backup.sql
    ```

    The single quotes matter: `POSTGRES_USER`/`POSTGRES_DB` are set inside the `db` container, not in your host shell.

    Restore with `docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < lightningrod-backup.sql` against a fresh database.

## Running a Repair

1. Open **Settings → Data Repair**. Each card's badge shows how many rows the operation would change right now — a gray "clean" badge means nothing to do.
2. Click **Preview** on a card with a count. The dry run writes nothing. It lists one collapsed row per change, each tagged with the evidence that selected it — a distance ratio, an odometer contradiction, the telemetry reading a value came from. Expand a row to see every field side by side: what the surviving row keeps, what it gains, and the whole contents of any row about to be deleted. Values are shown exactly as the database stores them, and long previews are paged ten at a time.
3. Click **Apply** and confirm. The operation snapshots the affected rows, applies the fix, and reports what changed.
4. Check your data (Trip Sessions, Driving Analytics). If something looks wrong, **Restore** the snapshot from the Snapshots section; if all is well, **Purge** it.

Run the cards top to bottom — duplicate consolidation should run before distance double-conversion, which is the order they appear in.

## Operations

### Trip duplicate consolidation

An earlier unit-conversion bug could record the same trip twice: one row with the correct distance and a twin about a minute later with the distance multiplied by 1.609 (kilometers converted to kilometers). This operation finds those pairs, merges the useful fields (temperatures, driving scores, energy, duration, start time, odometers) onto the correct row, and deletes the corrupted twin. The smaller distance is always the one kept.

### Trip distance double conversion

Some trips were stored with a distance converted to kilometers twice without leaving a duplicate behind. These are detected by contradiction: the stored distance is about 1.609 times the difference between the trip's start and end odometer readings. The fix divides the distance by 1.609344 and recomputes efficiency.

### Derive trip fields from telemetry

Some trips are stored with their headline numbers but no start time, duration, or odometer readings. This operation fills those gaps from vehicle telemetry already in the database — no Home Assistant connection needed. It anchors each trip to the odometer timeline around its end to reconstruct the missing odometer readings, start time, and duration (anything implying an implausible duration or average speed is left alone), recomputes missing efficiency from distance and energy, and averages stored temperature readings over the trip window. It also fills the start and end location from stored GPS history — matching the trip's endpoints to your known locations (an endpoint with no nearby known place is left blank rather than guessed). It only fills empty fields — existing values are never changed. Driving scores and regenerated range cannot be derived from telemetry; those need one of the two replay operations below.

### Event archive replay

Replays trip-related events from LightningROD's own [event archive](settings.md#event-archive) back through the ingestion pipeline, filling the same fields recorder replay fills — duration, start time, odometer readings, regenerated range, driving scores, and temperatures — and recovering trips that were never ingested. Because the events are stored locally as they arrive, this works with Home Assistant offline and reaches back as far as your archive retention, not the recorder's. Each archived event also records the unit system Home Assistant was using when it arrived, so a replay reads distances and temperatures the same way live ingestion did — even if you have changed that unit system since. Events archived before Home Assistant reported its unit system are the exception: the values that depend on it are skipped rather than guessed at, and the card reports how many. It can only replay events archived since you upgraded to a release with the archive; for anything older, use recorder replay while the history is still there.

### Recorder history replay

Replays trip sensor history from Home Assistant's recorder back through the ingestion pipeline. This fills trip fields that were missed the first time — duration, start time, odometer readings, regenerated range, driving scores, and temperatures — and recovers trips that were never ingested at all. Replay needs an active Home Assistant connection *and* recorder history for the trip events sensor; the card stays disabled (with a banner explaining which is missing) until both are available. If Home Assistant is connected but no history is found, check your recorder retention — the window is re-probed every few minutes.

## Snapshots and Restore

Before an operation changes existing rows, it saves them to a snapshot. The Snapshots section at the bottom of the tab lists every snapshot run with its operation, timestamp, and row count:

- **Restore** writes the snapshotted rows back exactly as they were, including rows the repair deleted.
- **Purge** deletes the snapshot once you are happy with the repair.

!!! warning "Restore scope"
    Restore puts the snapshotted rows back; rows created after the snapshot are not removed. In particular, trips recovered by either replay are new rows and stay after a restore — re-running the replay converges to the same result either way.

## The Recorder Window

Recorder replay can only reach as far back as Home Assistant retains history — controlled by the recorder's `purge_keep_days` setting, which defaults to about 10 days. The banner at the top of the tab shows the actual replay window your instance can reach. Trips older than the window cannot be re-enriched; if you want more reach, raise `purge_keep_days` in Home Assistant before the history you need is purged.

Event archive replay is not bound by that window — it reads events LightningROD stored itself, kept for as long as the retention setting on the General tab allows.

## Safe to Re-run

Every operation is idempotent: applying it twice changes nothing the second time. A clean census badge means the operation currently has nothing to do.
