"""phase04 cost schema — is_free, cost_source, app_settings

Revision ID: c9345e830aab
Revises: 7086caea2990
Create Date: 2026-02-28 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

# revision identifiers, used by Alembic.
revision: str = "c9345e830aab"
down_revision: Union[str, Sequence[str], None] = "7086caea2990"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TIMESTAMPTZ = TIMESTAMP(timezone=True)


def upgrade() -> None:
    """Phase 04 cost schema changes."""

    # --- Schema changes ---

    # Add is_free to ev_charging_networks
    op.add_column(
        "ev_charging_networks",
        sa.Column("is_free", sa.Boolean(), nullable=True),
    )

    # Add cost_source to ev_charging_session
    op.add_column(
        "ev_charging_session",
        sa.Column("cost_source", sa.String(length=20), nullable=True),
    )

    # Create app_settings key-value table
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            TIMESTAMPTZ,
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("key"),
    )

    # --- Seed data ---

    # Seed known charging networks into ev_charging_networks
    ev_charging_networks_tbl = sa.table(
        "ev_charging_networks",
        sa.column("network_name", sa.String),
        sa.column("cost_per_kwh", sa.Numeric),
        sa.column("is_free", sa.Boolean),
        sa.column("notes", sa.Text),
    )
    op.bulk_insert(
        ev_charging_networks_tbl,
        [
            {
                "network_name": "Home",
                "cost_per_kwh": 0.12,
                "is_free": False,
                "notes": "Residential electricity rate",
            },
            {
                "network_name": "Work",
                "cost_per_kwh": 0.00,
                "is_free": True,
                "notes": "Free workplace charging",
            },
            {
                "network_name": "Electrify America",
                "cost_per_kwh": 0.43,
                "is_free": False,
                "notes": "One of the largest open fast-charging networks in the U.S.; pricing varies by state and location."
            },
            {
                "network_name": "Tesla (Supercharger, non-Tesla access)",
                "cost_per_kwh": 0.28,
                "is_free": False,
                "notes": "Pricing varies by location"
            },
            {
                "network_name": "EVgo",
                "cost_per_kwh": 0.35,
                "is_free": False,
                "notes": "Public DC fast charger network; membership plans can lower cost per kWh."
            },
            {
                "network_name": "ChargePoint",
                "cost_per_kwh": 0.35,
                "is_free": False,
                "notes": "Mostly Level 2 charging; rates vary by individual host location."
            },
            {
                "network_name": "Blink Charging",
                "cost_per_kwh": 0.39,
                "is_free": False,
                "notes": "Mixed Level 2/DCFC; pricing varies by station owner/operator."
            },
            {
                "network_name": "Greenlots (Shell Recharge)",
                "cost_per_kwh": 0.37,
                "is_free": False,
                "notes": "Part of Shell Recharge network"
            },
            {
                "network_name": "Electrify Canada",
                "cost_per_kwh": 0.40,
                "is_free": False,
                "notes": "CA counterpart to Electrify America."
            },
            {
                "network_name": "EV Connect",
                "cost_per_kwh": 0.32,
                "is_free": False,
                "notes": "Enterprise/host-managed network; rates depend on billing plan the host chooses."
            },
            {
                "network_name": "Wheego",
                "cost_per_kwh": 0.29,
                "is_free": False,
                "notes": "Smaller regional network; general average based on publicly posted pricing."
            },
            {
                "network_name": "Fastned",
                "cost_per_kwh": 0.49,
                "is_free": False,
                "notes": "European high-power DCFC network; often higher kWh pricing due to power costs."
            },
            {
                "network_name": "IONITY",
                "cost_per_kwh": 0.79,
                "is_free": False,
                "notes": "Pan-European high-power charging network; premium pricing common."
            },
            {
                "network_name": "Electrify UK",
                "cost_per_kwh": 0.70,
                "is_free": False,
                "notes": "UK fast chargers with regional pricing; cost per kWh higher in Europe."
            }
        ],
    )

    # Seed default app_settings
    app_settings_tbl = sa.table(
        "app_settings",
        sa.column("key", sa.String),
        sa.column("value", sa.Text),
    )
    op.bulk_insert(
        app_settings_tbl,
        [
            {"key": "gas_price_per_gallon", "value": "3.50"},
            {"key": "vehicle_mpg", "value": "28.0"},
            {"key": "comparison_gas_enabled", "value": "true"},
            {"key": "comparison_network_enabled", "value": "true"},
            {"key": "comparison_section_visible", "value": "true"},
        ],
    )

    # Backfill cost_source for existing sessions that already have a cost
    op.execute(
        "UPDATE ev_charging_session SET cost_source = 'imported'"
        " WHERE cost IS NOT NULL AND cost_source IS NULL"
    )


def downgrade() -> None:
    """Revert phase 04 cost schema changes."""
    op.drop_table("app_settings")
    op.drop_column("ev_charging_session", "cost_source")
    op.drop_column("ev_charging_networks", "is_free")
