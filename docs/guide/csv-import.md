# :lucide-file-up: CSV Import

Import charging sessions in bulk from CSV files. The import flow walks you through upload, column mapping, preview, and confirmation — all from the Settings page.

## Getting Started

Navigate to `/settings` and click the **CSV Import** tab.

1. **Upload** — Select a CSV file and click Upload & Process
2. **Map Columns** — Match CSV headers to database fields (auto-detected where possible)
3. **Preview** — Review parsed rows, check for duplicates, select which rows to import
4. **Import** — Confirm and import selected rows
5. **Summary** — See counts of added, skipped, and failed rows

## CSV Format

Your CSV should have a header row. Column names are flexible — the mapper auto-detects common patterns. At minimum you need a date and energy value.

| Field | Example | Notes |
|-------|---------|-------|
| Start time | `2025-06-15 14:30:00` | ISO datetime or similar |
| Energy (kWh) | `32.5` | Required |
| Cost | `8.12` | Dollars |
| Location | `Home` | Free text |
| Charge type | `AC` or `DC` | |
| Duration | `45` | Minutes |
| SOC start/end | `20` / `80` | Percent, 0-100 |
| Miles added | `95.2` | |

Additional columns are accepted — the mapper shows all available database fields.

## Column Mapping

After upload, the mapper presents each CSV column with a dropdown of database fields. Auto-detection uses several strategies:

- Exact header match against known column names
- Normalized matching (lowercase, stripped punctuation)
- Keyword hints (e.g., "kwh" maps to energy, "soc" maps to state of charge)

Set any column to **Skip** to exclude it from the import. Adjust mappings as needed before proceeding.

## Preview and Duplicates

The preview table shows the first 25 rows with status badges:

| Badge | Meaning |
|-------|---------|
| New | No matching session found — will be imported |
| Duplicate | Matches an existing session by ID or fuzzy criteria |
| Error | Row has a parsing issue |

Duplicate detection uses two layers:

- **Exact match** — same `session_id` already in the database
- **Fuzzy match** — same start time (within 1 hour), location, and energy (within 10%)

Use the bulk selection buttons to quickly select all new rows, all rows, or clear the selection. Individual row checkboxes are also available.

## Import Results

After confirming, the summary shows:

- **Added** — New sessions inserted
- **Updated** — Existing sessions updated (if you selected duplicates with "update" action)
- **Skipped** — Rows you deselected
- **Failed** — Rows that couldn't be inserted (shown with details)

Each row is imported independently — a failed row doesn't affect others.

Click **Import Another File** to start a new import without leaving the page.

!!! tip "Large Imports"
    For initial bulk imports (hundreds of sessions), consider using the [seed script](../getting-started/data-import.md) instead. It runs server-side with direct database access and handles large files more efficiently.
