"""Coaching-score radar degrades gracefully with partial scores."""

from types import SimpleNamespace

from web.queries.trips import build_trip_score_radar


def _trip(**scores):
    base = dict(charge_regenerated=None, speed_score=None,
                acceleration_score=None, deceleration_score=None)
    base.update(scores)
    return SimpleNamespace(**base)


def test_three_coaching_scores_render_radar():
    html = build_trip_score_radar(
        _trip(charge_regenerated=85, speed_score=60, acceleration_score=75, deceleration_score=70)
    )
    assert "scatterpolar" in html.lower()


def test_charge_regenerated_is_not_a_radar_axis():
    html = build_trip_score_radar(
        _trip(charge_regenerated=85, speed_score=60, acceleration_score=75, deceleration_score=70)
    )
    assert "Overall" not in html
    assert "Charge regenerated" not in html


def test_without_coaching_scores_shows_charge_regenerated_stat():
    html = build_trip_score_radar(_trip(charge_regenerated=85))
    assert "radial-progress" in html
    assert "85 %" in html
    assert "Charge regenerated" in html
    assert "scatterpolar" not in html.lower()


def test_zero_scores_treated_as_unmeasured():
    html = build_trip_score_radar(
        _trip(charge_regenerated=85, speed_score=0, acceleration_score=0, deceleration_score=0)
    )
    assert "radial-progress" in html


def test_single_coaching_score_without_regen_shows_that_score():
    html = build_trip_score_radar(_trip(speed_score=60))
    assert "Speed score" in html
    assert ">60<" in html


def test_no_scores_returns_empty():
    assert build_trip_score_radar(_trip()) == ""
