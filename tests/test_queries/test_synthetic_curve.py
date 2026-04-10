"""Query-layer tests for synthetic charge curve aggregation. phase_25 Wave 0 stubs."""
import pytest

pytestmark = pytest.mark.query


async def test_phase_25_dc_peak_aggregation_median(db_session):
    """query_synthetic_curve_inputs returns median DC peak kW across DC sessions in range."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 2 synthetic-curve task")


async def test_phase_25_fallback_trigger_hides_when_real_data_present(db_session):
    """When any DC session in window has >=3 battery_status points, synthetic is NOT shown."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 2 synthetic-curve task")


async def test_phase_25_fallback_trigger_shows_when_no_real_data(db_session):
    """When no DC session has >=3 battery_status points, synthetic IS shown."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 2 synthetic-curve task")


async def test_phase_25_dc_only_excludes_ac_sessions(db_session):
    """query_synthetic_curve_inputs excludes charge_type='AC' rows entirely."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 2 synthetic-curve task")


async def test_phase_25_respects_range_window(db_session):
    """Aggregation includes only DC sessions with start_time inside the ?range= window."""
    pytest.skip("phase_25 Wave 0 stub — implement in Wave 2 synthetic-curve task")
