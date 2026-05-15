"""Cost Explorer aggregator — single-query backend for the /charging/costs Cost Explorer card."""

from datetime import date
from typing import Any

import plotly.graph_objects as go
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from db.models.charging_session import EVChargingSession
from db.models.reference import EVNetworkSubscription
from web.queries.costs import (
    _HOVER_LABEL,
    _PLOTLY_CONFIG,
    _wrap_chart,
    build_time_filter,
    calculate_monthly_fees_in_range,  # noqa: F401  (re-exported for callers wanting the legacy total)
    compute_session_cost,
    find_active_subscription,  # noqa: F401
    get_locations_by_id,
    get_networks_by_id,
)
from web.queries.settings import get_all_subscriptions_by_network


async def get_charge_type_network_groupings(
    db: AsyncSession,
    *,
    time_range: str = "all",
    device_id: str | None = None,
) -> dict[str, list[int]]:
    """Return network IDs grouped by session-history AC/DC mix.

    A network appears in `ac` if the user has at least one AC session on it
    within the time range, in `dc` likewise. Networks can appear in both
    (mixed-use networks like ChargePoint or Tesla Supercharger). Networks with
    no charge_type recorded contribute to neither list.
    """
    stmt = (
        select(EVChargingSession.network_id, EVChargingSession.charge_type)
        .where(EVChargingSession.network_id.is_not(None))
        .where(EVChargingSession.charge_type.is_not(None))
        .distinct()
    )
    time_filter = build_time_filter(time_range)
    if time_filter is not None:
        stmt = stmt.where(time_filter)
    if device_id:
        stmt = stmt.where(EVChargingSession.device_id == device_id)

    result = await db.execute(stmt)
    ac_ids: set[int] = set()
    dc_ids: set[int] = set()
    for net_id, ct in result.all():
        if ct == "AC":
            ac_ids.add(net_id)
        elif ct == "DC":
            dc_ids.add(net_id)
    return {"ac": sorted(ac_ids), "dc": sorted(dc_ids)}


def calculate_fee_breakdown(
    periods: list[EVNetworkSubscription],
    range_start: date,
    range_end: date,
) -> list[dict[str, Any]]:
    """Per-period fee breakdown for the Cost Explorer subscription block.

    Returns one dict per period overlapping [range_start, range_end]:
    `{"months": int, "fee_per_month": float, "fee_total": float, "period": EVNetworkSubscription}`.
    Months are counted with the same overlap rule as `calculate_monthly_fees_in_range`:
    any calendar month that has any overlap with both the range and the period counts
    as one full month.
    """
    breakdown: list[dict[str, Any]] = []
    for p in periods:
        if p.monthly_fee is None or float(p.monthly_fee) == 0.0:
            continue
        p_start = p.start_date
        p_end = p.end_date if p.end_date is not None else range_end
        window_start = max(p_start, range_start)
        window_end = min(p_end, range_end)
        if window_start > window_end:
            continue
        months = (window_end.year - window_start.year) * 12 + (window_end.month - window_start.month) + 1
        fee = float(p.monthly_fee)
        breakdown.append({
            "months": months,
            "fee_per_month": fee,
            "fee_total": round(months * fee, 2),
            "period": p,
        })
    return breakdown


