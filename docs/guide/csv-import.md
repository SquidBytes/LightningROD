# CSV Import

Import charging sessions in bulk from CSV files. The import flow is available from the **CSV Import** tab on the Settings page.

## Import Flow

The import uses a three-step flow:

1. **Upload** -- Select a CSV file, choose a timezone, and upload
2. **Preview** -- Review parsed rows, fix errors inline, handle duplicates
3. **Summary** -- See counts of added, updated, skipped, and failed rows

## CSV Template

Download the template CSV from the upload screen. It contains headers for all mappable fields -- fill in what you have and leave the rest blank.

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

The full template has ~24 columns covering all session and EVSE fields.

## Auto-Detection

You don't have to use the template. The importer auto-detects common column name patterns:

- Exact matches against known column names
- Normalized matching (lowercase, stripped punctuation)
- Keyword hints (e.g., "kwh" maps to energy, "soc" maps to state of charge)

An info banner shows which columns were matched and which were skipped.

## Timezone Handling

The upload form includes a timezone selector, defaulting to your app timezone setting.

- **Naive timestamps** (no timezone info in the CSV) are interpreted as the selected timezone and converted to UTC for storage
- **Timezone-aware timestamps** (with explicit offset or zone) are respected as-is
- All stored timestamps are UTC; display uses your configured timezone

## Preview Table

The preview shows all parsed rows (no row cap) in a scrollable table with columns for date, location, energy, cost, type, network, duration, and status.

### Row Status

| Status | Meaning |
|--------|---------|
| New | No matching session found -- will be imported |
| Duplicate | Matches an existing session |
| Error | Row has a parsing issue |

### Inline Editing

Click an error or duplicate row to expand an inline editor below it. Edit problematic fields directly -- the row re-verifies automatically when you move focus away from a field (blur triggers server-side validation via HTMX).

### Duplicate Handling

Duplicate rows offer three actions:

- **Skip** -- Don't import this row
- **Insert anyway** -- Import as a new session regardless
- **Update existing** -- Overwrite the existing session with CSV values

Duplicate detection uses exact match on `session_id` and fuzzy match on start time (within 1 hour), location, and energy (within 10%).

## Import Results

After confirming, the summary shows:

- **Added** -- New sessions inserted
- **Updated** -- Existing sessions updated
- **Skipped** -- Rows you deselected or marked as skip
- **Failed** -- Rows that couldn't be inserted (shown with error details)

Each row is imported independently -- a failed row doesn't affect others.

!!! tip "Large Imports"
    For initial bulk imports (hundreds of sessions), consider using the [seed script](../getting-started/data-import.md) instead. It runs server-side with direct database access.
