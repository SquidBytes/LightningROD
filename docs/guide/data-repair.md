# Data Repair

The Data Repair tab on the [settings page](settings.md) fixes historical trip data damaged by ingestion bugs that have since been patched. Each repair card shows a live count of affected rows, a **Preview** button that dry-runs the operation and lists the exact changes, and an **Apply** button that snapshots the rows before touching them.

!!! info "What repairs will never touch"
    Repairs only modify rows ingested from Home Assistant. Trips you entered manually or imported from CSV are never changed, no matter what an operation finds.

## Operations

### Trip duplicate consolidation

An earlier unit-conversion bug could record the same trip twice: one row with the correct distance and a twin about a minute later with the distance multiplied by 1.609 (kilometers converted to kilometers). This operation finds those pairs, merges the useful fields (temperatures, driving scores, energy, duration, start time, odometers) onto the correct row, and deletes the corrupted twin. The smaller distance is always the one kept.

### Trip distance double conversion

Some trips were stored with a distance converted to kilometers twice without leaving a duplicate behind. These are detected by contradiction: the stored distance is about 1.609 times the difference between the trip's start and end odometer readings. The fix divides the distance by 1.609344 and recomputes efficiency.

### Recorder history replay

Replays trip sensor history from Home Assistant's recorder back through the ingestion pipeline. This fills trip fields that were missed the first time — duration, start time, odometer readings, regenerated range, driving scores, and temperatures — and recovers trips that were never ingested at all. Replay requires an active Home Assistant connection; the card is disabled until one is up.

## Snapshots and Restore

Before an operation changes existing rows, it saves them to a snapshot. The Snapshots section at the bottom of the tab lists every snapshot run with its operation, timestamp, and row count:

- **Restore** writes the snapshotted rows back exactly as they were, including rows the repair deleted.
- **Purge** deletes the snapshot once you are happy with the repair.

!!! warning "Restore scope"
    Restore puts the snapshotted rows back; rows created after the snapshot are not removed. In particular, trips recovered by recorder replay are new rows and stay after a restore — re-running the replay converges to the same result either way.

## The Recorder Window

Recorder replay can only reach as far back as Home Assistant retains history — controlled by the recorder's `purge_keep_days` setting, which defaults to about 10 days. The banner at the top of the tab shows the actual replay window your instance can reach. Trips older than the window cannot be re-enriched; if you want more reach, raise `purge_keep_days` in Home Assistant before the history you need is purged.

## Safe to Re-run

Every operation is idempotent: applying it twice changes nothing the second time. A clean census badge means the operation currently has nothing to do.
