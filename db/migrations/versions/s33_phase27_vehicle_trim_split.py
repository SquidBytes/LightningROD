"""Split ev_vehicles.trim into trim_level + battery_option.

Phase 27 plan 27-03 (Thread 4a). The existing single `trim` column conflated
trim_level (e.g. Lariat, XLT, Premium) with battery_option (Standard Range
vs Extended Range). That conflation blocked correct (make, model, year, trim)
-> battery cascade auto-fill because a single trim could map to multiple
battery values (e.g. "Lariat" exists with both SR and ER packs).

This migration:
    1. Adds trim_level VARCHAR NULL.
    2. Adds battery_option VARCHAR NULL.
    3. Tokenizes every existing ev_vehicles.trim value per the heuristic
       table in .planning/phases/27-analytics-polish-and-vehicle-preset-overhaul/RESEARCH.md §4.
    4. Drops the old trim column. No backwards-compat shim (per CLAUDE.md).

Downgrade recombines trim_level + battery_option into a single trim string
via COALESCE. Drivetrain suffixes (e.g. "RWD"/"AWD") that were stripped on
upgrade are NOT recovered — downgrade is a best-effort recombination.

Revision ID: s33_phase27_vehicle_trim_split
Revises: s31_battery_gross (chains after current head; plan 27-01's
    s32_phase27_charging_session_temps had not landed when this migration
    was authored, so we chain directly after s31.)
Create Date: 2026-04-12
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "s33_phase27_vehicle_trim_split"
down_revision: Union[str, None] = "s31_battery_gross"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


# (match_where_clause, trim_level_value_or_None, battery_option_value_or_None)
# Order matters: more specific matches first so the bare-name fallbacks at
# the end don't over-claim rows that were already tokenized.
_TOKENIZATION_RULES: list[tuple[str, "str | None", "str | None"]] = [
    # Lariat + battery qualifier combinations
    ("LOWER(trim) IN ('lariat er', 'lariat extended range')", "Lariat", "Extended Range"),
    ("LOWER(trim) = 'lariat sr'", "Lariat", "Standard Range"),
    # Premium (Mach-E) with drivetrain suffix
    ("LOWER(trim) IN ('premium sr rwd', 'premium sr awd', 'premium sr')", "Premium", "Standard Range"),
    ("LOWER(trim) IN ('premium er rwd', 'premium er awd', 'premium er')", "Premium", "Extended Range"),
    # Flash / Platinum / STX (alias -> XLT) — always Extended Range
    ("LOWER(trim) = 'flash'", "Flash", "Extended Range"),
    ("LOWER(trim) = 'platinum'", "Platinum", "Extended Range"),
    ("LOWER(trim) = 'stx'", "XLT", "Extended Range"),
    # GT / GT Performance — always Extended Range
    ("LOWER(trim) IN ('gt', 'gt performance')", "GT", "Extended Range"),
    # Rally / California Route 1 — always Extended Range
    ("LOWER(trim) = 'rally'", "Rally", "Extended Range"),
    ("LOWER(trim) = 'california route 1'", "California Route 1", "Extended Range"),
    # Bare trim names — no battery qualifier
    ("LOWER(trim) = 'lariat'", "Lariat", None),
    ("LOWER(trim) = 'pro'", "Pro", None),
    ("LOWER(trim) = 'xlt'", "XLT", None),
    ("LOWER(trim) = 'select'", "Select", None),
    # Battery qualifier only — no trim level
    ("LOWER(trim) IN ('standard range', 'sr', 'std range')", None, "Standard Range"),
    ("LOWER(trim) IN ('extended range', 'er', 'ext range')", None, "Extended Range"),
]


def _build_update_sql(where_clause: str, trim_level: "str | None", battery_option: "str | None") -> str:
    """Build a parameterized UPDATE statement for one tokenization rule."""
    assignments = []
    if trim_level is None:
        assignments.append("trim_level = NULL")
    else:
        assignments.append(f"trim_level = '{trim_level.replace(chr(39), chr(39) * 2)}'")
    if battery_option is None:
        assignments.append("battery_option = NULL")
    else:
        assignments.append(f"battery_option = '{battery_option.replace(chr(39), chr(39) * 2)}'")
    return (
        f"UPDATE ev_vehicles SET {', '.join(assignments)} "
        f"WHERE trim IS NOT NULL AND trim_level IS NULL AND battery_option IS NULL "
        f"AND {where_clause}"
    )


def upgrade() -> None:
    op.add_column(
        "ev_vehicles",
        sa.Column("trim_level", sa.String(), nullable=True),
    )
    op.add_column(
        "ev_vehicles",
        sa.Column("battery_option", sa.String(), nullable=True),
    )
    # Tokenize every recognized trim value. Rules applied in order; the guard
    # `trim_level IS NULL AND battery_option IS NULL` prevents later rules
    # from overwriting already-classified rows. Unmatched trim values stay
    # NULL on both new columns (non-Ford vehicles, free text, etc.).
    for where_clause, trim_level_val, battery_option_val in _TOKENIZATION_RULES:
        op.execute(sa.text(_build_update_sql(where_clause, trim_level_val, battery_option_val)))
    op.drop_column("ev_vehicles", "trim")


def downgrade() -> None:
    op.add_column(
        "ev_vehicles",
        sa.Column("trim", sa.String(), nullable=True),
    )
    # Best-effort recombination. Drivetrain tokens (RWD/AWD) and aliases
    # (STX, GT Performance) that were normalized on upgrade are NOT restored.
    op.execute(sa.text(
        "UPDATE ev_vehicles SET trim = COALESCE("
        "  trim_level || ' ' || battery_option, "
        "  trim_level, "
        "  battery_option"
        ") WHERE trim_level IS NOT NULL OR battery_option IS NOT NULL"
    ))
    op.drop_column("ev_vehicles", "battery_option")
    op.drop_column("ev_vehicles", "trim_level")
