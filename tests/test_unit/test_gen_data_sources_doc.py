"""Pure-function tests for the data-sources doc generator."""

import pytest

from scripts.gen_data_sources_doc import render_markdown
from web.services.units.contracts import FieldContract, SourceLocator, SourceLocatorKind

pytestmark = pytest.mark.unit


def _sample_groups():
    return [
        ("ha_fordpass", [
            FieldContract(
                source_locator=SourceLocator("sensor.fordpass_{vin}_metrics", SourceLocatorKind.HA_ENTITY_ID),
                source_attribute="xevBatteryRange",
                source_unit="km",
                target_db_table="ev_battery_status",
                target_db_column="hv_battery_range",
                target_unit="km",
                notes="D-B1 canonical source",
            ),
        ]),
    ]


def test_render_markdown_deterministic():
    """Identical input must produce identical output (byte-for-byte)."""
    g = _sample_groups()
    assert render_markdown(g) == render_markdown(g)


def test_render_markdown_sorted_by_entity_attribute():
    """Rows within a group are sorted by (entity_pattern, attribute)."""
    sl = SourceLocator("a", SourceLocatorKind.HA_ENTITY_ID)
    groups = [
        ("z_source", [
            FieldContract(sl, "b2", "km", "t", "c", "km"),
            FieldContract(sl, "b1", "km", "t", "c", "km"),
        ]),
    ]
    out = render_markdown(groups)
    i1 = out.index("`b1`")
    i2 = out.index("`b2`")
    assert i1 < i2


def test_render_markdown_contains_auto_generated_marker():
    assert "AUTO-GENERATED" in render_markdown(_sample_groups())


def test_render_markdown_ends_with_single_newline():
    out = render_markdown(_sample_groups())
    assert out.endswith("\n")
    assert not out.endswith("\n\n")


def test_render_markdown_escapes_pipe_in_notes():
    sl = SourceLocator("a", SourceLocatorKind.HA_ENTITY_ID)
    g = [("x", [FieldContract(sl, "b", "km", "t", "c", "km", "pipes|and|more")])]
    out = render_markdown(g)
    # Raw pipes would break the markdown table; they must be escaped.
    assert "pipes\\|and\\|more" in out


def test_committed_doc_in_sync_with_registry():
    """docs/data-sources.md must match what the live registries generate."""
    from scripts.gen_data_sources_doc import _OUTPUT_PATH, discover_adapters

    assert _OUTPUT_PATH.read_text() == render_markdown(discover_adapters()), (
        "docs/data-sources.md is stale — run "
        "`uv run python scripts/gen_data_sources_doc.py` and commit the result"
    )
