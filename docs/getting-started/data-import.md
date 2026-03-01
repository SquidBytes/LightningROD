# :lucide-file-input: Data Import (Seed Script)

!!! note "Importing via the web UI?"
    To import CSV files through the browser with column mapping and duplicate detection, see the [CSV Import guide](../guide/csv-import.md).

LightningROD starts with an empty database. The seed script imports charging session history from CSV files directly into PostgreSQL. This is best for initial setup and large bulk imports.

## Running the Seed Script

=== "Docker"

    ```bash
    # Place your CSV in the data/ directory, then:
    docker compose exec web uv run python scripts/seed.py --vin YOUR_VIN_HERE
    ```

=== "Local Development"

    ```bash
    uv run python scripts/seed.py --vin YOUR_VIN_HERE
    ```

### Options

| Flag | Description |
|------|-------------|
| `--vin` | Vehicle identification number (required) |
| `--csv-path` | Path to CSV file (default: `data/fake_charging_sessions_sample.csv` for demo) |
| `--dry-run` | Preview what would be imported without writing to the database |

### Dry Run

Preview the import before committing:

```bash
uv run python scripts/seed.py --vin YOUR_VIN_HERE --dry-run
```

## CSV Format

The seed script expects a CSV with the following columns. Not all columns are required -- the script handles missing values.

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| `session_id` | UUID | No | Unique identifier. If missing, a deterministic UUID is generated from row content. |
| `session_start_utc` | ISO datetime | Yes | When the session started |
| `session_end_utc` | ISO datetime | No | When the session ended |
| `energy_kwh` | float | Yes | Energy delivered |
| `charge_type` | string | No | AC, DC, Level1, Level2, etc. |
| `location_name` | string | No | Human-readable location name |
| `cost` | float | No | Session cost in dollars |
| `soc_start` | float | No | State of charge at start (0-100) |
| `soc_end` | float | No | State of charge at end (0-100) |
| `miles_added` | float | No | Estimated range added |

Additional columns from the ha-fordpass data model are accepted and stored if present. See the database schema in `db/models/charging_session.py` for the full field list.

## Classification Rules

The seed script automatically classifies each session based on its location.

### Location Type

Sessions are assigned a `location_type`:

| Type | Description |
|------|-------------|
| `home` | Matches your configured home location |
| `work` | Matches your configured work location |
| `public` | Everything else |

### Free Charging

Sessions are marked `is_free = true` when the location matches a known free charging location (workplace chargers, promotional locations, etc.).

!!! tip "Customizing Classifications"
    Edit the `FREE_LOCATIONS` set and location mapping constants at the top of `scripts/seed.py` to match your own charging locations.

## Idempotency

The seed script uses PostgreSQL's `ON CONFLICT DO UPDATE` on the `session_id` column. Running the script multiple times with the same CSV produces the same result -- no duplicates.

For rows missing a `session_id`, the script generates a deterministic UUID by hashing the row contents. Re-importing the same CSV always produces the same IDs.

## Data Directory

CSV files go in the `data/` directory at the project root. This directory is gitignored (except for `.gitkeep`) to keep personal data out of version control.

```
data/
├── .gitkeep
└── fake_charging_sessions_sample.csv   # Included demo data (40 sessions)
```

Place your own CSV files here for seeding. The `data/` directory is gitignored (except `.gitkeep` and the sample file) to keep personal data out of version control.
