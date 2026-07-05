"""Driving-score radar degrades gracefully with partial scores."""

from types import SimpleNamespace

from web.queries.trips import build_driving_score_radar


def _trip(**scores):
    base = dict(driving_score=None, speed_score=None,
                acceleration_score=None, deceleration_score=None)
    base.update(scores)
    return SimpleNamespace(**base)


def test_all_scores_render_radar():
    html = build_driving_score_radar(
        _trip(driving_score=85, speed_score=60, acceleration_score=75, deceleration_score=70)
    )
    assert "Scatterpolar" in html or "scatterpolar" in html


def test_only_overall_renders_radial_stat_not_radar():
    """Metrics-backfilled trips carry only the overall score — a radar with
    three zero spokes reads as broken; a compact radial stat replaces it."""
    html = build_driving_score_radar(_trip(driving_score=85))
    assert "radial-progress" in html
    assert "85" in html
    assert "scatterpolar" not in html.lower()


def test_zero_scores_treated_as_unmeasured():
    html = build_driving_score_radar(
        _trip(driving_score=85, speed_score=0, acceleration_score=0, deceleration_score=0)
    )
    assert "radial-progress" in html


def test_no_scores_returns_empty():
    assert build_driving_score_radar(_trip()) == ""
