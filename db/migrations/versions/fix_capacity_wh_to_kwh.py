"""Repair hv_battery_capacity rows stored in raw Wh.

The FordPass API reports pack capacity in Wh (131 kWh pack -> 131000);
ingestion stored the value unconverted, so battery health and degradation
compared Wh-scale readings against a rated-kWh capacity. Divide affected
rows by 1000. No real EV pack exceeds 1000 kWh, so `> 1000` cleanly
separates Wh-scale rows from correct kWh ones; the update is idempotent.

Revision ID: fix_capacity_wh_to_kwh
Revises: p34_battery_trips_overhaul
Create Date: 2026-07-05
"""
from __future__ import annotations

from alembic import op

revision = "fix_capacity_wh_to_kwh"
down_revision = "p34_battery_trips_overhaul"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE ev_battery_status "
        "SET hv_battery_capacity = hv_battery_capacity / 1000.0 "
        "WHERE hv_battery_capacity > 1000"
    )


def downgrade() -> None:
    # Irreversible data repair: Wh-scale rows can't be distinguished from
    # legitimate kWh values after the fix. No-op.
    pass
