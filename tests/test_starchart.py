"""Finder charts: the layout the display layer draws without further maths.

The orientation conventions are the thing most worth pinning down, because
getting one backwards produces a chart that looks entirely plausible and is
a mirror image of the sky. Checked against known stars around M13:

* eta Herculis is 2.5 deg due north of M13; zeta Her (Rutilicus) is further
  north-west, epsilon Her south-east.
* North up: north is +y and east is -x -- the sky seen overhead, the mirror of
  a map.
* As seen: zenith is +y. At 22:00 MDT on 2026-09-21 M13 is ~45 deg up in the
  west-north-west, so north lies to the right and the eastern side of the
  constellation, setting later, is higher.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from conftest import requires_ephemeris
from engine.locations import Location
from engine.starchart import (AS_SEEN, NORTH_UP, finder_chart,
                              limiting_mag_for)

UTAH = Location(key="utah", name="Utah", lat=40.408, lon=-111.792,
                elevation_m=1400, tz="America/Denver")
M13 = (250.4235, 36.4613)
EVENING = datetime(2026, 9, 22, 4, 0, tzinfo=timezone.utc)     # 22:00 MDT


def star(chart, label):
    return next(s for s in chart.stars if s.label == label)


@requires_ephemeris
def test_north_up_puts_north_up_and_east_left():
    chart = finder_chart(*M13, UTAH, EVENING, orientation=NORTH_UP)
    eta = star(chart, "η Her")          # 2.5 deg north, a hair east
    eps = star(chart, "ε Her")          # south-east
    assert eta.y == pytest.approx(2.46, abs=0.05) and abs(eta.x) < 0.5
    assert eps.x < 0 and eps.y < 0


@requires_ephemeris
def test_as_seen_puts_the_zenith_up():
    chart = finder_chart(*M13, UTAH, EVENING, orientation=AS_SEEN)
    assert 40 < chart.center_alt_deg < 50 and 270 < chart.center_az_deg < 300
    # Facing west-north-west, north is to the right...
    assert star(chart, "η Her").x > 0
    # ...and the south-eastern side sets later, so it stands higher.
    eps = star(chart, "ε Her")
    assert eps.x < 0 and eps.y > 0


@requires_ephemeris
def test_everything_drawn_is_inside_the_frame():
    for orientation in (AS_SEEN, NORTH_UP):
        chart = finder_chart(*M13, UTAH, EVENING, radius_deg=10,
                             orientation=orientation)
        assert chart.stars
        for point in chart.stars + chart.objects:
            assert abs(point.x) <= 10 and abs(point.y) <= 10


@requires_ephemeris
def test_a_wider_field_shows_fewer_faint_stars():
    """A wide field is for finding the region by eye; magnitude 8 would bury
    the pattern. A narrow one is for the finder scope."""
    assert limiting_mag_for(5) > limiting_mag_for(10) > limiting_mag_for(20)
    narrow = finder_chart(*M13, UTAH, EVENING, radius_deg=5)
    assert max(s.mag for s in narrow.stars) <= limiting_mag_for(5)
    assert any(s.mag > limiting_mag_for(10) for s in narrow.stars)


@requires_ephemeris
def test_neighbours_in_the_field_are_marked():
    m92 = (259.2808, 43.1359)
    chart = finder_chart(*M13, UTAH, EVENING, radius_deg=10,
                         nearby_positions=[(*m92, "M92", "Globular Clusters"),
                                           (83.8, -5.4, "M42", "Nebulae")])
    assert [o.label for o in chart.objects] == ["M92"]     # M42 is not in frame


@requires_ephemeris
def test_the_horizon_is_drawn_only_as_seen_and_only_when_in_frame():
    low = finder_chart(*M13, UTAH, datetime(2026, 9, 22, 7, 30, tzinfo=timezone.utc),
                       radius_deg=20, orientation=AS_SEEN)
    assert low.center_alt_deg < 20
    assert low.horizon and low.directions
    assert all(p.label in {"N", "NE", "E", "SE", "S", "SW", "W", "NW"}
               for p in low.directions)
    north = finder_chart(*M13, UTAH, EVENING, radius_deg=20, orientation=NORTH_UP)
    assert not north.horizon and not north.directions


@requires_ephemeris
def test_a_planet_chart_leaves_the_planet_itself_off_the_neighbours():
    from engine.starchart import body_position

    ra, dec = body_position("saturn", EVENING)
    chart = finder_chart(ra, dec, UTAH, EVENING, radius_deg=10,
                         exclude_body="saturn")
    assert "Saturn" not in [o.label for o in chart.objects]
    # In 2026 Neptune sits a few degrees from Saturn; it should be marked.
    assert "Neptune" in [o.label for o in chart.objects]


def test_chart_times_are_aware_utc():
    from engine.timeutil import is_aware_utc

    chart = finder_chart(*M13, UTAH, EVENING)
    assert is_aware_utc(chart.at_utc)


def test_a_bad_orientation_is_refused():
    with pytest.raises(ValueError):
        finder_chart(*M13, UTAH, EVENING, orientation="south")


@requires_ephemeris
def test_the_sky_overview_shows_only_the_major_constellations():
    """Past 20 degrees the chart is 'where is this in the sky': the figures
    and names of rank 1-2 constellations only, and bright stars."""
    from engine.starchart import OVERVIEW_MAX_RANK, _figures

    chart = finder_chart(*M13, UTAH, EVENING, radius_deg=45)
    assert chart.overview
    names = {c.label for c in chart.constellations}
    assert {"Hercules", "Lyra", "Boötes"} <= names
    minor = {f["name"] for f in _figures().values() if f["rank"] > OVERVIEW_MAX_RANK}
    assert not names & minor                   # no Sagitta, no Vulpecula
    assert chart.limiting_mag <= 4.0
    # And a hopping chart is not an overview and names nothing.
    hop = finder_chart(*M13, UTAH, EVENING, radius_deg=10)
    assert not hop.overview and not hop.constellations


@requires_ephemeris
def test_the_chart_carries_fainter_stars_for_the_density_control():
    """The client thins or thickens the field without asking again, so the
    chart arrives deeper than its default -- never past the catalogue."""
    chart = finder_chart(*M13, UTAH, EVENING, radius_deg=20)
    assert chart.max_mag == pytest.approx(chart.limiting_mag + 1.5)
    assert max(s.mag for s in chart.stars) > chart.limiting_mag
    narrow = finder_chart(*M13, UTAH, EVENING, radius_deg=5)
    assert narrow.max_mag == 8.0                # capped at the catalogue
    shallow = finder_chart(*M13, UTAH, EVENING, radius_deg=20, extra_mag=0)
    assert max(s.mag for s in shallow.stars) <= shallow.limiting_mag
