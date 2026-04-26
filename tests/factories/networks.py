"""Factories for EVChargingNetwork and EVNetworkSubscription model instances."""

from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from db.models.reference import EVChargingNetwork, EVNetworkSubscription
from tests.factories import BaseFactory


class NetworkFactory(BaseFactory):
    """Create EVChargingNetwork instances with realistic network data."""

    @classmethod
    async def create(cls, db: AsyncSession, **overrides) -> EVChargingNetwork:
        n = cls._next_id()
        defaults = {
            "network_name": f"Test Network {n}",
            "cost_per_kwh": cls._random_float(0.20, 0.55),
            "is_verified": True,
            "source_system": "test_factory",
        }
        defaults.update(overrides)
        network = EVChargingNetwork(**defaults)
        db.add(network)
        await db.flush()
        return network


class SubscriptionFactory(BaseFactory):
    """Create EVNetworkSubscription instances for a given network."""

    @classmethod
    async def create(cls, db: AsyncSession, **overrides) -> EVNetworkSubscription:
        cls._next_id()
        start = date.today() - timedelta(days=90)
        defaults = {
            "member_rate": cls._random_float(0.10, 0.30),
            "monthly_fee": cls._random_float(5.0, 15.0),
            "start_date": start,
            "end_date": start + timedelta(days=180),
        }
        defaults.update(overrides)
        subscription = EVNetworkSubscription(**defaults)
        db.add(subscription)
        await db.flush()
        return subscription
