"""Finder charts: the patch of sky around a target, for star hopping.

A finder chart is what you hold up to the sky to get from a star you can see
to the object you cannot. So it is drawn the way the sky looks:

* **As seen from the site** (the default): stars placed by altitude and
  azimuth at a chosen moment, zenith up. Facing the target, the chart and
  the sky match without turning either -- the horizon, if it is in frame, is
  drawn where it is.
* **North up**: the atlas convention, by right ascension and declination,
  north up and east to the *left*, which is how the sky looks overhead
  (east and west are the mirror of a map's because you are looking up, not
  down).

Both use the gnomonic projection, the standard for finder charts because it
keeps great circles straight: a line of stars on the sky is a line on the
chart, which is what hopping along one relies on.

Stars come from `stars.csv` and stick figures from `constellation_lines.json`,
vendored from d3-celestial by `scripts/fetch_star_chart_data.py`. Nothing here
touches the network.

Everything returned is in chart units -- degrees on the tangent plane, the
target at (0, 0), +x right and +y up -- so the display layer only scales and
draws. The one thing that depends on the site and the time, the rotation to
"as seen", is computed here with the rest of the astronomy.
"""

from __future__ import annotations

import csv
import functools
import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
from skyfield.api import Star

from .ephem import FIXED_TARGET_DEFLECTORS, _observer, load_ephemeris
from .locations import Location
from .timeutil import ensure_utc

DATA = Path(__file__).resolve().parent / "catalog" / "data"

#: Orientation names, as the API takes them.
AS_SEEN = "sky"
NORTH_UP = "north"

#: Faintest star drawn for a field of a given half-width. A wide field is for
#: finding the region by eye, where magnitude 8 would bury the pattern; a
#: narrow one is for the finder scope, which shows that faint.
LIMITING_MAG = ((5.0, 8.0), (10.0, 7.2), (20.0, 6.2), (90.0, 5.5))

#: The Moon and planets are placed on the chart when they are in the field,
#: and the Sun is never charted: a finder chart is a night-time thing.
CHARTED_BODIES = ("moon", "mercury", "venus", "mars", "jupiter", "saturn",
                  "uranus", "neptune")


@dataclass(frozen=True)
class ChartPoint:
    """Something drawn on the chart, in chart units."""

    x: float
    y: float
    label: str
    mag: float | None = None
    kind: str = "star"          # star, deep-sky group name, or a body name


@dataclass(frozen=True)
class FinderChart:
    center_ra_deg: float
    center_dec_deg: float
    at_utc: datetime
    orientation: str
    radius_deg: float
    limiting_mag: float
    #: Where the target is at `at_utc`; negative altitude means it is below
    #: the horizon then, which the display should say.
    center_alt_deg: float
    center_az_deg: float
    stars: list[ChartPoint] = field(default_factory=list)
    lines: list[list[tuple[float, float]]] = field(default_factory=list)
    objects: list[ChartPoint] = field(default_factory=list)
    #: The horizon as a polyline, "as seen" only, when it is in the frame.
    horizon: list[tuple[float, float]] = field(default_factory=list)
    #: Compass points on the horizon, "as seen" only, when in the frame.
    directions: list[ChartPoint] = field(default_factory=list)

    def __post_init__(self) -> None:
        object.__setattr__(self, "at_utc", ensure_utc(self.at_utc, field="at_utc"))


def limiting_mag_for(radius_deg: float) -> float:
    for half_width, mag in LIMITING_MAG:
        if radius_deg <= half_width:
            return mag
    return LIMITING_MAG[-1][1]


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
def _figures() -> dict[str, list[list[list[float]]]]:
    with open(DATA / "constellation_lines.json", encoding="utf-8") as fh:
        return json.load(fh)


def _unit(ra_deg, dec_deg) -> np.ndarray:
    ra, dec = np.radians(ra_deg), np.radians(dec_deg)
    return np.stack([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra),
                     np.sin(dec)], axis=-1)


