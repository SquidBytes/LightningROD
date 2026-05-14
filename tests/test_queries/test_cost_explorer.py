"""Unit tests for query_cost_explorer aggregator."""

import pytest

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
    assert result["totals_row"]["total_paid"] == result["total_paid_actual"]
    assert isinstance(result["by_network"], list)
    assert result["subscription"] is not None
    # When reference_rate=0.50, total_at_reference = sum(session.kWh) * 0.50.
    expected_at_ref = result["totals_row"]["total_kwh"] * 0.50
    assert abs(result["total_at_reference"] - round(expected_at_ref, 2)) < 0.05


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
    assert scoped["total_paid_actual"] == scoped["by_network"][0]["total_paid"]
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
        # Inactive -> nothing else is asserted on the block.
        assert sub == {"active": False}


async def test_query_cost_explorer_zero_reference_rate(cost_scenario):
    """reference_rate=0.0 disables the reference comparison (Q-07 edge case)."""
    db = cost_scenario["db"]

    result = await query_cost_explorer(db, time_range="all", reference_rate=0.0)

    assert result["total_at_reference"] == 0.0
    # total_savings_vs_reference = total_at_reference - total_paid_actual;
    # with rate=0 that's -total_paid_actual (negative — you "saved" nothing vs an
    # all-free counterfactual, in fact you paid more).
    assert result["total_savings_vs_reference"] == round(-result["total_paid_actual"], 2)
    for row in result["by_network"]:
        assert row["total_at_reference"] == 0.0
        # Per-row delta is total_paid - total_at_reference (positive when you paid
        # more than the reference); with rate=0 it collapses to total_paid.
        assert row["delta"] == round(row["total_paid"], 2)
