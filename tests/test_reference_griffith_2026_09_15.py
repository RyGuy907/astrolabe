"""External cross-check for 2026-09-15 at Griffith Observatory.

PLAN.md 7: "Don't trust self-generated astronomical values." Every number in
EXTERNAL below was retrieved from a published third-party source on
2026-09-15 and is recorded here verbatim. None of them was produced by this
codebase. If you regenerate this fixture, re-fetch from the sources rather
than pasting in our own output.

Why this site
-------------
Griffith Observatory, 34.11833 N, 118.300333 W, 346 m — coordinates and
elevation per https://en.wikipedia.org/wiki/Griffith_Observatory. A public
landmark is used deliberately: anyone can re-run these queries and check the
answers, which a private address does not allow. (This file previously
anchored on the author's home coordinates to four decimal places, which is
roughly 11 m of precision and not something to publish.)

The site is defined **in this file** rather than read from
`config/locations.yaml`. A reference fixture pins external literals, so its
inputs must be pinned too; reading them from editable config would mean a
change to the shipped example sites could silently alter what this test
claims to have verified.

Sources
-------
USNO  US Naval Observatory, Astronomical Applications Dept, API v4.0.1,
      "Complete Sun and Moon Data for One Day":
      https://aa.usno.navy.mil/api/rstt/oneday?date=2026-09-15
          &coords=34.11833,-118.300333&tz=-7&dst=false
      Retrieved 2026-09-15. Times as returned, in UTC-7 (PDT).
      Note: passing tz=-7 together with dst=true makes USNO add a second
      hour of DST. tz=-7&dst=false and tz=-8&dst=true both give true PDT;
      both were fetched when this was captured and agree to the minute on
      every value below.

SSO   sunrise-sunset.org public API:
      https://api.sunrise-sunset.org/json?lat=34.11833&lng=-118.300333
          &date=2026-09-15&formatted=0
      Retrieved 2026-09-15. Returned in UTC; PDT equivalents noted inline.
      Used only for the astronomical twilight boundary, which the USNO
      one-day endpoint does not publish. Requires a User-Agent header; it
      answers 403 to a bare urllib request.

Tolerances
----------
Published tables round to the whole minute, and the sources differ from each
other on sunset by ~1 min because sunrise-sunset.org ignores site elevation
and uses a lower-precision solar model. We therefore check each value against
the *better* source for that quantity and allow 2 minutes:

  * sunset, moonrise, moonset, civil twilight  -> USNO (elevation-aware)
  * astronomical twilight end                  -> SSO (only source that has it)

A tighter tolerance would be testing rounding behaviour, not correctness.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from conftest import requires_ephemeris
from engine.ephem import night_window
from engine.locations import Location
from engine.timeutil import to_local

PDT = ZoneInfo("America/Los_Angeles")
TOLERANCE = timedelta(minutes=2)

REFERENCE_DATE = date(2026, 9, 15)

#: The site these published values were fetched for. Bortle is irrelevant to
#: every assertion here — all of them are sun and moon geometry — so it is
#: left unset rather than invented.
REFERENCE_SITE = Location(
    key="griffith_reference",
    name="Griffith Observatory",
    lat=34.11833,
    lon=-118.300333,
    elevation_m=346.0,
    bortle=None,
    tz="America/Los_Angeles",
)

# --- externally published values, verbatim ---------------------------------

EXTERNAL = {
    # USNO Sun "Set", 2026-09-15, UTC-7.
    "sunset": "19:00",
    # USNO "End Civil Twilight", 2026-09-15, UTC-7.
    "civil_dusk": "19:25",
    # SSO astronomical_twilight_end 2026-09-16T03:24:15+00:00 = 20:24 PDT.
    "astronomical_dusk": "20:24",
    # USNO Moon "Rise", 2026-09-15, UTC-7.
    "moonrise": "11:18",
    # USNO Moon "Set", 2026-09-15, UTC-7.
    "moonset": "21:23",
}

# USNO "fracillum" for 2026-09-15 = "22%". USNO reports this at local noon,
# not at the middle of the night, so it is checked against a noon sample
# rather than against NightWindow.moon_illumination (which is deliberately
# sampled at the night's midpoint).
#
# The two timezone forms disagree by one point here — tz=-7&dst=false gives
# 22%, tz=-8&dst=true gives 23% — because each pins "local noon" slightly
# differently. The tolerance below spans both rather than pretending the
# published figure is sharper than it is.
EXTERNAL_FRACILLUM_AT_LOCAL_NOON = 0.22
EXTERNAL_FRACILLUM_TOLERANCE = 0.015

# USNO "curphase" for 2026-09-15.
EXTERNAL_PHASE = "Waxing Crescent"

#: Hours of true dark (sun below -18 AND moon down). **Self-generated, not
#: published** — no source gives this intersection. Every input to it is
#: externally verified above; this is a regression guard only, and is labelled
#: as such so it is never mistaken for a cross-check.
DARK_HOURS_REGRESSION = 7.84


@pytest.fixture(scope="module")
def reference_night():
    """The night of 2026-09-15 at the site pinned above, not at config's."""
    return night_window(REFERENCE_DATE, REFERENCE_SITE)