def _gnomonic(lon_deg, lat_deg, lon0_deg: float, lat0_deg: float):
    """Tangent-plane coordinates in degrees, and cos of the angular distance.

    Points with cos_c <= 0 are on the far hemisphere and have no projection.
    """
    lon, lat = np.radians(lon_deg), np.radians(lat_deg)
    lon0, lat0 = math.radians(lon0_deg), math.radians(lat0_deg)
    cos_c = (math.sin(lat0) * np.sin(lat)
             + math.cos(lat0) * np.cos(lat) * np.cos(lon - lon0))
    safe = np.where(cos_c > 1e-6, cos_c, 1.0)
    x = np.cos(lat) * np.sin(lon - lon0) / safe
    y = (math.cos(lat0) * np.sin(lat)
         - math.sin(lat0) * np.cos(lat) * np.cos(lon - lon0)) / safe
    return np.degrees(x), np.degrees(y), cos_c


class _Projector:
    """Places (ra, dec) points on the chart for one orientation and moment."""

    def __init__(self, location: Location, when: datetime, orientation: str,
                 center_ra: float, center_dec: float):
        self.orientation = orientation
        eph = load_ephemeris()
        self._observer = _observer(eph, location)
        self._t = eph.timescale.from_datetime(when)
        self.center_ra, self.center_dec = center_ra, center_dec
        (self.center_alt,), (self.center_az,) = self.altaz(
            np.array([center_ra]), np.array([center_dec]))

    def altaz(self, ra, dec):
        if len(ra) == 0:
            return np.array([]), np.array([])
        stars = Star(ra_hours=np.asarray(ra) / 15.0, dec_degrees=np.asarray(dec))
        alt, az, _ = (self._observer.at(self._t).observe(stars)
                      .apparent(FIXED_TARGET_DEFLECTORS).altaz())
        return np.atleast_1d(alt.degrees), np.atleast_1d(az.degrees)

    def project(self, ra, dec):
        """(x, y, visible) arrays for these equatorial positions."""
        ra, dec = np.asarray(ra, dtype=float), np.asarray(dec, dtype=float)
        if self.orientation == AS_SEEN:
            alt, az = self.altaz(ra, dec)
            x, y, cos_c = _gnomonic(az, alt, self.center_az, self.center_alt)
        else:
            x, y, cos_c = _gnomonic(ra, dec, self.center_ra, self.center_dec)
            x = -x                         # east to the left, looking up
        return x, y, cos_c > 1e-6

    def project_altaz(self, alt, az):
        """(x, y, visible) for horizon coordinates -- as-seen charts only."""
        x, y, cos_c = _gnomonic(np.asarray(az), np.asarray(alt),
                                self.center_az, self.center_alt)
        return x, y, cos_c > 1e-6


