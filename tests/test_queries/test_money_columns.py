"""Money columns round-trip as Decimal; other numeric columns as float."""
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from db.models.reference import EVChargingNetwork
from db.models.vehicle import EVVehicle
from web.money import cost_of, to_money
from web.queries.settings import create_network

pytestmark = [pytest.mark.query, pytest.mark.db]


async def test_money_column_equals_stored_value_to_the_cent(db_session):
    net = EVChargingNetwork(network_name="Cent Test", cost_per_kwh=to_money(0.35))
    db_session.add(net)
    await db_session.flush()
    await db_session.refresh(net)

    assert net.cost_per_kwh == Decimal("0.35")
    assert isinstance(net.cost_per_kwh, Decimal)


async def test_float_passed_to_a_create_path_stores_to_the_cent(db_session):
    net = await create_network(db_session, "Float Insert Test", 0.35, False, None)
    await db_session.flush()
    await db_session.refresh(net)

    assert net.cost_per_kwh == Decimal("0.35")


async def test_whole_float_column_and_its_sum_read_as_float(db_session):
    db_session.add(EVVehicle(device_id="FLOAT_TEST", display_name="Float Test", battery_capacity_kwh=80.0))
    await db_session.flush()

    stmt = select(
        EVVehicle.battery_capacity_kwh, func.sum(EVVehicle.battery_capacity_kwh)
    ).where(EVVehicle.device_id == "FLOAT_TEST").group_by(EVVehicle.battery_capacity_kwh)
    value, total = (await db_session.execute(stmt)).one()

    assert type(value) is float
    assert type(total) is float


async def test_to_money_uses_float_repr():
    assert to_money(0.1) == Decimal("0.1")
    assert to_money(None) is None
    assert to_money(Decimal("1.50")) == Decimal("1.50")


async def test_cost_of_has_no_float_error():
    assert cost_of(Decimal("0.48"), 61.3) == Decimal("29.424")