def _local_hhmm(value: datetime) -> datetime:
    """Computed UTC instant -> local wall clock, truncated to the minute."""
    return to_local(value, "America/Los_Angeles").replace(second=0, microsecond=0)


def _expected(hhmm: str, day_offset: int = 0) -> datetime:
    hour, minute = (int(p) for p in hhmm.split(":"))
    return datetime(
        REFERENCE_DATE.year, REFERENCE_DATE.month, REFERENCE_DATE.day,
        hour, minute, tzinfo=PDT,
    ) + timedelta(days=day_offset)


@requires_ephemeris
@pytest.mark.parametrize(
    "field, key",
    [
        ("sunset_utc", "sunset"),
        ("civil_dusk_utc", "civil_dusk"),
        ("astronomical_dusk_utc", "astronomical_dusk"),
        ("moonrise_utc", "moonrise"),
        ("moonset_utc", "moonset"),
    ],
)
def test_matches_published_value(reference_night, field, key):
    """Each computed event lands within 2 minutes of the published value."""
    computed = getattr(reference_night, field)
    assert computed is not None, f"{field} was not computed"

    got = _local_hhmm(computed)
    want = _expected(EXTERNAL[key])
    delta = abs(got - want)

    assert delta <= TOLERANCE, (
        f"{field}: computed {got:%H:%M} PDT, published {want:%H:%M} PDT, "
        f"delta {delta}"
    )


@requires_ephemeris
def test_moon_illumination_matches_usno_at_its_reference_instant():
    """USNO's 22% is a local-noon figure; ours agrees there.

    This is the check that the illumination model is right. NightWindow's own
    `moon_illumination` samples the midpoint of the night instead, which is
    the more useful number for planning but is not what USNO publishes.
    """
    from skyfield import almanac

    from engine.ephem import load_ephemeris

    eph = load_ephemeris()
    noon_local = datetime(2026, 9, 15, 12, 0, tzinfo=PDT)
    fraction = float(
        almanac.fraction_illuminated(
            eph.kernel, "moon", eph.timescale.from_datetime(noon_local)
        )
    )

    assert abs(fraction - EXTERNAL_FRACILLUM_AT_LOCAL_NOON) <= EXTERNAL_FRACILLUM_TOLERANCE, (
        f"computed {fraction:.3f} at local noon, USNO published "
        f"{EXTERNAL_FRACILLUM_AT_LOCAL_NOON:.2f}"
    )


@requires_ephemeris
def test_moon_is_waxing_crescent(reference_night):
    """Sanity check against USNO's curphase string."""
    assert EXTERNAL_PHASE.startswith("Waxing")
    assert reference_night.moon_waxing is True
    assert 0.02 < reference_night.moon_illumination < 0.45


@requires_ephemeris
def test_true_dark_window_starts_at_moonset(reference_night):
    """On this night the moon sets after astronomical dusk.

    So true dark opens at moonset (21:23 PDT) rather than at dusk (20:24),
    and is about an hour shorter than astronomical night. This asserts the
    dark window is actually the intersection and not just a copy of the
    astronomical night, which is the failure mode worth catching.

    NOTE: unlike everything above, `dark_hours` is **not** externally
    verified — no published source gives the sun-below-18-and-moon-down
    intersection. Each of its inputs is verified above; the figure pinned
    here is this codebase's own output, kept only as a regression guard.
    """
    assert reference_night.moon_up_at_dusk is True
    assert len(reference_night.dark_intervals) == 1

    dark_start, dark_end = reference_night.dark_intervals[0]
    # The structural claim: true dark opens exactly at moonset and closes at
    # astronomical dawn. Compared against the *computed* moonset instant, not
    # the published wall-clock minute — whether that instant matches USNO is
    # a separate question, already answered within tolerance by
    # test_matches_published_value. Asserting minute-equality here instead
    # made this test fail on a 27-second rounding difference, which tests
    # truncation rather than the intersection logic.
    assert dark_start == reference_night.moonset_utc
    assert dark_end == reference_night.astronomical_dawn_utc

    assert reference_night.dark_hours < reference_night.astronomical_night_hours
    assert reference_night.dark_hours == pytest.approx(DARK_HOURS_REGRESSION, abs=0.05)



@requires_ephemeris
def test_twilights_are_monotonic(reference_night):
    """Dusk boundaries descend in order, dawn boundaries ascend in order."""
    ordered = [
        reference_night.sunset_utc,
        reference_night.civil_dusk_utc,
        reference_night.nautical_dusk_utc,
        reference_night.astronomical_dusk_utc,
        reference_night.astronomical_dawn_utc,
        reference_night.nautical_dawn_utc,
        reference_night.civil_dawn_utc,
        reference_night.sunrise_utc,
    ]
    assert all(a < b for a, b in zip(ordered, ordered[1:])), ordered
