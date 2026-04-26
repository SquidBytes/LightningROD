# CSV Import

Import charging sessions in bulk from CSV files. You can open the import flow from the **CSV Import** tab on the Settings page.

## Import Flow

The import uses three steps:

1. **Upload** -- Select a CSV file, choose a timezone, and upload it
2. **Preview** -- Review parsed rows, fix errors inline, and handle duplicates
3. **Summary** -- See counts of added, updated, skipped, and failed rows

## CSV Template

Download the template CSV from the upload screen. It includes headers for all mappable fields, so you can fill in what you have and leave the rest blank.

The template includes columns for:

| Field | Example | Required |
|-------|---------|----------|
| session_start_utc | `2025-06-15 14:30:00` | Soft (flagged if missing) |
| energy_kwh | `32.5` | Soft (flagged if missing) |
| cost | `8.12` | No |
| location_name | `Home` | No |
| charge_type | `AC` or `DC` | No |
| duration_minutes | `45` | No |
| soc_start / soc_end | `20` / `80` | No |
| miles_added | `95.2` | No |
| network_name | `Electrify America` | No |
| evse_voltage | `480` | No |
| evse_kw | `150` | No |

The full template includes 30+ columns for session details, location data, EVSE fields, and stall links.

## Auto-Detection

You do not have to use the template. The importer can match common column names automatically:

- Exact matches against known column names
- Normalized matching (lowercase, stripped punctuation)
- Keyword hints (e.g., "kwh" maps to energy, "soc" maps to state of charge)

An info banner shows which columns were matched and which were skipped.

## Timezone Handling

The upload form includes a timezone selector, which defaults to your app timezone setting.

- **Naive timestamps** (no timezone info in the CSV) are interpreted as the selected timezone and converted to UTC for storage
- **Timezone-aware timestamps** (with explicit offset or zone) are respected as-is
- All stored timestamps are UTC; display uses your configured timezone

## Preview Table

The preview shows every parsed row in a scrollable table with columns for date, location, energy, cost, type, network, duration, and status.

### Row Status

| Status | Meaning |
|--------|---------|
| New | No matching session found and it will be imported |
| Duplicate | Matches an existing session |
| Fuzzy Match | Similar existing session found within the matching tolerance |
| Error | Row has a parsing issue |

### Inline Editing

Click an error or duplicate row to expand an inline editor below it. Edit the fields directly and the row re-checks automatically when you move focus away.

### Duplicate Handling

Duplicate rows offer three actions:

- **Skip** -- Don't import this row
- **Insert anyway** -- Import as a new session regardless
- **Update existing** -- Overwrite the existing session with CSV values

Duplicate detection uses an exact match on `session_id` and a fuzzy match on start time, location, and energy.

## Import Results

After confirming, the summary shows:

- **Added** -- New sessions inserted
- **Updated** -- Existing sessions updated
- **Skipped** -- Rows you deselected or marked as skip
- **Failed** -- Rows that couldn't be inserted (shown with error details)

Each row is imported on its own, so one failed row does not affect the others.

!!! tip "Large Imports"
    For very large imports, consider using the [seed script](../getting-started/data-import.md) instead. It runs server-side with direct database access.
