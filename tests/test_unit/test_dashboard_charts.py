"""Dashboard network-chart bucketing — many-CPO readability."""

from types import SimpleNamespace
from datetime import UTC, datetime

from web.queries.dashboard import (
    _MAX_CHART_NETWORKS,
    _bucket_minor_networks,
    build_energy_by_network_chart,
    build_monthly_energy_by_network_chart,
)


def _totals(n: int) -> dict[str, float]:
    # Network 1 is largest, descending
    return {f"Net{i}": float(100 - i) for i in range(1, n + 1)}


def test_bucket_keeps_all_when_under_limit():
    totals = _totals(5)
    top, other = _bucket_minor_networks(totals)
    assert top == [f"Net{i}" for i in range(1, 6)]
    assert other == 0


def test_bucket_folds_minor_networks():
    totals = _totals(10)
    top, other = _bucket_minor_networks(totals)
    assert len(top) == _MAX_CHART_NETWORKS
    assert top[0] == "Net1"
    assert other == sum(totals[f"Net{i}"] for i in range(_MAX_CHART_NETWORKS + 1, 11))


def test_donut_many_networks_renders_other_bucket():
    by_network = [
        {"network": f"Net{i}", "total_kwh": float(100 - i)} for i in range(1, 10)
    ]
    html = build_energy_by_network_chart(by_network)
    assert "Other" in html
    assert "Net9" not in html


def test_monthly_chart_many_networks_renders_other_bucket():
    sessions = [
        SimpleNamespace(
            session_start_utc=datetime(2026, 1 + (i % 3), 1, tzinfo=UTC),
            energy_kwh=float(100 - i),
            network_id=i,
        )
        for i in range(1, 10)
    ]
    id_to_name = {i: f"Net{i}" for i in range(1, 10)}
    html = build_monthly_energy_by_network_chart(sessions, id_to_name)
    assert "Other" in html
    assert "Net9" not in html
