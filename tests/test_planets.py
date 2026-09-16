"""Planet apparitions, oppositions, elongations and next-visibility search.

External cross-check
--------------------
Opposition and greatest-elongation dates verified 2026-08-25 against
published sources — EarthSky ("Saturn at opposition - brightest for 2026 - on
October 4", earthsky.org) and In-The-Sky.org's opposition listings. The dates
in PUBLISHED_EVENTS below come from those sources, not from this codebase.

Saturn's ring tilt is checked differently: rather than a published table, the
test asserts the tilt passes through zero in March 2025, which is the actual
ring-plane crossing and an unmistakable physical landmark.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from conftest import requires_ephemeris
from engine.ephem import load_ephemeris, night_window
from engine.planets import (
    ALL_PLANETS,
    INNER_PLANETS,
    TOO_NEAR_SUN_DEG,
    apparent_diameter_arcsec,
    next_apparition_event,
    next_visibility,
    observing_span,
    report_all,
    report_planet,
    saturn_ring_tilt_deg,
)
from engine.timeutil import is_aware_utc

# --- externally published values --------------------------------------------

# (planet, event label, published date). Sources in the module docstring.
PUBLISHED_EVENTS = [
    ("saturn", "opposition", date(2026, 10, 4)),
    ("uranus", "opposition", date(2026, 11, 25)),
    ("jupiter", "opposition", date(2027, 2, 11)),
    ("mars", "opposition", date(2027, 2, 19)),
]

# Published opposition dates are quoted to the day and differ by a few hours
# depending on whether apparent or geometric opposition is meant.
EVENT_TOLERANCE_DAYS = 1


@pytest.fixture(scope="module")
def reference_planets(home):
    return report_all(home, night_window(date(2026, 9, 15), home), find_next=False)


# --- external cross-check ---------------------------------------------------

@requires_ephemeris
@pytest.mark.parametrize("planet, label, published", PUBLISHED_EVENTS)
def test_apparition_events_match_published_dates(planet, label, published):
    eph = load_ephemeris()
    event = next_apparition_event(eph, planet, date(2026, 9, 15))

    assert event is not None, f"no {label} found for {planet}"
    found_label, found_date, _ = event
    assert found_label == label
    delta = abs((found_date - published).days)
    assert delta <= EVENT_TOLERANCE_DAYS, (
        f"{planet} {label}: computed {found_date}, published {published}"
    )


@requires_ephemeris
def test_opposition_means_the_planet_is_opposite_the_sun():
    """An outer planet at opposition sits near 180 deg elongation."""
    eph = load_ephemeris()
    for planet in ("saturn", "jupiter", "mars", "uranus", "neptune"):
        _, _, elongation = next_apparition_event(eph, planet, date(2026, 9, 15))
        assert elongation > 170.0, f"{planet} opposition elongation {elongation}"


@requires_ephemeris
def test_inner_planet_elongations_stay_inside_their_physical_limits():
    """Venus maxes near 47 deg, Mercury near 28 — a hard orbital constraint."""
    eph = load_ephemeris()

    _, _, venus = next_apparition_event(eph, "venus", date(2026, 9, 15))
    _, _, mercury = next_apparition_event(eph, "mercury", date(2026, 9, 15))

    assert 40.0 < venus < 48.0
    assert 17.0 < mercury < 29.0


# --- Saturn's rings ---------------------------------------------------------

@requires_ephemeris
def test_saturn_rings_are_edge_on_in_early_2025():
    """The March 2025 ring-plane crossing is the physical landmark here."""
    eph = load_ephemeris()
    tilt = saturn_ring_tilt_deg(eph, datetime(2025, 3, 23, tzinfo=timezone.utc))
    assert abs(tilt) < 1.0, f"rings should be near edge-on, got {tilt:.2f} deg"


@requires_ephemeris
def test_saturn_ring_tilt_opens_steadily_after_the_crossing():
    eph = load_ephemeris()
    tilts = [
        saturn_ring_tilt_deg(eph, datetime(year, 9, 15, tzinfo=timezone.utc))
        for year in (2025, 2026, 2027, 2028)
    ]
    assert tilts == sorted(tilts), f"tilt should grow year on year: {tilts}"
    assert tilts[-1] > 15.0


@requires_ephemeris
def test_only_saturn_reports_a_ring_tilt(reference_planets):
    for report in reference_planets:
        if report.name == "saturn":
            assert report.ring_tilt_deg is not None
        else:
            assert report.ring_tilt_deg is None


# --- apparent diameter ------------------------------------------------------

def test_apparent_diameter_shrinks_with_distance():
    near = apparent_diameter_arcsec("jupiter", 4.0)
    far = apparent_diameter_arcsec("jupiter", 6.0)
    assert near > far


def test_jupiter_apparent_diameter_is_in_the_known_range():
    """Jupiter runs roughly 30-50 arcsec across its apparition."""
    at_opposition = apparent_diameter_arcsec("jupiter", 4.0)
    assert 40.0 < at_opposition < 52.0


def test_saturn_globe_is_smaller_than_jupiter_at_similar_distance():
    assert apparent_diameter_arcsec("saturn", 9.0) < apparent_diameter_arcsec("jupiter", 9.0)


def test_apparent_diameter_of_an_unknown_body_is_none():
    assert apparent_diameter_arcsec("pluto", 30.0) is None


# --- observing span: the inner/outer distinction ----------------------------

@requires_ephemeris
def test_inner_planets_are_assessed_across_twilight(home):
    """PLAN.md 3.4 says astronomical night; that is wrong for Mercury/Venus.

    An inner planet's elongation caps how far from the Sun it can get, so it
    almost never clears a sensible floor with the Sun 18 deg down. Assessing
    Venus only during astronomical night reports the brightest planet in the
    sky as permanently invisible.
    """
    window = night_window(date(2026, 9, 15), home)

    venus_span = observing_span("venus", window)
    saturn_span = observing_span("saturn", window)

    assert venus_span == (window.sunset_utc, window.sunrise_utc)
    assert saturn_span == (window.astronomical_night[0][0],
                           window.astronomical_night[-1][1])
    # The twilight span is strictly wider.
    assert venus_span[0] < saturn_span[0]
    assert venus_span[1] > saturn_span[1]


@requires_ephemeris
def test_venus_has_a_return_date_rather_than_never(home):
    """The regression this guards: Venus reported "no return within a year"."""
    visibility = next_visibility("venus", home, date(2026, 9, 15))
    assert visibility.when is not None, "Venus must become observable"
    assert visibility.when > date(2026, 9, 15)


@requires_ephemeris
def test_mercury_reports_its_best_altitude_when_the_floor_is_unreachable(home):
    """Mercury cannot clear 25 deg from 34 N, so say what it *does* reach."""
    visibility = next_visibility("mercury", home, date(2026, 9, 15))

    if visibility.when is None:
        assert visibility.floor_unreachable
        assert visibility.best_altitude_deg > 5.0
        assert visibility.best_date is not None


@requires_ephemeris
def test_a_low_floor_makes_mercury_observable(home):
    """Sanity: the constraint is the floor, not a broken computation."""
    visibility = next_visibility("mercury", home, date(2026, 9, 15),
                                 min_altitude_deg=5.0)
    assert visibility.when is not None


# --- reports ----------------------------------------------------------------

@requires_ephemeris
def test_every_planet_is_reported(reference_planets):
    assert {r.name for r in reference_planets} == set(ALL_PLANETS)


@requires_ephemeris
def test_observable_planets_are_listed_first(reference_planets):
    observable = [r.observable for r in reference_planets]
    assert observable == sorted(observable, reverse=True)


@requires_ephemeris
def test_saturn_is_near_opposition_in_september_2026(reference_planets):
    saturn = next(r for r in reference_planets if r.name == "saturn")
    assert saturn.observable is True
    assert "opposition" in saturn.trend
    assert saturn.event_date == date(2026, 10, 4)
    assert 0 < saturn.days_to_event <= 30


@requires_ephemeris
def test_magnitudes_are_physically_sensible(reference_planets):
    by_name = {r.name: r for r in reference_planets}
    assert by_name["venus"].magnitude < -3.0        # always brilliant
    assert by_name["jupiter"].magnitude < 0.0
    assert by_name["neptune"].magnitude > 7.0       # never naked-eye
    assert by_name["uranus"].magnitude > 5.0


@requires_ephemeris
def test_illuminated_fraction_is_a_fraction(reference_planets):
    for report in reference_planets:
        assert 0.0 <= report.illuminated_fraction <= 1.0


@requires_ephemeris
def test_outer_planets_are_always_nearly_fully_lit(reference_planets):
    """Geometry: we never see much of an outer planet's night side."""
    for report in reference_planets:
        if report.name in {"jupiter", "saturn", "uranus", "neptune"}:
            assert report.illuminated_fraction > 0.95


@requires_ephemeris
def test_report_datetimes_are_aware_utc(reference_planets):
    for report in reference_planets:
        if report.peak_time_utc is not None:
            assert is_aware_utc(report.peak_time_utc)


@requires_ephemeris
def test_a_planet_near_the_sun_is_flagged_and_not_observable(home):
    """Elongation under 15 deg means lost in twilight whatever the altitude."""
    # Scan a year for a date when Mercury is at conjunction.
    from datetime import timedelta

    for offset in range(0, 365, 10):
        window = night_window(date(2026, 9, 15) + timedelta(days=offset), home)
        report = report_planet("mercury", home, window, find_next=False)
        if report.elongation_deg is not None and report.elongation_deg < TOO_NEAR_SUN_DEG:
            assert report.observable is False
            assert report.trend == "too near the Sun"
            assert any("Sun" in note for note in report.notes)
            return
    pytest.skip("no Mercury solar conjunction found in the scanned year")


@requires_ephemeris
def test_inner_planets_carry_a_twilight_note(home):
    window = night_window(date(2026, 9, 15), home)
    for planet in INNER_PLANETS:
        report = report_planet(planet, home, window, find_next=False)
        assert any("twilight" in note for note in report.notes)
