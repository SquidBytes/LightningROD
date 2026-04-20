"""Tests for web/tooltips.py — single source of truth for tooltip copy.
Covers:
- locked Avg Efficiency copy is preserved verbatim.
- Every tooltip is one sentence and ≤15 words.
- Slug convention is `<page>_<metric>` with known page prefixes.
- Tooltips dict is registered as a Jinja global on every route templates env.
"""

from __future__ import annotations

from web.tooltips import TOOLTIPS


# Phase 26-04 locked copy — MUST be verbatim.
LOCKED_AVG_EFFICIENCY = (
    "Arithmetic mean of per-session mi/kWh, not total distance divided by total energy."
)

ALLOWED_PAGE_PREFIXES = {"performance", "costs", "battery", "driving", "home"}


def test_avg_efficiency_copy_locked():
    """locked Avg Efficiency tooltip is present verbatim."""
    assert TOOLTIPS["performance_avg_efficiency"] == LOCKED_AVG_EFFICIENCY


def test_every_tooltip_is_one_sentence_under_15_words():
    """Every tooltip is ≤15 whitespace-separated tokens, ends with . ! or ?."""
    for slug, copy in TOOLTIPS.items():
        words = copy.split()
        assert len(words) <= 15, f"{slug!r} has {len(words)} words: {copy!r}"
        stripped = copy.rstrip()
        assert stripped.endswith((".", "!", "?")), (
            f"{slug!r} does not end with sentence terminator: {copy!r}"
        )


def test_slug_convention_page_underscore_metric():
    """Slugs follow `<page>_<metric>` — prefix is a known page group."""
    for slug in TOOLTIPS.keys():
        parts = slug.split("_", 1)
        assert len(parts) == 2, f"slug {slug!r} missing `_metric` suffix"
        prefix, suffix = parts
        assert prefix in ALLOWED_PAGE_PREFIXES, (
            f"slug {slug!r} has unknown page prefix {prefix!r}; "
            f"allowed: {sorted(ALLOWED_PAGE_PREFIXES)}"
        )
        assert suffix, f"slug {slug!r} has empty metric suffix"


def test_seed_slugs_present():
    """At least the six seed slugs required by scope exist."""
    required = {
        "performance_avg_efficiency",
        "performance_total_energy",
        "costs_avg_per_session",
        "costs_cost_per_mile",
        "costs_cost_per_kwh",
        "costs_actual_vs_estimated",
    }
    missing = required - set(TOOLTIPS.keys())
    assert not missing, f"missing seed slugs: {missing}"


def test_tooltips_registered_as_jinja_global():
    """After app import, every route module's templates env exposes `tooltips`."""
    # Importing the app triggers create_app(), which registers globals on each
    # route module's `templates.env`.
    from web.main import app  # noqa: F401 — import is the registration trigger

    from web.routes import battery, costs, performance

    for module in (costs, performance, battery):
        env = module.templates.env
        assert "tooltips" in env.globals, (
            f"{module.__name__} templates env missing `tooltips` global"
        )
        assert env.globals["tooltips"] is TOOLTIPS, (
            f"{module.__name__} templates env `tooltips` is not the TOOLTIPS dict"
        )
