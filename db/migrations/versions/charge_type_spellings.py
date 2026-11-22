"""Collapse every known charger-type spelling to AC/DC.

CSV import used to store these codes unchanged; it now stores AC or DC, so
rows from before would land in different filters and chart slices.

Revision ID: charge_type_spellings
Revises: money_storage
Create Date: 2026-11-22
"""
from __future__ import annotations

from alembic import op

revision = "charge_type_spellings"
down_revision = "money_storage"
branch_labels = None
depends_on = None

# A copy, not an import: a migration must keep working when the app's map changes.
SPELLINGS = {
    "AC": [
        "AC_BASIC", "AC_LEVEL_1", "AC_LEVEL_2", "AC_CHARGING", "AC LEVEL 1", "AC LEVEL 2",
        "LEVEL_1", "LEVEL_2", "LEVEL 1", "LEVEL 2", "L1", "L2",
    ],
    "DC": [
        "DC_FAST", "DC_DCFAST", "DC_COMBO", "DC_CHARGING", "DC FAST", "DCFC", "L3", "LEVEL 3",
    ],
}


def upgrade() -> None:
    for code, spellings in SPELLINGS.items():
        quoted = ", ".join(f"'{s}'" for s in spellings + [code])
        op.execute(
            f"UPDATE ev_charging_session SET charge_type = '{code}' "
            f"WHERE UPPER(TRIM(charge_type)) IN ({quoted}) AND charge_type <> '{code}'"
        )


def downgrade() -> None:
    # Collapsed rows can't be told apart from rows that were AC/DC already. No-op.
    pass