def finder_chart(center_ra_deg: float, center_dec_deg: float,
                 location: Location, when: datetime, *,
                 radius_deg: float = 10.0,
                 orientation: str = AS_SEEN,
                 nearby: list[ChartPoint] | None = None,
                 nearby_positions: list[tuple[float, float, str, str]] | None = None,
                 exclude_body: str | None = None) -> FinderChart:
    """The chart around (ra, dec) at `when` from `location`.

    `radius_deg` is the half-width of the square field. `nearby_positions`
    are other things worth marking -- deep-sky showpieces -- as
    (ra, dec, label, kind); they are kept when they fall in the frame.
    `exclude_body` leaves a body off when it is the chart's own subject.
    """
    when = ensure_utc(when, field="when")
    if orientation not in (AS_SEEN, NORTH_UP):
        raise ValueError(f"orientation must be {AS_SEEN!r} or {NORTH_UP!r}")
    proj = _Projector(location, when, orientation, center_ra_deg, center_dec_deg)
    limit = limiting_mag_for(radius_deg)
    # Anything within the square's corner distance might be in frame; the
    # square test after projection decides.
    reach = math.cos(math.radians(min(radius_deg * 1.5, 89.0)))
    center = _unit(center_ra_deg, center_dec_deg)

    def in_frame(x, y, visible):
        return visible & (np.abs(x) <= radius_deg) & (np.abs(y) <= radius_deg)

    # --- stars ---
    ra, dec, mag, labels = _stars()
    candidate = (mag <= limit) & (_unit(ra, dec) @ center >= reach)
    idx = np.nonzero(candidate)[0]
    x, y, vis = proj.project(ra[idx], dec[idx])
    keep = in_frame(x, y, vis)
    stars = [ChartPoint(x=round(float(x[k]), 4), y=round(float(y[k]), 4),
                        label=labels[i], mag=float(mag[i]))
             for k, i in enumerate(idx) if keep[k]]

    # --- constellation figures: every segment with an end near the field ---
    lines: list[list[tuple[float, float]]] = []
    wide = math.cos(math.radians(min(radius_deg * 2.5, 89.0)))
    for polylines in _figures().values():
        for polyline in polylines:
            pts = np.array(polyline)
            near = _unit(pts[:, 0], pts[:, 1]) @ center >= wide
            if not near.any():
                continue
            px, py, pvis = proj.project(pts[:, 0], pts[:, 1])
            run: list[tuple[float, float]] = []
            for k in range(len(pts)):
                if pvis[k] and near[k]:
                    run.append((round(float(px[k]), 4), round(float(py[k]), 4)))
                else:
                    if len(run) > 1:
                        lines.append(run)
                    run = []
            if len(run) > 1:
                lines.append(run)

    # --- deep-sky neighbours and bodies ---
    objects: list[ChartPoint] = []
    if nearby_positions:
        arr = np.array([(r, d) for r, d, _, _ in nearby_positions])
        ox, oy, ovis = proj.project(arr[:, 0], arr[:, 1])
        okeep = in_frame(ox, oy, ovis)
        for k, (_, _, label, kind) in enumerate(nearby_positions):
            if okeep[k] and (abs(ox[k]) > 1e-3 or abs(oy[k]) > 1e-3):
                objects.append(ChartPoint(x=round(float(ox[k]), 4),
                                          y=round(float(oy[k]), 4),
                                          label=label, kind=kind))
    eph = load_ephemeris()
    earth = eph.kernel["earth"]
    for body in CHARTED_BODIES:
        if body == exclude_body:
            continue
        b_ra, b_dec, _ = earth.at(proj._t).observe(eph.target(body)).radec()
        bx, by, bvis = proj.project([b_ra._degrees], [b_dec.degrees])
        if in_frame(bx, by, bvis)[0]:
            objects.append(ChartPoint(x=round(float(bx[0]), 4),
                                      y=round(float(by[0]), 4),
                                      label=body.capitalize(), kind=body))

    # --- horizon and compass, as seen only ---
    horizon: list[tuple[float, float]] = []
    directions: list[ChartPoint] = []
    if orientation == AS_SEEN:
        az = np.arange(proj.center_az - 89.0, proj.center_az + 89.5, 1.0)
        hx, hy, hvis = proj.project_altaz(np.zeros_like(az), az % 360.0)
        horizon = [(round(float(a), 4), round(float(b), 4))
                   for a, b, v in zip(hx, hy, hvis)
                   if v and abs(a) <= radius_deg * 1.5 and abs(b) <= radius_deg * 1.5]
        if len(horizon) < 2:
            horizon = []
        for name, bearing in (("N", 0), ("NE", 45), ("E", 90), ("SE", 135),
                              ("S", 180), ("SW", 225), ("W", 270), ("NW", 315)):
            cx, cy, cvis = proj.project_altaz([0.0], [float(bearing)])
            if in_frame(cx, cy, cvis)[0]:
                directions.append(ChartPoint(x=round(float(cx[0]), 4),
                                             y=round(float(cy[0]), 4),
                                             label=name, kind="direction"))

    return FinderChart(
        center_ra_deg=center_ra_deg, center_dec_deg=center_dec_deg,
        at_utc=when, orientation=orientation, radius_deg=radius_deg,
        limiting_mag=limit,
        center_alt_deg=float(proj.center_alt), center_az_deg=float(proj.center_az),
        stars=stars, lines=lines, objects=objects,
        horizon=horizon, directions=directions,
    )


def body_position(body: str, when: datetime) -> tuple[float, float]:
    """A solar-system body's J2000 RA and Dec in degrees at `when`.

    Astrometric, in the same frame as the star catalogue, so the planet sits
    among the chart's stars exactly where it is.
    """
    eph = load_ephemeris()
    t = eph.timescale.from_datetime(ensure_utc(when, field="when"))
    ra, dec, _ = eph.kernel["earth"].at(t).observe(eph.target(body)).radec()
    return float(ra._degrees), float(dec.degrees)
