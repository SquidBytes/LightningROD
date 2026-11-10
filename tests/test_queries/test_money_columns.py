"""Money columns round-trip as Decimal; other numeric columns as float."""
from decimal import Decimal

import pytest

from db.models.reference import EVChargingNetwork
from web.money import to_money

pytestmark = [pytest.mark.query, pytest.mark.db]


async def test_money_column_equals_stored_value_to_the_cent(db_session):
    net = EVChargingNetwork(network_name="Cent Test", cost_per_kwh=to_money(0.35))
    db_session.add(net)
    await db_session.flush()
    await db_session.refresh(net)

    assert net.cost_per_kwh == Decimal("0.35")
    assert isinstance(net.cost_per_kwh, Decimal)


async def test_to_money_uses_float_repr():
    assert to_money(0.1) == Decimal("0.1")
    assert to_money(None) is None
    assert to_money(Decimal("1.50")) == Decimal("1.50")
