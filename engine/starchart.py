"""The interactive sky chart's data: the static sky, and the view of it from
one site at one moment.

The chart pans and zooms on the client, so the client takes the whole static
catalogue once (`sky_catalog`) and, per moment, only what depends on it
(`sky_frame`): the rotation from the catalogue's frame to the observer's
horizon, and where the Moon and planets are. The astronomy is inside that
rotation, computed here; the client only multiplies.

Stars come from `stars.csv` and stick figures from `constellation_lines.json`,
vendored by `scripts/fetch_star_chart_data.py`. Nothing here touches the
network.

(This module used to draw fixed-field SVG finder charts too, served by
/api/finder. The interactive chart replaced them and they were removed.)
"""

from __future__ import annotations

import csv
import functools
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from skyfield.api import Star

from .ephem import FIXED_TARGET_DEFLECTORS, _observer, load_ephemeris
from .locations import Location
from .timeutil import ensure_utc

DATA = Path(__file__).resolve().parent / "catalog" / "data"

#: The bodies the chart places: the Moon and planets. Never the Sun -- a sky
#: chart is a night-time thing.
CHARTED_BODIES = ("moon", "mercury", "venus", "mars", "jupiter", "saturn",
                  "uranus", "neptune")


@functools.lru_cache(maxsize=1)
def _stars() -> tuple[np.ndarray, np.ndarray, np.ndarray, tuple[str, ...]]:
    """RA, Dec (degrees), magnitude and label for every vendored star."""
    ra, dec, mag, label = [], [], [], []
    with open(DATA / "stars.csv", encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            ra.append(float(row["ra"]))
            dec.append(float(row["dec"]))
            mag.append(float(row["mag"]))
            label.append(row["label"])
    return np.array(ra), np.array(dec), np.array(mag), tuple(label)


@functools.lru_cache(maxsize=1)
def _figures() -> dict[str, dict]:
    """Per constellation: name, rank, label position and stick figure."""
    with open(DATA / "constellation_lines.json", encoding="utf-8") as fh:
        return json.load(fh)


# --- the interactive chart ----------------------------------------------------
#
# The zoomable chart draws on the client, so the client needs the whole
# static catalogue once and, per moment, only what depends on the moment.
# That second part is small: the rotation from the catalogue's frame to the
# observer's horizon, the same for every star, plus where the Moon and
# planets are. The astronomy -- precession, nutation, the Earth's rotation,
# the site's latitude -- is all inside that rotation, so it stays here and
# the client only multiplies.


@dataclass(frozen=True)
class SkyFrame:
    """What the interactive chart needs for one moment at one site.

    `matrix` maps a J2000 unit vector (x towards RA 0h, z to the north
    celestial pole) to the horizon frame: x east, y north, z up. So for a
    star, h = M v gives altitude asin(h_z) and azimuth atan2(h_x, h_y).
    """

    at_utc: datetime
    matrix: tuple[tuple[float, float, float], ...]
    #: The Moon and planets: (name, ra_deg, dec_deg), J2000 like the stars.
    bodies: tuple[tuple[str, float, float], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "at_utc", ensure_utc(self.at_utc, field="at_utc"))


def sky_frame(location: Location, when: datetime) -> SkyFrame:
    """The J2000-to-horizon rotation and the bodies' positions at `when`.

    Built by asking Skyfield for the apparent altitude and azimuth of the
    three J2000 axes, which folds in precession, nutation and aberration,
    and then orthonormalising: aberration is not a rotation, so the three
    come back up to 20" from mutually perpendicular. What remains is at
    most that 20" of aberration -- a hundredth of a degree, far inside a
    star dot at any zoom this chart offers.
    """
    when = ensure_utc(when, field="when")
    eph = load_ephemeris()
    observer = _observer(eph, location)
    t = eph.timescale.from_datetime(when)

    axes = Star(ra_hours=np.array([0.0, 6.0, 0.0]),
                dec_degrees=np.array([0.0, 0.0, 90.0]))
    alt, az, _ = observer.at(t).observe(axes).apparent(FIXED_TARGET_DEFLECTORS).altaz()
    alt, az = np.radians(alt.degrees), np.radians(az.degrees)
    columns = np.stack([np.cos(alt) * np.sin(az),       # east
                        np.cos(alt) * np.cos(az),       # north
                        np.sin(alt)])                   # up
    # Nearest proper rotation to the measured one.
    u, _, vt = np.linalg.svd(columns)
    rotation = u @ vt

    # From the site: the Moon's parallax moves it up to a degree against the
    # stars, which the rotation (built for infinitely distant stars) can't know.
    here = observer.at(t)
    bodies = []
    for body in CHARTED_BODIES:
        ra, dec, _ = here.observe(eph.target(body)).radec()
        bodies.append((body, round(float(ra._degrees), 5), round(float(dec.degrees), 5)))

    return SkyFrame(
        at_utc=when,
        matrix=tuple(tuple(round(float(v), 9) for v in row) for row in rotation),
        bodies=tuple(bodies),
    )


@functools.lru_cache(maxsize=1)
def sky_catalog() -> dict:
    """Every star and constellation figure, for the client to draw from.

    Static -- it is the vendored data, reshaped -- so it is built once and
    the API can let the browser cache it indefinitely. Stars come brightest
    first, as parallel arrays, so the client can take "everything down to
    magnitude m" as a prefix.
    """
    ra, dec, mag, labels = _stars()
    order = np.argsort(mag, kind="stable")
    return {
        "ra": [round(float(v), 4) for v in ra[order]],
        "dec": [round(float(v), 4) for v in dec[order]],
        "mag": [round(float(v), 2) for v in mag[order]],
        # Sparse: most stars have no name, so only the indices that do.
        "labels": {str(i): labels[j] for i, j in enumerate(order) if labels[j]},
        "constellations": _figures(),
    }
