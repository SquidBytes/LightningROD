"""Main entry point for seed/demo data generation.

Usage:
    python -m scripts.seed.main --help
    python -m scripts.seed.main --all
    python -m scripts.seed.main --module=vehicle --dry-run
    python -m scripts.seed.main --all --no-refresh-timestamps
    python -m scripts.seed.main --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# FK-respecting run order: parents before dependants.
RUN_ORDER = [
    ("vehicle", "scripts.seed.vehicle"),
    ("locations", "scripts.seed.locations"),
    ("networks", "scripts.seed.networks"),
    ("evse_stalls", "scripts.seed.evse_stalls"),
    ("subscriptions", "scripts.seed.subscriptions"),
    ("charging_sessions", "scripts.seed.charging_sessions"),
    ("battery_status", "scripts.seed.battery_status"),
    ("trip_metrics", "scripts.seed.trip_metrics"),
    ("vehicle_status", "scripts.seed.vehicle_status"),
    ("gps_location", "scripts.seed.gps_location"),
    ("review_queue", "scripts.seed.review_queue"),
    ("gas_prices", "scripts.seed.gas_prices"),
]


async def run_all(
    *, refresh_timestamps: bool, dry_run: bool, gap_report: Path | None = None
) -> dict:
    """Orchestrate all seed modules in one transaction.

    Returns a dict keyed by module name -> rows inserted/affected.

    If *gap_report* is provided, a markdown report of FieldContract gaps is
    written to that path (resolved relative to the current working directory).
    Otherwise gaps are summarized in the log only.
    """
    from db.engine import AsyncSessionLocal
    from scripts.seed.base import (
        TIMESTAMP_SOURCES,
        ContractDrivenSeeder,
        apply_offset_to_table,
        compute_global_offset,
        load_declared_contracts,
        write_contracts_gap_report,
    )
    from scripts.seed.vehicle_status import EXPECTED_CONTRACTS as VS_EXPECTED

    counts = {}
    async with AsyncSessionLocal() as db:
        try:
            # 1) Run modules in order; collect inserted counts
            for name, modpath in RUN_ORDER:
                mod = importlib.import_module(modpath)
                count = await mod.seed(db)
                counts[name] = count
                logger.info("Seed %s -> %d rows", name, count)

            # 2) Apply global time-shift (after generation, before commit)
            if refresh_timestamps:
                offset_result = await compute_global_offset(db)
                if offset_result.has_data:
                    logger.info(
                        "Global time-shift: %s (latest seed timestamp -> now)",
                        offset_result.offset,
                    )
                    # Group by model — re-derive from TIMESTAMP_SOURCES
                    by_model: dict = {}
                    for model, ts_col in TIMESTAMP_SOURCES:
                        by_model.setdefault(model, []).append(ts_col)
                    for model, cols in by_model.items():
                        n = await apply_offset_to_table(
                            db, model, cols, offset_result.offset
                        )
                        logger.info("Shifted %s: %d rows", model.__name__, n)

            # 3) Commit (or rollback on dry-run)
            if dry_run:
                await db.rollback()
                logger.info("Dry-run: rolled back transaction")
            else:
                await db.commit()
                logger.info("Committed all seed changes")

        except Exception:
            await db.rollback()
            raise

    # 4) Detect FieldContract gaps (outside the txn — file IO only)
    declared = load_declared_contracts()
    seeder = ContractDrivenSeeder(declared=declared, expected=VS_EXPECTED)
    for c in VS_EXPECTED:
        seeder.value_for(c.target_db_table, c.target_db_column)
    gaps = seeder.gaps_report()

    if gap_report is not None:
        write_contracts_gap_report(gaps, gap_report)
        logger.info("Wrote contracts-gap report (%d gap(s)) -> %s", len(gaps), gap_report)
    elif gaps:
        logger.info(
            "%d FieldContract gap(s) detected — pass --gap-report PATH to write report",
            len(gaps),
        )

    return counts


async def run_module(name: str, *, dry_run: bool) -> int:
    """Seed a single module by name.

    Returns the number of rows inserted/affected.
    Raises SystemExit if the name is not in RUN_ORDER.
    """
    from db.engine import AsyncSessionLocal

    matched = next((m for n, m in RUN_ORDER if n == name), None)
    if not matched:
        raise SystemExit(f"Unknown module: {name}")
    mod = importlib.import_module(matched)
    async with AsyncSessionLocal() as db:
        try:
            count = await mod.seed(db)
            if dry_run:
                await db.rollback()
            else:
                await db.commit()
            return count
        except Exception:
            await db.rollback()
            raise


def main() -> None:
    """Parse args and orchestrate seed generation."""
    parser = argparse.ArgumentParser(
        prog="seed",
        description="Seed/demo data generator for LightningROD",
    )

    parser.add_argument(
        "--all",
        action="store_true",
        help="Seed all modules",
    )

    parser.add_argument(
        "--module",
        type=str,
        choices=[
            "vehicle",
            "locations",
            "networks",
            "evse_stalls",
            "subscriptions",
            "charging_sessions",
            "battery_status",
            "trip_metrics",
            "vehicle_status",
            "gps_location",
            "gas_prices",
            "review_queue",
        ],
        help="Seed a specific module",
    )

    parser.add_argument(
        "--refresh-timestamps",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Refresh all timestamps to current time (default: True)",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without modifying the database",
    )

    parser.add_argument(
        "--gap-report",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Write a markdown FieldContract gap report to PATH (resolved "
            "relative to the current working directory). If omitted, gaps "
            "are summarized in the log only."
        ),
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.module and args.all:
        raise SystemExit("Use --all or --module, not both")

    if args.all:
        counts = asyncio.run(
            run_all(
                refresh_timestamps=args.refresh_timestamps,
                dry_run=args.dry_run,
                gap_report=args.gap_report,
            )
        )
        total = sum(counts.values())
        print(f"\nSeed complete. Total rows: {total}")
        for name, n in counts.items():
            print(f"  {name}: {n}")
    elif args.module:
        n = asyncio.run(run_module(args.module, dry_run=args.dry_run))
        print(f"{args.module}: {n} rows")
    else:
        parser.print_help()
        raise SystemExit(2)


if __name__ == "__main__":
    main()
