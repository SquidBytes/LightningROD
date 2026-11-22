"""Money columns: totals as cents, rates to four places, floats rejected; measurements as float."""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import StatementError

from db.models import Base
from db.models.base import Cents, Rate
from db.models.charging_session import EVChargingSession
from db.models.reference import EVChargingNetwork, EVNetworkSubscription
from db.models.vehicle import EVVehicle
from tests.factories.networks import NetworkFactory
from tests.factories.sessions import ChargingSessionFactory
from web.money import cost_of, to_cents, to_money
from web.queries.costs import avg_cost_per_session, cost_per_kwh
from web.queries.settings import create_network

pytestmark = [pytest.mark.query, pytest.mark.db]

DEVICE = "MONEY_TEST"
MONEY_COLUMNS = {
    "ev_charging_session.cost": Cents,
    "ev_charging_session.cost_without_overrides": Cents,
    "ev_charging_session.estimated_cost": Cents,
    "ev_network_subscriptions.monthly_fee": Cents,
    "ev_network_subscriptions.member_rate": Rate,
    "ev_charging_networks.cost_per_kwh": Rate,
    "ev_location_lookup.cost_per_kwh": Rate,
    "gas_price_history.station_price": Rate,
    "gas_price_history.average_price": Rate,
    "gas_price_readings.price": Rate,
}


async def _session(db, cost: Decimal | None, energy_kwh: float = 10.0) -> EVChargingSession:
    session = await ChargingSessionFactory.create(
        db, device_id=DEVICE, cost=cost, energy_kwh=energy_kwh
    )
    await db.refresh(session)
    return session


async def _three_sessions(db) -> None:
    for cost, kwh in (("10.00", 20.0), ("20.50", 30.0), ("30.25", 50.0)):
        await _session(db, Decimal(cost), kwh)
    await _session(db, None, 5.0)


# --- floats are rejected ---


def test_every_money_column_uses_a_money_type():
    mapped = {
        f"{table.name}.{column.name}": type(column.type)
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if isinstance(column.type, (Cents, Rate))
    }

    assert mapped == MONEY_COLUMNS


@pytest.mark.parametrize("dialect", [postgresql.dialect(), sqlite.dialect()], ids=["pg", "sqlite"])
@pytest.mark.parametrize("money_type", [Cents(), Rate()], ids=["cents", "rate"])
def test_money_type_rejects_float_and_accepts_int_and_decimal(money_type, dialect):
    with pytest.raises(TypeError):
        money_type.process_bind_param(0.35, dialect)
    money_type.process_bind_param(3, dialect)
    money_type.process_bind_param(Decimal("0.35"), dialect)


@pytest.mark.parametrize(
    "row",
    [
        lambda: EVChargingSession(device_id=DEVICE, is_complete=True, cost=12.5),
        lambda: EVChargingNetwork(network_name="Float Rate", cost_per_kwh=0.35),
    ],
    ids=["total", "rate"],
)
async def test_float_written_to_a_money_column_raises_type_error(db_session, row):
    db_session.add(row())

    # SQLAlchemy wraps an error raised while binding; the TypeError is its cause.
    with pytest.raises(StatementError) as raised:
        await db_session.flush()
    assert isinstance(raised.value.orig, TypeError)


def test_to_money_parses_text_and_rejects_float():
    assert to_money("0.35") == Decimal("0.35")
    assert to_money(" 12.50 ") == Decimal("12.50")
    assert to_money(3) == Decimal("3")
    assert to_money("") is None
    assert to_money(None) is None
    with pytest.raises(TypeError):
        to_money(0.35)  # type: ignore[call-overload]


# --- totals round to the cent, half up ---


@pytest.mark.parametrize(
    ("written", "stored"),
    [("29.424", "29.42"), ("29.425", "29.43"), ("15", "15.00"), ("-0.005", "-0.01")],
)
async def test_total_rounds_half_up_to_the_cent(db_session, written, stored):
    session = await _session(db_session, Decimal(written))

    assert session.cost == Decimal(stored)
    assert session.cost.as_tuple().exponent == -2


