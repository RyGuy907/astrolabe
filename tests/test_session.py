"""Observing sessions: the hours you actually plan to be outside.

Everything used to be computed across the whole astronomical night. That
answers "what is this night like", which is not the question anyone asks --
nobody observes dusk to dawn on a Tuesday. A night clear until midnight and
overcast afterwards averaged out to "partly cloudy", and an object rising at
04:00 was listed as tonight's target. Both true of the night, both useless to
somebody deciding whether to set up.

These pin the default, the clipping, and the polar cases where the honest
answer is that there are no observing hours at all.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from conftest import REFERENCE_DATE, requires_ephemeris
from engine.ephem import night_window
from engine.horizon import parse_horizon
from engine.locations import Location
from engine.session import (
    DEFAULT_END_HOUR,
    dark_overlap_hours,
    default_session,
    resolve_session,
    session_hours,
)
from engine.timeutil import is_aware_utc, to_local


def _site(lat: float, lon: float, tz: str) -> Location:
    return Location(key="probe", name="Probe", lat=lat, lon=lon,
                    elevation_m=1400.0, bortle=5, tz=tz,
                    horizon=parse_horizon(0))


UTAH = _site(40.4, -111.8, "America/Denver")
TROMSO = _site(69.6, 18.9, "Europe/Oslo")


@requires_ephemeris
def test_the_default_runs_from_astronomical_dusk():
    """Not sunset. There is no point starting before the sky is dark, and
    starting at sunset dragged an hour of twilight into every average."""
    window = night_window(REFERENCE_DATE, UTAH)
    session = default_session(window)
    assert session is not None
    assert session[0] == window.astronomical_dusk_utc


@requires_ephemeris
def test_the_default_ends_at_one_in_the_morning_local():
    """Roughly when a weeknight observer gives up. Local, not UTC: the whole
    point is the clock the observer is looking at."""
    window = night_window(REFERENCE_DATE, UTAH)
    session = default_session(window)
    end_local = to_local(session[1], UTAH.tz)
    assert end_local.hour == DEFAULT_END_HOUR
    assert end_local.minute == 0
    # 01:00 belongs to the morning after the evening the night is named for.
    assert end_local.date() == REFERENCE_DATE + timedelta(days=1)


@requires_ephemeris
def test_both_ends_are_aware_utc():
    """The invariant, at a new engine boundary."""
    session = default_session(night_window(REFERENCE_DATE, UTAH))
    assert is_aware_utc(session[0]) and is_aware_utc(session[1])


@requires_ephemeris
def test_a_session_cannot_run_into_daylight():
    """Asking for targets at noon must not be answered with targets at noon.

    Both ends clip to sunset and sunrise, because the engine has nothing to
    say about a daylit sky and would happily compute altitudes for one.
    """
    window = night_window(REFERENCE_DATE, UTAH)
    noon = datetime(2026, 9, 16, 19, 0, tzinfo=timezone.utc)   # ~13:00 MDT
    session = resolve_session(window, window.sunset_utc - timedelta(hours=6), noon)
    assert session is not None
    assert session[0] >= window.sunset_utc
    assert session[1] <= window.sunrise_utc


@requires_ephemeris
def test_one_end_can_be_given_alone():
    """Changing the finishing time should not force the observer to restate
    the start, which they never chose in the first place."""
    window = night_window(REFERENCE_DATE, UTAH)
    fallback = default_session(window)
    earlier_end = fallback[1] - timedelta(hours=1)

    session = resolve_session(window, None, earlier_end)
    assert session[0] == fallback[0]
    assert session[1] == earlier_end


@requires_ephemeris
def test_an_inverted_request_falls_back_rather_than_inverting():
    """An end before the start is a caller bug, not a negative-length night."""
    window = night_window(REFERENCE_DATE, UTAH)
    fallback = default_session(window)
    session = resolve_session(window, fallback[1], fallback[0])
    assert session == fallback
    assert session_hours(session) > 0


@requires_ephemeris
def test_polar_summer_has_no_session_at_all():
    """The honest answer is none, not a fabricated interval. Tromso in June
    has no astronomical night and no sunset."""
    window = night_window(date(2026, 6, 21), TROMSO)
    assert default_session(window) is None
    assert session_hours(None) == 0.0


@requires_ephemeris
def test_a_long_winter_night_still_ends_at_one():
    """The default is a plan, not a maximum. A 17-hour polar night does not
    mean anybody is standing outside for 17 hours."""
    window = night_window(date(2026, 12, 21), TROMSO)
    session = default_session(window)
    assert session is not None
    assert to_local(session[1], TROMSO.tz).hour == DEFAULT_END_HOUR


@requires_ephemeris
def test_a_short_summer_night_does_not_invert():
    """In late June at mid-northern latitudes astronomical dusk falls after
    01:00, so the naive span would run backwards."""
    window = night_window(date(2026, 6, 21), UTAH)
    session = default_session(window)
    assert session is not None
    assert session[1] > session[0]


@requires_ephemeris
def test_dark_overlap_counts_only_the_moonless_part():
    """Reported, not enforced: an observer out from dusk with the Moon up is
    not making a mistake, and the scoring already says what it costs."""
    window = night_window(REFERENCE_DATE, UTAH)
    session = default_session(window)
    overlap = dark_overlap_hours(window, session)
    assert 0.0 <= overlap <= session_hours(session) + 1e-9
    assert dark_overlap_hours(window, None) == 0.0
