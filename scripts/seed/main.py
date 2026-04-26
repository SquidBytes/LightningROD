"""Main entry point for seed/demo data generation.

Usage:
    python -m scripts.seed.main --help
    python -m scripts.seed.main --all
    python -m scripts.seed.main --module=vehicle --dry-run
"""
from __future__ import annotations

import argparse
import sys


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

    args = parser.parse_args()

    # For now, just print the parsed args (logic added in T16)
    print(f"Parsed args: {args}")
    sys.exit(0)


if __name__ == "__main__":
    main()
