"""Query-layer tests for /driving/performance data functions. phase_25 Wave 0 stubs."""
import pytest

pytestmark = pytest.mark.query


async def test_phase_25_temp_correlation_filters_null_ambient_temp(db_session):
    """query_temperature_correlation excludes trips where ambient_temp IS NULL."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 2 temperature scatter task")


async def test_phase_25_temp_correlation_respects_range_filter(db_session):
    """query_temperature_correlation applies the ?range= window."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 2 temperature scatter task")


async def test_phase_25_temp_correlation_derives_efficiency(db_session):
    """Returned rows include distance + energy_consumed so the chart builder can derive mi/kWh."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 2 temperature scatter task")


async def test_phase_25_regen_per_trip_returns_row_per_trip(db_session):
    """query_regen_per_trip returns one row per trip with non-null range_regenerated."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 2 regen bar task")


async def test_phase_25_regen_per_trip_pct_calculation(db_session):
    """Returned regen_pct matches regen_kwh / gross_kwh * 100 for known fixture trips."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 2 regen bar task")


async def test_phase_25_regen_kwh_derivation_from_range(db_session):
    """regen_kwh is derived as range_regenerated / (distance / energy_consumed) per trip."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 2 regen bar task")
