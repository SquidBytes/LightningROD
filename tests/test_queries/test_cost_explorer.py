"""Unit tests for query_cost_explorer aggregator."""

from datetime import date

import pytest

from db.models.reference import EVNetworkSubscription
from web.queries.cost_explorer import (  # noqa: F401
    calculate_fee_breakdown,
    query_cost_explorer,
)

pytestmark = [pytest.mark.query, pytest.mark.db]


# ---------------------------------------------------------------------------
# Happy-path tests against cost_scenario fixture
# ---------------------------------------------------------------------------


async def test_query_cost_explorer_totals(cost_scenario):
    """Headline totals and reference math line up with fixture-computed expectations."""
    db = cost_scenario["db"]

    result = await query_cost_explorer(db, time_range="all", reference_rate=0.50)

    assert result["total_paid_actual"] > 0
    assert result["unconfigured_count"] >= 0
    # totals_row.total_paid is the energy-only sum (matches per-row totals so
    # the ledger footer arithmetic is consistent). The headline
    # total_paid_actual now includes subscription fees on top of that.
    assert result["totals_row"]["total_paid"] == result["total_paid_energy"]
    assert result["total_paid_actual"] == round(
        result["total_paid_energy"] + result["total_fees_paid"], 2
    )
    assert isinstance(result["by_network"], list)
    assert result["subscription"] is not None
    # When reference_rate=0.50, total_at_reference = sum(session.kWh) * 0.50.
    expected_at_ref = result["totals_row"]["total_kwh"] * 0.50
    assert abs(result["total_at_reference"] - round(expected_at_ref, 2)) < 0.05


async def test_query_cost_explorer_range_level_totals_and_ratios(cost_scenario):
    """Range-level counts, distance, and all-in cost ratios derive consistently."""
    db = cost_scenario["db"]

    result = await query_cost_explorer(db, time_range="all", reference_rate=0.50)

    # Counts mirror the ledger totals row.
    assert result["total_sessions"] == result["totals_row"]["session_count"]
    assert result["total_kwh"] == result["totals_row"]["total_kwh"]
    assert result["total_distance"] >= 0.0
    assert result["free_session_count"] >= 0

    # $/kWh = all-in total / total kWh.
    if result["total_kwh"] > 0:
        expected_kwh = round(
            result["total_paid_actual"] / result["total_kwh"], 3
        )
        assert result["cost_per_kwh"] == expected_kwh
    # $/session = all-in total / session count.
    if result["total_sessions"] > 0:
        expected_session = round(
            result["total_paid_actual"] / result["total_sessions"], 2
        )
        assert result["cost_per_session"] == expected_session
    # $/km divides by raw km distance (the route converts to display units).
    if result["total_distance"] > 0:
        expected_dist = round(
            result["total_paid_actual"] / result["total_distance"], 4
        )
        assert result["cost_per_distance_km"] == expected_dist
    else:
        assert result["cost_per_distance_km"] is None


async def test_query_cost_explorer_strip_ignores_network_filter(cost_scenario):
    """Range-level strip figures stay constant when a network filter is applied.

    The card's summary strip is range-scoped; the route calls the aggregator a
    second time without the filter and reads the strip keys from there.
    """
    db = cost_scenario["db"]

    unscoped = await query_cost_explorer(db, time_range="all", reference_rate=0.50)
    first_net_id = unscoped["by_network"][0]["network_id"]
    scoped = await query_cost_explorer(
        db, time_range="all", network_ids=[first_net_id], reference_rate=0.50
    )

    # The scoped call narrows its own totals — that's expected. The route uses
    # the UNSCOPED call for the strip, so this test asserts the unscoped figure
    # is the broader one (the strip is built from it).
    assert unscoped["total_sessions"] >= scoped["total_sessions"]
    assert unscoped["total_paid_actual"] >= scoped["total_paid_actual"]


async def test_query_cost_explorer_free_charging_strip_figures(cost_scenario):
    """free_charging_saved and cost_per_free_session populate when free sessions exist."""
    db = cost_scenario["db"]

    result = await query_cost_explorer(db, time_range="all", reference_rate=0.40)

    if result["free_session_count"] > 0:
        assert result["free_charging_saved"] >= 0.0
        if result["cost_per_free_session"] is not None:
            expected = round(
                result["free_charging_saved"] / result["free_session_count"], 2
            )
            assert result["cost_per_free_session"] == expected
    else:
        assert result["free_charging_saved"] == 0.0
        assert result["cost_per_free_session"] is None


