"""The Moon's row in the Planets tab.

External cross-check
--------------------
Principal phase times verified 2026-09-20 against the US Naval Observatory's
Astronomical Applications API (aa.usno.navy.mil/api/moon/phases), which gave,
in UTC: First Quarter 18 Sep 20:44, Full Moon 26 Sep 16:49, Last Quarter
3 Oct 13:25, New Moon 10 Oct 15:50. PUBLISHED_PHASES below comes from that
response, not from this codebase.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from conftest import requires_ephemeris
from engine.ephem import night_window
from engine.planets import (MOON_MAGNITUDE_MAX_PHASE_DEG, moon_magnitude,
                            report_moon)
from engine.reference import MOON_FACTS
from engine.timeutil import UTC, is_aware_utc

# (night, expected next phase, its published UTC time). Source: USNO, above.
PUBLISHED_PHASES = [
    (date(2026, 9, 20), "Full Moon", datetime(2026, 9, 26, 16, 49, tzinfo=UTC)),
    (date(2026, 9, 27), "Last Quarter", datetime(2026, 10, 3, 13, 25, tzinfo=UTC)),
    (date(2026, 10, 4), "New Moon", datetime(2026, 10, 10, 15, 50, tzinfo=UTC)),
]


@requires_ephemeris
@pytest.mark.parametrize("night, name, published", PUBLISHED_PHASES)
def test_next_phase_matches_the_published_time(home, night, name, published):
    report = report_moon(home, night_window(night, home))
    assert report.next_phase_name == name
    # USNO publishes to the minute.
    assert abs((report.next_phase_utc - published).total_seconds()) < 90


@requires_ephemeris
def test_illumination_is_the_dashboards_number(home):
    """The row and the night summary must never disagree about the Moon."""
    window = night_window(date(2026, 9, 20), home)
    report = report_moon(home, window)
    assert report.illuminated_fraction == window.moon_illumination
    assert report.waxing == window.moon_waxing


@requires_ephemeris
def test_age_counts_from_the_last_new_moon(home):
    # New Moon was 11 Sep 03:27 UTC (USNO), so a Moon on the night of the
    # 20th is a little over nine days old -- ten by the night's midpoint.
    report = report_moon(home, night_window(date(2026, 9, 20), home))
    assert 9.5 < report.age_days < 10.8


@requires_ephemeris
def test_full_moon_is_bright_large_and_near_opposition(home):
    report = report_moon(home, night_window(date(2026, 9, 26), home))
    assert report.illuminated_fraction > 0.97
    assert report.phase_angle_deg < 15
    assert report.elongation_deg > 165
    assert report.magnitude is not None and -12.9 < report.magnitude < -12.3
    # The Moon is 29.3' to 34.1' across between apogee and perigee.
    assert 29.0 * 60 < report.apparent_diameter_arcsec < 34.5 * 60


@requires_ephemeris
def test_a_new_moon_is_not_observable_and_has_no_magnitude(home):
    report = report_moon(home, night_window(date(2026, 10, 10), home))
    assert not report.observable
    assert report.magnitude is None
    assert any("New Moon" in note for note in report.notes)


@requires_ephemeris
def test_moon_datetimes_are_aware_utc(home):
    report = report_moon(home, night_window(date(2026, 9, 20), home))
    for value in (report.peak_time_utc, report.next_phase_utc,
                  report.moonrise_utc, report.moonset_utc):
        if value is not None:
            assert is_aware_utc(value)


def test_magnitude_formula_reproduces_allens_anchor_points():
    # At mean distance the formula is Allen's fit exactly: -12.73 at full.
    full = moon_magnitude(0.0, 384_400.0)
    quarter = moon_magnitude(90.0, 384_400.0)
    assert full == pytest.approx(-12.73)
    # A quarter Moon is half lit but only about a tenth as bright as a full
    # one -- the opposition surge -- which is ~2.6 magnitudes, not the 0.75
    # that halving the lit area alone would give.
    assert quarter - full == pytest.approx(2.6, abs=0.1)
    # Farther away is fainter.
    assert moon_magnitude(0.0, 405_000.0) > moon_magnitude(0.0, 363_000.0)


def test_magnitude_is_withheld_where_the_fit_does_not_reach():
    assert moon_magnitude(MOON_MAGNITUDE_MAX_PHASE_DEG + 1, 384_400.0) is None


def test_the_synodic_month_is_longer_than_the_sidereal():
    assert MOON_FACTS.synodic_month_days > MOON_FACTS.sidereal_month_days
