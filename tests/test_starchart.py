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
    eps = star(chart, "Khepdenreret")   # epsilon Her (IAU-named 2025), south-east
    assert eta.y == pytest.approx(2.46, abs=0.05) and abs(eta.x) < 0.5
    assert eps.x < 0 and eps.y < 0


@requires_ephemeris
def test_as_seen_puts_the_zenith_up():
    chart = finder_chart(*M13, UTAH, EVENING, orientation=AS_SEEN)
    assert 40 < chart.center_alt_deg < 50 and 270 < chart.center_az_deg < 300
    # Facing west-north-west, north is to the right...
    assert star(chart, "η Her").x > 0
    # ...and the south-eastern side sets later, so it stands higher.
    eps = star(chart, "Khepdenreret")   # epsilon Her, IAU-named 2025
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

    ra, dec = body_position("saturn", UTAH, EVENING)
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


# --- the interactive chart's frame ------------------------------------------

def _frame_error_arcsec(frame, ra_deg, dec_deg, alt_deg, az_deg):
    """Angle between where the frame puts (ra, dec) and the exact alt/az."""
    import numpy as np
    r, d = np.radians(ra_deg), np.radians(dec_deg)
    v = np.array([np.cos(d) * np.cos(r), np.cos(d) * np.sin(r), np.sin(d)])
    got = np.array(frame.matrix) @ v
    a, z = np.radians(alt_deg), np.radians(az_deg)
    want = np.array([np.cos(a) * np.sin(z), np.cos(a) * np.cos(z), np.sin(a)])
    return float(np.degrees(np.arccos(np.clip(got @ want, -1, 1)))) * 3600


@requires_ephemeris
def test_sky_frame_is_a_rotation():
    import numpy as np
    from engine.starchart import sky_frame
    m = np.array(sky_frame(UTAH, EVENING).matrix)
    assert np.allclose(m @ m.T, np.eye(3), atol=1e-8)
    assert np.linalg.det(m) == pytest.approx(1.0, abs=1e-8)     # not a mirror


@requires_ephemeris
def test_sky_frame_puts_stars_where_skyfield_does():
    """The client multiplies every star by this matrix and nothing else, so it
    must reproduce Skyfield's apparent alt/az to well inside a star dot."""
    from skyfield.api import Star
    from engine.ephem import FIXED_TARGET_DEFLECTORS, _observer, load_ephemeris
    from engine.starchart import sky_frame

    frame = sky_frame(UTAH, EVENING)
    eph = load_ephemeris()
    t = eph.timescale.from_datetime(EVENING)
    here = _observer(eph, UTAH).at(t)
    # Spread over the sky: M13, Vega, Polaris, Fomalhaut, Capella.
    for ra, dec in [M13, (279.2347, 38.7837), (37.9546, 89.2641),
                    (344.4127, -29.6222), (79.1723, 45.998)]:
        alt, az, _ = here.observe(Star(ra_hours=ra / 15, dec_degrees=dec)) \
            .apparent(FIXED_TARGET_DEFLECTORS).altaz()
        assert _frame_error_arcsec(frame, ra, dec, alt.degrees, az.degrees) < 60


@requires_ephemeris
def test_sky_frame_puts_the_moon_where_it_is_from_the_site():
    """The Moon is close enough that where you stand moves it by up to a
    degree against the stars; the chart must show it from the site."""
    from engine.ephem import _observer, load_ephemeris
    from engine.starchart import sky_frame

    for when in (EVENING, datetime(2026, 9, 27, 3, 0, tzinfo=timezone.utc)):
        frame = sky_frame(UTAH, when)
        eph = load_ephemeris()
        t = eph.timescale.from_datetime(when)
        alt, az, _ = _observer(eph, UTAH).at(t).observe(eph.target("moon")) \
            .apparent().altaz()
        name, ra, dec = next(b for b in frame.bodies if b[0] == "moon")
        assert _frame_error_arcsec(frame, ra, dec, alt.degrees, az.degrees) < 90


def test_sky_catalog_is_brightest_first_and_consistent():
    from engine.starchart import sky_catalog
    cat = sky_catalog()
    n = len(cat["ra"])
    assert n > 30_000 and len(cat["dec"]) == n and len(cat["mag"]) == n
    assert cat["mag"] == sorted(cat["mag"])          # a prefix is a magnitude cut
    named = {name: int(i) for i, name in cat["labels"].items()}
    assert cat["mag"][named["Sirius"]] < -1
    assert "UMa" in cat["constellations"] and cat["constellations"]["UMa"]["lines"]
