"""Per-card chart-builder empty-input behavior.

All four card-chart builders share the convention that empty input collapses
to "" so the template can branch on truthiness — never wraps an empty Plotly
container in a `.plotly-chart-wrap` div.
"""

import pytest

from web.queries.energy import (
    build_charging_speed_chart,
    build_efficiency_over_time_chart,
    build_energy_over_time_chart,
    build_range_regen_over_time_chart,
)

pytestmark = [pytest.mark.query]


def test_card_chart_builders_return_empty_on_empty_input():
    assert build_charging_speed_chart([]) == ""
    assert build_energy_over_time_chart([]) == ""
    assert build_efficiency_over_time_chart([]) == ""
    assert build_range_regen_over_time_chart(None) == ""
    assert build_range_regen_over_time_chart([]) == ""