def build_cost_explorer_monthly_chart(
    monthly_trend: list[dict[str, Any]],
    *,
    reference_network_name: str | None = None,
) -> str:
    """Two-trace line chart for the Cost Explorer monthly strip — solid actual, dashed reference.

    Returns "" when `monthly_trend` is empty so the template can use a truthiness check.
    """
    if not monthly_trend:
        return ""

    x_vals = [row["month"] for row in monthly_trend]
    paid_vals = [row["paid"] for row in monthly_trend]
    ref_vals = [row["at_reference"] for row in monthly_trend]

    ref_name = f"At reference ({reference_network_name})" if reference_network_name else "At reference rate"

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=x_vals,
            y=paid_vals,
            mode="lines+markers",
            name="You paid",
            line=dict(color="#47A8E5", width=2),
            marker=dict(size=4),
            hovertemplate="<b>%{x}</b><br>You paid: $%{y:.2f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=x_vals,
            y=ref_vals,
            mode="lines",
            name=ref_name,
            line=dict(color="#94a3b8", width=1.5, dash="dash"),
            hovertemplate="<b>%{x}</b><br>" + ref_name + ": $%{y:.2f}<extra></extra>",
        )
    )
    fig.update_layout(
        height=280,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#e5e7eb",
        margin=dict(l=20, r=20, t=10, b=20),
        xaxis=dict(title=""),
        yaxis=dict(title="Cost ($)", tickprefix="$"),
        hovermode="x unified",
        hoverlabel=_HOVER_LABEL,
        legend=dict(orientation="h", yanchor="bottom", y=-0.25, xanchor="center", x=0.5),
    )
    return _wrap_chart(fig.to_html(full_html=False, include_plotlyjs=False, config=_PLOTLY_CONFIG))


