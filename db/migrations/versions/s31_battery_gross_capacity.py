"""Add battery_gross_capacity_kwh column to ev_vehicles.

Separates pack gross capacity (what FordPass reports via maximumBatteryCapacity)
from usable capacity (driver-facing, what energy_kwh can be compared against).

The existing battery_capacity_kwh column retains its semantic as USABLE capacity
(what it was populated with historically from the preset table). The new column
stores GROSS pack capacity. Health/degradation math on /battery must use GROSS
to match the FordPass sensor values in ev_battery_status.hv_battery_capacity.

No backfill — existing vehicle records need the user to re-pick a preset or
enter the gross value manually. This is a deliberate choice to avoid guessing
at ratios on rows that may have been hand-entered with non-Ford specs.

Revision ID: s31_battery_gross
Revises: s30_metric_canonical
Create Date: 2026-04-11
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "s31_battery_gross"
down_revision: Union[str, None] = "s30_metric_canonical_storage"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column(
        "ev_vehicles",
        sa.Column("battery_gross_capacity_kwh", sa.Numeric(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ev_vehicles", "battery_gross_capacity_kwh")
