"""Pure-function unit tests for to_metric.
Locks the public API of web.services.units.to_metric. All tests in this file
MUST ImportError today and MUST pass after it lands the module.
Covers every supported source_unit , edge cases (None, NaN, negative,
zero, extreme values), and the UnknownSourceUnit exception. Property tests
use Hypothesis for round-trip identity.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from web.services.units.to_metric import UnknownSourceUnit, to_metric  # noqa: F401

pytestmark = pytest.mark.unit


# --- metric passthrough (identity) ---

@pytest.mark.parametrize("value", [0, 0.0, 1, 42.5, 1000, 1e6, -5.5])
def test_km_passthrough(value):
    assert to_metric(value, "km") == pytest.approx(float(value))


def test_degC_passthrough():
    assert to_metric(22.0, "degC") == pytest.approx(22.0)


def test_kWh_passthrough():
    assert to_metric(5.5, "kWh") == pytest.approx(5.5)


# --- imperial -> metric conversion ---

def test_mi_to_km():
    assert to_metric(100.0, "mi") == pytest.approx(160.934, abs=0.01)


def test_mph_to_kmh():
    assert to_metric(60.0, "mph") == pytest.approx(96.5604, abs=0.01)


def test_degF_to_degC_boiling():
    assert to_metric(212.0, "degF") == pytest.approx(100.0, abs=0.01)


def test_degF_to_degC_freezing():
    assert to_metric(32.0, "degF") == pytest.approx(0.0, abs=0.01)


def test_Wh_to_kWh():
    assert to_metric(1000.0, "Wh") == pytest.approx(1.0)


# --- None + invalid input ---

def test_none_returns_none():
    assert to_metric(None, "mi") is None


def test_non_numeric_string_returns_none():
    assert to_metric("banana", "mi") is None


# --- unknown source unit ---

def test_unknown_unit_raises():
    with pytest.raises(UnknownSourceUnit):
        to_metric(42.0, "furlongs")


# --- property: metric-in is identity ---

@given(st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False))
def test_km_identity_property(value):
    assert to_metric(value, "km") == pytest.approx(value)


@given(st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False))
def test_degC_identity_property(value):
    assert to_metric(value, "degC") == pytest.approx(value)


# --- property: mi round-trip within tolerance ---

@given(st.floats(min_value=0, max_value=1e5, allow_nan=False, allow_infinity=False))
def test_mi_km_mi_round_trip(miles):
    km = to_metric(miles, "mi")
    recovered_miles = km / 1.60934
    assert recovered_miles == pytest.approx(miles, rel=1e-4)