async def query_cost_explorer(
    db: AsyncSession,
    *,
    time_range: str = "all",
    device_id: str | None = None,
    network_ids: list[int] | None = None,
    free_charging_what_if: bool = False,
    free_charging_scope: str = "global",
    free_charging_networks: list[int] | None = None,
    reference_rate: float = 0.0,
    reference_network_id: int | None = None,
    reference_network_name: str | None = None,
) -> dict[str, Any]:
    """Aggregate Cost Explorer card payload for the active filter set.

    Honors network multi-select, the free-charging what-if (global/per-network),
    reference-rate (resolved to a float by caller), and the subscription counterfactual.
    All currency values are `round(value, 2)` at the boundary.

    Returns dict with:
    - total_paid_actual: float — total out-of-pocket = session energy paid +
      subscription fees overlapping the active range. Includes fees so the
      headline reflects what the user actually paid, not just energy cost.
    - total_paid_energy: float — session energy only (sum of display_cost);
      use this for like-for-like comparison with estimated_total or for
      reconciling against totals_row.total_paid (which stays energy-only so
      the ledger table footer matches the per-row sum).
    - total_paid_what_if: float — total_paid_actual + delta from free-rebill
      (== total_paid_actual when what_if=False)
    - estimated_total, delta_actual_vs_estimated: float — Estimated − Energy
      (apples-to-apples, both exclude subscription fees)
    - reference_rate, reference_network_id, reference_network_name, total_at_reference,
      total_savings_vs_reference
    - subscription: dict (active, with_total, with_energy_at_member_rate, with_fees_count,
                          with_fees_per_month, with_fees_total, without_total, net_saved,
                          fee_breakdown)
    - by_network: list[dict] — one row per network in scope, keyed on network_id
    - totals_row: dict — totals across by_network. Includes `paid_per_kwh` and
      `reference_per_kwh` (None when energy/reference is zero) and a
      `reference_label` ready for the ledger tfoot sub-line (e.g.
      "Blink - $0.40/kWh" in network mode, "$0.52/kWh" in custom mode).
    - monthly_trend: list[dict] — [{month, paid, at_reference}, ...]
    - unconfigured_count: int — sessions skipped due to missing network config
    - free_charging_eligible_networks: list[dict] — networks with >=1 free session (per-net UI)
    - single_network_detail: dict | None — extra fields when len(by_network) == 1
    - total_sessions, total_kwh, total_distance, free_session_count: range counts
    - cost_per_kwh, cost_per_session, cost_per_distance_km: all-in cost ratios
      (None when the denominator is zero)
    - free_charging_saved, cost_per_free_session: free-charging strip figures

    The summary strip on the card is range-scoped, not network-filtered — the
    route calls this aggregator a second time with `network_ids=None` and reads
    the count/ratio/free-charging keys from that unfiltered result.
    """
    networks_by_id = await get_networks_by_id(db)
    subs_by_network = await get_all_subscriptions_by_network(db)

    stmt = select(EVChargingSession).options(
        load_only(
            EVChargingSession.id,
            EVChargingSession.energy_kwh,
            EVChargingSession.cost,
            EVChargingSession.cost_source,
            EVChargingSession.is_free,
            EVChargingSession.location_name,
            EVChargingSession.location_type,
            EVChargingSession.network_id,
            EVChargingSession.location_id,
            EVChargingSession.device_id,
            EVChargingSession.session_start_utc,
            EVChargingSession.estimated_cost,
            EVChargingSession.distance_added,
        )
    ).where(EVChargingSession.energy_kwh > 0)

    time_filter = build_time_filter(time_range)
    if time_filter is not None:
        stmt = stmt.where(time_filter)
    if device_id:
        stmt = stmt.where(EVChargingSession.device_id == device_id)
    # Empty list / None = all networks (treats all-deselected as "all" per Q-07).
    if network_ids:
        stmt = stmt.where(EVChargingSession.network_id.in_(network_ids))

    result = await db.execute(stmt)
    sessions = list(result.scalars().all())

    location_ids = [s.location_id for s in sessions if s.location_id]
    locations_by_id = await get_locations_by_id(db, location_ids) if location_ids else {}

    total_paid_actual = 0.0
    total_paid_what_if = 0.0
    estimated_total = 0.0
    total_at_reference = 0.0
    total_distance = 0.0
    free_session_count = 0
    free_charging_saved = 0.0
    with_energy = 0.0
    without_energy = 0.0
    unconfigured_count = 0
    by_network: dict[int, dict[str, Any]] = {}
    monthly_paid: dict[str, float] = {}
    monthly_at_ref: dict[str, float] = {}
    free_eligible_ids: set[int] = set()
    range_start_min: date | None = None
    range_end_max: date | None = None
    # Per-network subscription-energy accumulator: only counts sessions where
    # compute_session_cost flagged subscription_active. Used to drive the
    # per-subscription breakdown rendered in the aside.
    subscription_energy_by_network: dict[int, float] = {}

    per_net_scope_set = set(free_charging_networks or [])

    for s in sessions:
        network = networks_by_id.get(s.network_id) if s.network_id else None
        location = locations_by_id.get(s.location_id) if s.location_id else None
        sub_periods = subs_by_network.get(s.network_id, []) if s.network_id else []
        cost_info = compute_session_cost(s, network=network, location=location, subscription_periods=sub_periods)

        if cost_info["display_cost"] is None:
            unconfigured_count += 1
            continue

        # Range bounds drive subscription fee proration below.
        session_date = s.session_start_utc.date() if s.session_start_utc else None
        if session_date:
            range_start_min = session_date if range_start_min is None else min(range_start_min, session_date)
            range_end_max = session_date if range_end_max is None else max(range_end_max, session_date)

        # Track free-charging eligible networks for the per-network mode UI.
        if (cost_info.get("is_free") or (network and getattr(network, "is_free", False))) and s.network_id:
            free_eligible_ids.add(s.network_id)

        energy_kwh = float(s.energy_kwh or 0.0)
        paid = float(cost_info["display_cost"])
        hypothetical_at_ref = energy_kwh * reference_rate

        # Free-charging what-if: rebill free sessions at reference_rate when in scope.
        is_free_session = bool(cost_info.get("is_free") or (network and getattr(network, "is_free", False)))
        if free_charging_what_if and is_free_session:
            in_scope = (
                free_charging_scope == "global"
                or (free_charging_scope == "per_network" and s.network_id in per_net_scope_set)
            )
            paid_what_if = hypothetical_at_ref if in_scope else paid
        else:
            paid_what_if = paid

        total_paid_actual += paid
        total_paid_what_if += paid_what_if
        total_at_reference += hypothetical_at_ref
        if cost_info.get("estimated_cost") is not None:
            estimated_total += float(cost_info["estimated_cost"])

        # Distance powers the $/mile (or $/km) strip ratio. distance_added is
        # stored in km; the route converts to the user's display unit.
        if s.distance_added is not None and s.distance_added > 0:
            total_distance += float(s.distance_added)

        # Free-charging strip figures: estimated_cost on a free session is its
        # pre-cascade "would-have-cost" — the dollars saved by charging free.
        if is_free_session:
            free_session_count += 1
            if cost_info.get("estimated_cost") is not None:
                free_charging_saved += float(cost_info["estimated_cost"])

        # Subscription counterfactual: With = display_cost (already member-rate),
        # Without = non_member_cost when subscription active, else display_cost.
        with_energy += paid
        if cost_info.get("subscription_active") and cost_info.get("non_member_cost") is not None:
            without_energy += float(cost_info["non_member_cost"])
            if s.network_id:
                subscription_energy_by_network[s.network_id] = (
                    subscription_energy_by_network.get(s.network_id, 0.0) + paid
                )
        else:
            without_energy += paid

        net_id = s.network_id or 0
        if net_id not in by_network:
            by_network[net_id] = {
                "network_id": net_id,
                "network_name": network.network_name if network else "Unknown",
                "color": (network.color if network and network.color else "#6B7280"),
                "session_count": 0,
                "total_kwh": 0.0,
                "total_paid": 0.0,
                "total_at_reference": 0.0,
                "free_session_count": 0,
                "paid_session_count": 0,
            }
        row = by_network[net_id]
        row["session_count"] += 1
        row["total_kwh"] += energy_kwh
        row["total_paid"] += paid
        row["total_at_reference"] += hypothetical_at_ref
        if is_free_session:
            row["free_session_count"] += 1
        else:
            row["paid_session_count"] += 1

        if session_date:
            month_key = session_date.strftime("%Y-%m")
            monthly_paid[month_key] = monthly_paid.get(month_key, 0.0) + paid_what_if
            monthly_at_ref[month_key] = monthly_at_ref.get(month_key, 0.0) + hypothetical_at_ref

    # Finalize by_network rows.
    by_network_list: list[dict[str, Any]] = []
    for row in by_network.values():
        kwh = row["total_kwh"]
        row["effective_rate_per_kwh"] = round(row["total_paid"] / kwh, 3) if kwh > 0 else 0.0
        row["delta"] = round(row["total_paid"] - row["total_at_reference"], 2)
        row["total_kwh"] = round(kwh, 2)
        row["total_paid"] = round(row["total_paid"], 2)
        row["total_at_reference"] = round(row["total_at_reference"], 2)
        by_network_list.append(row)
    by_network_list.sort(key=lambda r: r["total_paid"], reverse=True)

    totals_kwh = round(sum(r["total_kwh"] for r in by_network_list), 2)
    # Effective $/kWh sublines for the ledger tfoot. Guarded against zero-energy
    # ranges (template renders the bare $/kWh row as "—" when None).
    totals_paid_per_kwh = (
        round(total_paid_actual / totals_kwh, 2) if totals_kwh > 0 else None
    )
    if reference_rate > 0 and totals_kwh > 0:
        totals_reference_per_kwh = round(total_at_reference / totals_kwh, 2)
    else:
        totals_reference_per_kwh = None
    # Label for the reference sub-line. Network mode prefixes with the
    # reference network name ("Blink - $0.40/kWh"); custom mode renders the
    # bare rate. None when no reference is configured.
    if reference_rate > 0:
        if reference_network_name:
            reference_label = f"{reference_network_name} - ${reference_rate:.2f}/kWh"
        else:
            reference_label = f"${reference_rate:.2f}/kWh"
    else:
        reference_label = None

    totals_row = {
        "session_count": sum(r["session_count"] for r in by_network_list),
        "total_kwh": totals_kwh,
        "total_paid": round(total_paid_actual, 2),
        "total_at_reference": round(total_at_reference, 2),
        "delta": round(total_paid_actual - total_at_reference, 2),
        "paid_per_kwh": totals_paid_per_kwh,
        "reference_per_kwh": totals_reference_per_kwh,
        "reference_label": reference_label,
    }

    # Subscription block: collect every period overlapping the session range.
    # When a network filter is active, only include periods on those networks —
    # the block then reflects the scoped view rather than global totals.
    scope_network_ids: set[int] = set(network_ids) if network_ids else set()
    all_periods_in_range: list[EVNetworkSubscription] = []
    for sub_network_id, periods in subs_by_network.items():
        if scope_network_ids and sub_network_id not in scope_network_ids:
            continue
        for p in periods:
            if range_start_min is None or range_end_max is None:
                continue
            p_start = p.start_date
            p_end = p.end_date if p.end_date is not None else range_end_max
            if p_start <= range_end_max and p_end >= range_start_min:
                all_periods_in_range.append(p)

    # Surface the scoped-network label so the template can label or hide the
    # block when no subscription applies to the active network filter.
    scoped_network_name: str | None = None
    if scope_network_ids:
        names = [
            networks_by_id[nid].network_name
            for nid in scope_network_ids
            if nid in networks_by_id
        ]
        scoped_network_name = ", ".join(names) if names else None

    if all_periods_in_range and range_start_min and range_end_max:
        fee_breakdown = calculate_fee_breakdown(all_periods_in_range, range_start_min, range_end_max)
        with_fees_total = round(sum(b["fee_total"] for b in fee_breakdown), 2)
        with_fees_count = sum(b["months"] for b in fee_breakdown)
        # Headline "Fees · N × $Y" picks the most-recent period's monthly_fee; templates
        # that want a full per-period rendering can fall back to fee_breakdown.
        with_fees_per_month = fee_breakdown[-1]["fee_per_month"] if fee_breakdown else 0.0
        with_energy_only = round(with_energy, 2)
        with_total = round(with_energy_only + with_fees_total, 2)
        without_total = round(without_energy, 2)
        net_saved = round(without_total - with_total, 2)

        # Per-subscription rows for the aside breakdown: one entry per network
        # with a fee_breakdown contribution. Energy comes from
        # subscription_energy_by_network (sessions where subscription_active),
        # fees come from the fee_breakdown bucketed by period.network_id.
        per_network_fees: dict[int, dict[str, Any]] = {}
        for b in fee_breakdown:
            nid = b["period"].network_id
            if nid not in per_network_fees:
                per_network_fees[nid] = {
                    "fee_total": 0.0,
                    "months": 0,
                    "fee_per_month": float(b["fee_per_month"]),
                }
            per_network_fees[nid]["fee_total"] += float(b["fee_total"])
            per_network_fees[nid]["months"] += int(b["months"])
            # Carry the most-recent period's monthly fee as the headline rate.
            per_network_fees[nid]["fee_per_month"] = float(b["fee_per_month"])

        subscription_network_ids = sorted(
            set(per_network_fees.keys()) | set(subscription_energy_by_network.keys())
        )
        per_subscription: list[dict[str, Any]] = []
        for nid in subscription_network_ids:
            net = networks_by_id.get(nid)
            fees_entry = per_network_fees.get(nid, {"fee_total": 0.0, "months": 0, "fee_per_month": 0.0})
            per_subscription.append({
                "network_id": nid,
                "network_name": net.network_name if net else "Unknown",
                "energy_at_member_rate": round(subscription_energy_by_network.get(nid, 0.0), 2),
                "member_fee": round(fees_entry["fee_total"], 2),
                "fee_months": fees_entry["months"],
                "fee_per_month": round(fees_entry["fee_per_month"], 2),
            })
        # Stable display order: highest energy first, then alphabetical.
        per_subscription.sort(key=lambda r: (-r["energy_at_member_rate"], r["network_name"]))

        subscription_block: dict[str, Any] = {
            "active": True,
            "with_total": with_total,
            "with_energy_at_member_rate": with_energy_only,
            "with_fees_count": with_fees_count,
            "with_fees_per_month": round(with_fees_per_month, 2),
            "with_fees_total": with_fees_total,
            "without_total": without_total,
            "net_saved": net_saved,
            "fee_breakdown": fee_breakdown,
            "subscriptions": per_subscription,
            "scoped_network_name": scoped_network_name,
        }
    else:
        subscription_block = {"active": False, "scoped_network_name": scoped_network_name, "subscriptions": []}

    all_months = sorted(set(monthly_paid.keys()) | set(monthly_at_ref.keys()))
    monthly_trend = [
        {
            "month": m,
            "paid": round(monthly_paid.get(m, 0.0), 2),
            "at_reference": round(monthly_at_ref.get(m, 0.0), 2),
        }
        for m in all_months
    ]

    # Single-network detail surfaces extra fields only when one network is in scope.
    single_network_detail: dict[str, Any] | None = None
    if len(by_network_list) == 1:
        row = by_network_list[0]
        single_network_detail = {
            "network_id": row["network_id"],
            "network_name": row["network_name"],
            "effective_rate_per_kwh": row["effective_rate_per_kwh"],
            "free_session_count": row["free_session_count"],
            "paid_session_count": row["paid_session_count"],
            "free_charging_delta": round(
                row["total_at_reference"] - row["total_paid"] if row["free_session_count"] > 0 else 0.0,
                2,
            ),
        }

    free_charging_eligible_networks = [
        {"network_id": nid, "network_name": networks_by_id[nid].network_name}
        for nid in sorted(free_eligible_ids)
        if nid in networks_by_id
    ]

    # Subscription fees the user actually paid out-of-pocket in this range.
    # When a subscription is active, these are real recurring charges separate
    # from per-session energy cost — the headline must reflect them so
    # "you paid" matches what's left the user's wallet.
    total_fees_paid = float(subscription_block.get("with_fees_total", 0.0)) if subscription_block.get("active") else 0.0
    total_paid_energy = total_paid_actual
    total_paid_with_fees = total_paid_actual + total_fees_paid
    total_paid_what_if_with_fees = total_paid_what_if + total_fees_paid

    # Cost ratios for the summary strip — divide the all-in total (energy +
    # fees) by each denominator. None when the denominator is zero so the
    # template renders "—" rather than NaN. $/mile divides by raw km distance;
    # the route converts the figure to the user's display unit.
    total_sessions = totals_row["session_count"]
    cost_per_kwh = round(total_paid_with_fees / totals_kwh, 3) if totals_kwh > 0 else None
    cost_per_session = round(total_paid_with_fees / total_sessions, 2) if total_sessions > 0 else None
    cost_per_distance_km = (
        round(total_paid_with_fees / total_distance, 4) if total_distance > 0 else None
    )
    # Average estimated cost of a free session had it been billed.
    cost_per_free_session = (
        round(free_charging_saved / free_session_count, 2) if free_session_count > 0 else None
    )

    return {
        # Headline value: energy + subscription fees in range.
        "total_paid_actual": round(total_paid_with_fees, 2),
        # Energy-only sum — keeps the ledger table footer ↔ row arithmetic
        # consistent and powers the "Energy $X · Fees $Y" breakdown sub-line.
        "total_paid_energy": round(total_paid_energy, 2),
        "total_fees_paid": round(total_fees_paid, 2),
        "total_paid_what_if": round(total_paid_what_if_with_fees, 2),
        "estimated_total": round(estimated_total, 2),
        # Estimated is energy-only; compare like-for-like so the delta is meaningful.
        "delta_actual_vs_estimated": round(estimated_total - total_paid_energy, 2),
        # Summary-strip figures — counts, distance, ratios, and free-charging
        # estimates. The strip is range-scoped: the route calls this aggregator
        # a second time without the network filter to populate it.
        "total_sessions": total_sessions,
        "total_kwh": totals_kwh,
        "total_distance": round(total_distance, 2),
        "free_session_count": free_session_count,
        "free_charging_saved": round(free_charging_saved, 2),
        "cost_per_kwh": cost_per_kwh,
        "cost_per_session": cost_per_session,
        "cost_per_distance_km": cost_per_distance_km,
        "cost_per_free_session": cost_per_free_session,
        "reference_rate": reference_rate,
        "reference_network_id": reference_network_id,
        "reference_network_name": reference_network_name,
        "total_at_reference": round(total_at_reference, 2),
        "total_savings_vs_reference": round(total_at_reference - total_paid_energy, 2),
        "subscription": subscription_block,
        "by_network": by_network_list,
        "totals_row": totals_row,
        "monthly_trend": monthly_trend,
        "unconfigured_count": unconfigured_count,
        "free_charging_eligible_networks": free_charging_eligible_networks,
        "single_network_detail": single_network_detail,
    }
