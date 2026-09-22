"""The interactive sky chart's data: the frame for one moment at one site,
and the static catalogue.

The frame's rotation is the one piece of astronomy the chart relies on -- the
client multiplies every star by it -- so it is checked against Skyfield's own
apparent alt/az, and the Moon, which is close enough for the site's position
to move it, against its topocentric place.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from conftest import requires_ephemeris
from engine.locations import Location

UTAH = Location(key="utah", name="Utah", lat=40.408, lon=-111.792,
                elevation_m=1400, tz="America/Denver")
M13 = (250.4235, 36.4613)
EVENING = datetime(2026, 9, 22, 4, 0, tzinfo=timezone.utc)     # 22:00 MDT


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
