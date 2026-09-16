"""Meteor showers, eclipses and conjunctions, with external cross-checks.

External sources
----------------
NASA  Lunar eclipse dates, types and greatest-eclipse times from the NASA GSFC
      eclipse catalogue, https://eclipse.gsfc.nasa.gov/lunar.html, retrieved
      2026-08-25. Values recorded verbatim in NASA_LUNAR_ECLIPSES below.

IMO   Meteor shower parameters from the IMO Working List of Visual Meteor
      Showers, https://www.imo.net/resources/calendar/, retrieved 2026-08-25.
      The vendored copy lives in engine/data/meteor_showers.json with its own
      source header and an annual-refresh TODO.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from conftest import requires_ephemeris
from engine.events import (
    MOON_CONJUNCTION_DEG,
    PLANET_CONJUNCTION_DEG,
    MeteorShower,
    assess_shower,
    conjunctions,
    load_meteor_showers,
    lunar_eclipses,
    observed_rate,
    showers_active_between,
)
from engine.ephem import night_window
from engine.timeutil import is_aware_utc

# --- externally published values, verbatim ----------------------------------

# NASA GSFC, https://eclipse.gsfc.nasa.gov/lunar.html — greatest eclipse, UTC.
NASA_LUNAR_ECLIPSES = [
    (datetime(2027, 2, 20, 23, 14, 6, tzinfo=timezone.utc), "Penumbral"),
    (datetime(2027, 7, 18, 16, 4, 9, tzinfo=timezone.utc), "Penumbral"),
    (datetime(2027, 8, 17, 7, 14, 59, tzinfo=timezone.utc), "Penumbral"),
]

# Skyfield and NASA differ slightly in how greatest eclipse is defined, and a
# penumbral minimum is shallow and therefore poorly constrained in time.
ECLIPSE_TOLERANCE = timedelta(minutes=5)


@pytest.fixture(scope="module")
def showers():
    return load_meteor_showers()


# --- lunar eclipses against NASA --------------------------------------------

@requires_ephemeris
def test_lunar_eclipses_match_nasa(home):
    """Every 2027 lunar eclipse, by date, type and time."""
    found = lunar_eclipses(date(2027, 1, 1), 365, home)
    assert len(found) == len(NASA_LUNAR_ECLIPSES), (
        f"expected {len(NASA_LUNAR_ECLIPSES)} eclipses in 2027, got "
        f"{[(e.time_utc.date(), e.kind) for e in found]}"
    )

    for computed, (published_time, published_kind) in zip(found, NASA_LUNAR_ECLIPSES):
        assert computed.time_utc.date() == published_time.date()
        assert computed.kind == published_kind
        delta = abs(computed.time_utc - published_time)
        assert delta <= ECLIPSE_TOLERANCE, (
            f"{published_time.date()}: computed {computed.time_utc}, "
            f"NASA {published_time}, delta {delta}"
        )


@requires_ephemeris
def test_eclipse_visibility_is_flagged_per_site(home):
    """An eclipse with the Moon below the horizon is not visible from here."""
    found = lunar_eclipses(date(2027, 1, 1), 365, home)
    for eclipse in found:
        assert eclipse.visible == (eclipse.moon_altitude_deg > 0.0)
    # At least one of the three 2027 eclipses is not visible from California.
    assert any(not e.visible for e in found)


@requires_ephemeris
def test_no_eclipses_in_a_window_that_has_none(home):
    """The 90 days from 2026-09-15 contain no lunar eclipse."""
    assert lunar_eclipses(date(2026, 9, 15), 90, home) == []


@requires_ephemeris
def test_eclipse_times_are_aware_utc(home):
    for eclipse in lunar_eclipses(date(2027, 1, 1), 365, home):
        assert is_aware_utc(eclipse.time_utc)


# --- meteor showers ---------------------------------------------------------

def test_the_imo_working_list_loads(showers):
    assert len(showers) == 12
    names = {s.name for s in showers}
    assert {"Perseids", "Geminids", "Quadrantids", "Orionids"} <= names


@pytest.mark.parametrize(
    "name, peak_md, zhr",
    [("Perseids", "08-13", 100), ("Geminids", "12-14", 150),
     ("Quadrantids", "01-04", 120), ("Orionids", "10-22", 20)],
)
def test_shower_parameters_match_the_imo_list(showers, name, peak_md, zhr):
    """Values as published by the IMO — see the module docstring."""
    shower = next(s for s in showers if s.name == name)
    assert shower.peak == peak_md
    assert shower.zhr == zhr


def test_quadrantids_activity_wraps_the_year_end(showers):
    """Active 12-28 to 01-12, which a naive range check gets wrong."""
    quadrantids = next(s for s in showers if s.name == "Quadrantids")

    assert quadrantids.is_active_on(date(2026, 12, 30)) is True
    assert quadrantids.is_active_on(date(2027, 1, 3)) is True
    assert quadrantids.is_active_on(date(2026, 6, 15)) is False


def test_perseid_activity_window(showers):
    perseids = next(s for s in showers if s.name == "Perseids")
    assert perseids.is_active_on(date(2026, 8, 12)) is True
    assert perseids.is_active_on(date(2026, 9, 15)) is False


def test_next_peak_rolls_into_the_following_year(showers):
    perseids = next(s for s in showers if s.name == "Perseids")
    # Asking in September, the next Perseid peak is next August.
    assert perseids.next_peak(date(2026, 9, 15)) == date(2027, 8, 13)
    assert perseids.next_peak(date(2026, 1, 1)) == date(2026, 8, 13)


def test_showers_active_between_is_ordered_and_bounded(showers):
    peaks = showers_active_between(date(2026, 9, 15), 90, showers)
    dates = [p for _, p in peaks]

    assert dates == sorted(dates)
    assert all(date(2026, 9, 15) <= d <= date(2026, 12, 14) for d in dates)
    names = [s.name for s, _ in peaks]
    assert "Geminids" in names and "Orionids" in names
    assert "Perseids" not in names          # peaked in August


# --- PLAN.md 3.5 rate formula -----------------------------------------------

def test_observed_rate_is_zero_below_the_horizon():
    """No meteors from a radiant under your feet."""
    assert observed_rate(100, -10.0, 6.5) == 0.0
    assert observed_rate(100, 0.0, 6.5) == 0.0


def test_observed_rate_at_the_zenith_under_a_reference_sky():
    """sin(90) = 1 and 2^(6.5-6.5) = 1, so the rate is the ZHR itself."""
    assert observed_rate(100, 90.0, 6.5) == pytest.approx(100.0)


def test_observed_rate_falls_with_radiant_altitude():
    high = observed_rate(100, 80.0, 6.5)
    low = observed_rate(100, 20.0, 6.5)
    assert high > low
    assert low == pytest.approx(100 * 0.342, abs=1.0)      # sin(20)


def test_a_brighter_sky_halves_the_rate_per_magnitude():
    """The 2^(lm-6.5) term: one magnitude of light pollution costs half."""
    dark = observed_rate(100, 90.0, 6.5)
    one_mag_worse = observed_rate(100, 90.0, 5.5)
    assert one_mag_worse == pytest.approx(dark / 2.0)


def test_cloud_scales_the_rate_linearly():
    assert observed_rate(100, 90.0, 6.5, cloud_fraction=0.5) == pytest.approx(50.0)
    assert observed_rate(100, 90.0, 6.5, cloud_fraction=1.0) == pytest.approx(0.0)


@requires_ephemeris
def test_geminids_are_strong_and_orionids_are_moon_hit(home, showers):
    """2026: the Geminids fall near new moon, the Orionids near a 90% moon."""
    geminids = next(s for s in showers if s.name == "Geminids")
    orionids = next(s for s in showers if s.name == "Orionids")

    gem = assess_shower(geminids, home, night_window(date(2026, 12, 14), home))
    ori = assess_shower(orionids, home, night_window(date(2026, 10, 22), home))

    assert gem.best_rate > 100, "Geminids near new moon should be excellent"
    assert gem.best_radiant_altitude_deg > 60
    assert ori.moon_illumination > 0.8
    assert any("moon" in note for note in ori.notes)


@requires_ephemeris
def test_shower_assessment_times_are_aware_utc(home, showers):
    forecast = assess_shower(showers[0], home,
                             night_window(date(2026, 12, 14), home))
    if forecast.best_time_utc is not None:
        assert is_aware_utc(forecast.best_time_utc)


# --- conjunctions -----------------------------------------------------------

@requires_ephemeris
def test_conjunctions_respect_their_thresholds():
    found = conjunctions(date(2026, 9, 15), 90)
    assert found, "expected at least one conjunction in 90 days"
    for event in found:
        limit = MOON_CONJUNCTION_DEG if event.involves_moon else PLANET_CONJUNCTION_DEG
        assert event.separation_deg <= limit


@requires_ephemeris
def test_conjunctions_are_chronological():
    found = conjunctions(date(2026, 9, 15), 90)
    assert [c.time_utc for c in found] == sorted(c.time_utc for c in found)


@requires_ephemeris
def test_conjunction_times_are_aware_utc():
    for event in conjunctions(date(2026, 9, 15), 90):
        assert is_aware_utc(event.time_utc)


@requires_ephemeris
def test_mars_jupiter_conjunction_in_november_2026():
    """A real planet-planet close approach the scan should find."""
    found = conjunctions(date(2026, 11, 1), 30, include_moon=False)
    pairs = {(c.body_a, c.body_b) for c in found}
    assert ("mars", "jupiter") in pairs

    event = next(c for c in found if (c.body_a, c.body_b) == ("mars", "jupiter"))
    assert event.separation_deg < 2.0
    assert event.time_utc.date().month == 11


@requires_ephemeris
def test_excluding_the_moon_removes_lunar_conjunctions():
    with_moon = conjunctions(date(2026, 9, 15), 60, include_moon=True)
    without = conjunctions(date(2026, 9, 15), 60, include_moon=False)

    assert any(c.involves_moon for c in with_moon)
    assert not any(c.involves_moon for c in without)
    assert len(without) < len(with_moon)


@requires_ephemeris
def test_a_tighter_threshold_finds_fewer_conjunctions():
    loose = conjunctions(date(2026, 9, 15), 120, planet_threshold_deg=5.0,
                         include_moon=False)
    tight = conjunctions(date(2026, 9, 15), 120, planet_threshold_deg=1.0,
                         include_moon=False)
    assert len(tight) <= len(loose)
    assert all(c.separation_deg <= 1.0 for c in tight)