async def test_monthly_fee_is_stored_to_the_cent(db_session):
    network = await NetworkFactory.create(db_session)
    sub = EVNetworkSubscription(
        network_id=network.id,
        member_rate=Decimal("0.31"),
        monthly_fee=Decimal("12.995"),
        start_date=date(2026, 1, 1),
    )
    db_session.add(sub)
    await db_session.flush()
    await db_session.refresh(sub)

    assert sub.monthly_fee == Decimal("13.00")


def test_cost_of_rounds_to_the_cent():
    assert cost_of(Decimal("0.48"), 61.3) == Decimal("29.42")
    assert cost_of(Decimal("0.35"), 10.5) == Decimal("3.68")  # 3.675 rounds up
    assert to_cents(Decimal("29.425")) == Decimal("29.43")


# --- rates keep four places ---


@pytest.mark.parametrize(("written", "stored"), [("0.4321", "0.4321"), ("0.123456", "0.1235")])
async def test_rate_keeps_four_places(db_session, written, stored):
    network = await create_network(db_session, "Rate Test", Decimal(written), False, None)
    await db_session.refresh(network)

    assert network.cost_per_kwh == Decimal(stored)
    assert isinstance(network.cost_per_kwh, Decimal)


# --- SQL over a cents column comes back in dollars ---


async def test_sum_min_and_max_of_a_total_are_dollars(db_session):
    await _three_sessions(db_session)

    cost = EVChargingSession.cost
    stmt = select(func.sum(cost), func.min(cost), func.max(cost)).where(
        EVChargingSession.device_id == DEVICE
    )

    assert (await db_session.execute(stmt)).one() == (
        Decimal("60.75"),
        Decimal("10.00"),
        Decimal("30.25"),
    )


async def test_average_of_a_total_is_dollars(db_session):
    await _three_sessions(db_session)

    # avg() does not inherit the column type, so it is typed here.
    typed_avg = select(func.avg(EVChargingSession.cost, type_=Cents())).where(
        EVChargingSession.device_id == DEVICE
    )

    assert (await db_session.execute(typed_avg)).scalar_one() == Decimal("20.25")
    assert await avg_cost_per_session(db_session, device_id=DEVICE) == pytest.approx(20.25)


async def test_cost_per_kwh_is_dollars(db_session):
    await _three_sessions(db_session)

    assert await cost_per_kwh(db_session, device_id=DEVICE) == pytest.approx(0.6075)


async def test_filter_by_amount_compares_dollars(db_session):
    await _three_sessions(db_session)

    async def count(clause) -> int:
        stmt = select(func.count()).where(EVChargingSession.device_id == DEVICE, clause)
        return (await db_session.execute(stmt)).scalar_one()

    cost = EVChargingSession.cost
    assert await count(cost > Decimal("20")) == 2
    assert await count(cost == Decimal("20.50")) == 1
    assert await count(cost.between(Decimal("10"), Decimal("20.50"))) == 2
    assert await count(cost.in_([Decimal("10.00"), Decimal("30.25")])) == 2


# --- measurements are floats ---


async def test_whole_float_column_and_its_aggregates_read_as_float(db_session):
    db_session.add(EVVehicle(device_id="FLOAT_TEST", display_name="Float Test", battery_capacity_kwh=80))
    await db_session.flush()

    capacity = EVVehicle.battery_capacity_kwh
    stmt = (
        select(capacity, func.sum(capacity), func.avg(capacity))
        .where(EVVehicle.device_id == "FLOAT_TEST")
        .group_by(capacity)
    )
    row = (await db_session.execute(stmt)).one()

    assert tuple(row) == (80.0, 80.0, 80.0)
    assert [type(value) for value in row] == [float, float, float]
