"""Seed module: charging network subscriptions."""

from __future__ import annotations

import logging
from datetime import date
from typing import TypedDict

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.reference import EVChargingNetwork, EVNetworkSubscription

logger = logging.getLogger(__name__)


class _SubscriptionSpec(TypedDict):
    network_name: str
    member_rate: float
    monthly_fee: float
    start_date: date
    end_date: date | None
    notes: str


async def _resolve_network_id(db: AsyncSession, network_name: str) -> int | None:
    return (
        await db.execute(
            select(EVChargingNetwork.id).where(
                EVChargingNetwork.network_name == network_name
            )
        )
    ).scalar_one_or_none()


async def _update_network_cost_per_kwh(
    db: AsyncSession, network_name: str, cost_per_kwh: float
) -> None:
    """Upsert the non-member cost_per_kwh on an existing network row."""
    net_id = await _resolve_network_id(db, network_name)
    if net_id is None:
        logger.warning(
            "Network %r not found — skipping cost_per_kwh upsert", network_name
        )
        return
    await db.execute(
        update(EVChargingNetwork)
        .where(EVChargingNetwork.network_name == network_name)
        .values(cost_per_kwh=cost_per_kwh)
    )


async def seed(db: AsyncSession) -> int:
    """Insert EVNetworkSubscription rows for known network plans.

    Idempotent: skips rows where (network_id, start_date) already exists.
    Returns the number of rows inserted.
    """
    await _update_network_cost_per_kwh(db, "Tesla Supercharger", 0.57)
    await _update_network_cost_per_kwh(db, "Electrify America", 0.56)

    specs: list[_SubscriptionSpec] = [
        {
            "network_name": "Tesla Supercharger",
            "member_rate": 0.39,  # $/kWh with Tesla membership, medium congestion
            "monthly_fee": 12.99,
            "start_date": date(2026, 1, 1),
            "end_date": None,
            "notes": (
                "tier:medium-congestion (low=0.31, med=0.39, high=0.44; "
                "schema does not yet model congestion tiers — using medium as "
                "representative)"
            ),
        },
        {
            "network_name": "Electrify America",
            "member_rate": 0.42,  # $/kWh with EA Pass+ membership
            "monthly_fee": 7.00,
            "start_date": date(2026, 1, 1),
            "end_date": None,
            "notes": "tier:pass-plus (25% off non-member rate of $0.56/kWh)",
        },
        {
            "network_name": "ChargePoint",
            "member_rate": 0.00,  # free tier — pay-as-you-go per session
            "monthly_fee": 0.00,
            "start_date": date(2026, 1, 1),
            "end_date": None,
            "notes": "tier:free",
        },
    ]

    inserted = 0
    for spec in specs:
        net_id = await _resolve_network_id(db, spec["network_name"])
        if net_id is None:
            logger.warning(
                "Network %r not found — skipping subscription", spec["network_name"]
            )
            continue

        existing = (
            await db.execute(
                select(EVNetworkSubscription.id).where(
                    EVNetworkSubscription.network_id == net_id,
                    EVNetworkSubscription.start_date == spec["start_date"],
                )
            )
        ).scalar_one_or_none()

        if existing is not None:
            logger.debug(
                "Subscription for network_id=%s start=%s already exists — skipping",
                net_id,
                spec["start_date"],
            )
            continue

        db.add(
            EVNetworkSubscription(
                network_id=net_id,
                member_rate=spec["member_rate"],
                monthly_fee=spec["monthly_fee"],
                start_date=spec["start_date"],
                end_date=spec["end_date"],
                notes=spec["notes"],
            )
        )
        inserted += 1

    await db.flush()
    return inserted