async def test_query_cost_explorer_network_filter(cost_scenario):
    """Network filter scopes both totals and the by_network breakdown."""
    db = cost_scenario["db"]

    unscoped = await query_cost_explorer(db, time_range="all")
    assert len(unscoped["by_network"]) >= 1

    first_net_id = unscoped["by_network"][0]["network_id"]
    scoped = await query_cost_explorer(
        db, time_range="all", network_ids=[first_net_id]
    )

    assert len(scoped["by_network"]) == 1
    assert scoped["by_network"][0]["network_id"] == first_net_id
    # Energy-only sum lines up with the per-row total; the headline adds any
    # subscription fees on top.
    assert scoped["total_paid_energy"] == scoped["by_network"][0]["total_paid"]
    # Exactly one network in scope -> single_network_detail is populated.
    assert scoped["single_network_detail"] is not None
    assert scoped["single_network_detail"]["network_id"] == first_net_id


async def test_query_cost_explorer_free_what_if_global(cost_scenario):
    """Global free-charging what-if rebills free sessions at reference_rate."""
    db = cost_scenario["db"]

    baseline = await query_cost_explorer(db, time_range="all", reference_rate=0.40)
    what_if = await query_cost_explorer(
        db,
        time_range="all",
        free_charging_what_if=True,
        free_charging_scope="global",
        reference_rate=0.40,
    )

    free_session_count = sum(r["free_session_count"] for r in baseline["by_network"])
    if free_session_count > 0:
        assert what_if["total_paid_what_if"] > baseline["total_paid_actual"]
    else:
        assert what_if["total_paid_what_if"] == baseline["total_paid_actual"]
    # Actual must NOT change between the two calls.
    assert what_if["total_paid_actual"] == baseline["total_paid_actual"]


async def test_query_cost_explorer_free_what_if_per_network_multi(cost_scenario):
    """Per-network rebill applies to every network in the passed list, not just one.

    Guards WR-01: the form previously dropped all but the last selected
    free-charging network, so a two-network rebill silently behaved like a
    one-network rebill.
    """
    from datetime import datetime

    from db.models.charging_session import EVChargingSession
    from db.models.reference import EVChargingNetwork
    from tests.test_queries.conftest import DEVICE_ID

    db = cost_scenario["db"]

    # Two free-charging networks, each with one free session.
    free_x = EVChargingNetwork(
        network_name="Free X", cost_per_kwh=0.0, is_free=True,
        is_verified=True, source_system="test_fixture",
    )
    free_y = EVChargingNetwork(
        network_name="Free Y", cost_per_kwh=0.0, is_free=True,
        is_verified=True, source_system="test_fixture",
    )
    db.add_all([free_x, free_y])
    await db.flush()
    db.add_all([
        EVChargingSession(
            device_id=DEVICE_ID, energy_kwh=30.0, network_id=free_x.id,
            session_start_utc=datetime(2025, 6, 5, 10, 0, 0),
            is_complete=True, source_system="test_fixture",
        ),
        EVChargingSession(
            device_id=DEVICE_ID, energy_kwh=20.0, network_id=free_y.id,
            session_start_utc=datetime(2025, 6, 6, 10, 0, 0),
            is_complete=True, source_system="test_fixture",
        ),
    ])
    await db.flush()

    baseline = await query_cost_explorer(db, time_range="all", reference_rate=0.40)
    eligible = {n["network_id"] for n in baseline["free_charging_eligible_networks"]}
    assert {free_x.id, free_y.id} <= eligible

    # Rebill only Free X.
    one = await query_cost_explorer(
        db, time_range="all", free_charging_what_if=True,
        free_charging_scope="per_network", free_charging_networks=[free_x.id],
        reference_rate=0.40,
    )
    # Rebill both — must move further than the single-network rebill.
    both = await query_cost_explorer(
        db, time_range="all", free_charging_what_if=True,
        free_charging_scope="per_network",
        free_charging_networks=[free_x.id, free_y.id],
        reference_rate=0.40,
    )

    # Free X rebill adds 30 kWh * 0.40 = 12.00; Free Y adds 20 * 0.40 = 8.00.
    assert round(one["total_paid_what_if"] - baseline["total_paid_what_if"], 2) == 12.00
    assert round(both["total_paid_what_if"] - baseline["total_paid_what_if"], 2) == 20.00
    # Actuals are unaffected by the what-if.
    assert both["total_paid_actual"] == baseline["total_paid_actual"]


async def test_query_cost_explorer_subscription_counterfactual(cost_scenario):
    """With/Without/Net saved derive consistently when a subscription is active."""
    db = cost_scenario["db"]

    result = await query_cost_explorer(db, time_range="all", reference_rate=0.50)

    sub = result["subscription"]
    if sub["active"]:
        # With == energy_at_member_rate + fees_total (within a cent of rounding).
        assert abs(
            sub["with_total"]
            - (sub["with_energy_at_member_rate"] + sub["with_fees_total"])
        ) < 0.02
        # Net saved == without_total - with_total.
        assert abs(sub["net_saved"] - (sub["without_total"] - sub["with_total"])) < 0.02
        # At least one month of fees counted when active.
        assert sub["with_fees_count"] >= 1
    else:
        # Inactive — only structural keys are required.
        assert sub["active"] is False
        assert sub.get("subscriptions", []) == []


async def test_query_cost_explorer_zero_reference_rate(cost_scenario):
    """reference_rate=0.0 disables the reference comparison (Q-07 edge case)."""
    db = cost_scenario["db"]

    result = await query_cost_explorer(db, time_range="all", reference_rate=0.0)

    assert result["total_at_reference"] == 0.0
    # total_savings_vs_reference = total_at_reference - total_paid_energy
    # (apples-to-apples — fees aren't a per-kWh quantity so they're excluded
    # from this comparison). With rate=0 that collapses to -total_paid_energy.
    assert result["total_savings_vs_reference"] == round(-result["total_paid_energy"], 2)
    for row in result["by_network"]:
        assert row["total_at_reference"] == 0.0
        # Per-row delta is total_paid - total_at_reference (positive when you paid
        # more than the reference); with rate=0 it collapses to total_paid.
        assert row["delta"] == round(row["total_paid"], 2)


async def test_totals_row_reference_label_network_mode(cost_scenario):
    """Network-rate mode formats the tfoot label as "{network_name} - $X.XX/kWh"."""
    db = cost_scenario["db"]

    result = await query_cost_explorer(
        db,
        time_range="all",
        reference_rate=0.40,
        reference_network_name="Blink",
        reference_network_id=99,
    )
    totals = result["totals_row"]
    assert totals["reference_label"] == "Blink - $0.40/kWh"
    # paid_per_kwh + reference_per_kwh are populated when energy > 0.
    assert totals["paid_per_kwh"] is not None
    assert totals["reference_per_kwh"] == round(0.40, 2)


async def test_totals_row_reference_label_custom_mode(cost_scenario):
    """Custom-rate mode (no network name) formats the label as bare "$X.XX/kWh"."""
    db = cost_scenario["db"]

    result = await query_cost_explorer(
        db,
        time_range="all",
        reference_rate=0.52,
        reference_network_name=None,
    )
    totals = result["totals_row"]
    assert totals["reference_label"] == "$0.52/kWh"
    assert totals["reference_per_kwh"] == 0.52


async def test_totals_row_reference_label_multi_network_weighted(cost_scenario):
    """No-reference-set case: reference_label is None and reference_per_kwh is None."""
    db = cost_scenario["db"]

    # Multi-network scope with reference_rate=0 -> no reference comparison.
    result = await query_cost_explorer(db, time_range="all", reference_rate=0.0)
    totals = result["totals_row"]
    assert totals["reference_label"] is None
    assert totals["reference_per_kwh"] is None
    # paid_per_kwh still surfaces (it's independent of reference state).
    assert totals["paid_per_kwh"] is not None
    # Weighted-average reference math: when reference_rate is set in network
    # mode and multiple networks are in scope, reference_per_kwh equals the
    # uniform reference_rate (since total_at_reference = total_kwh * rate).
    weighted = await query_cost_explorer(
        db,
        time_range="all",
        reference_rate=0.45,
        reference_network_name="Network B",
    )
    assert weighted["totals_row"]["reference_per_kwh"] == 0.45
    assert weighted["totals_row"]["reference_label"] == "Network B - $0.45/kWh"


async def test_query_cost_explorer_per_subscription_breakdown(cost_scenario):
    """subscription.subscriptions exposes one entry per network with energy + fees."""
    db = cost_scenario["db"]

    # Add a second subscription on Network A so the breakdown carries two rows.
    net_a_sub = EVNetworkSubscription(
        network_id=cost_scenario["net_a"].id,
        member_rate=0.20,
        monthly_fee=4.99,
        start_date=date(2025, 6, 1),
        end_date=date(2025, 8, 1),
    )
    db.add(net_a_sub)
    await db.flush()

    result = await query_cost_explorer(db, time_range="all", reference_rate=0.50)
    sub = result["subscription"]
    assert sub["active"] is True

    subs = sub["subscriptions"]
    by_net_name = {row["network_name"]: row for row in subs}

    # Both networks should have a breakdown row.
    assert "Network A" in by_net_name
    assert "Network B" in by_net_name

    # Per-row structural shape: id + name + numeric energy + numeric fee.
    for row in subs:
        assert isinstance(row["network_id"], int)
        assert isinstance(row["network_name"], str)
        assert isinstance(row["energy_at_member_rate"], float)
        assert isinstance(row["member_fee"], float)
        assert row["member_fee"] >= 0.0

    # Fees should sum to the headline `with_fees_total`.
    assert abs(
        sum(r["member_fee"] for r in subs) - sub["with_fees_total"]
    ) < 0.02

    # Energy sum across networks should not exceed total paid (free sessions
    # don't carry a subscription so they're excluded from the per-net energy).
    assert sum(r["energy_at_member_rate"] for r in subs) <= sub["with_energy_at_member_rate"] + 0.02
