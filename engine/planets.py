"""Planets: apparitions, oppositions, elongations, next visibility (PLAN.md §3.4).

For each planet we report where it sits in its apparition, not just tonight's
altitude — "past opposition, window closing" is the fact that decides whether
to bother. And when a planet is not observable tonight, PLAN.md §3.4 insists we
say *when* it will be, rather than leaving a blank.

Magnitudes come from `skyfield.magnitudelib`, which implements the Mallama &
Hilton (2018) photometric model. Apparent diameters use IAU equatorial radii;
the constants are listed with their source below.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np
from skyfield import almanac
from skyfield.magnitudelib import planetary_magnitude
from skyfield.searchlib import find_maxima

from .ephem import (Ephemeris, _observer, ephemeris_key, load_ephemeris,
                    night_window)
from .locations import Location
from .timeutil import UTC, ensure_utc, local_noon_utc

# Equatorial radii in km. Source: IAU Working Group on Cartographic Coordinates
# and Rotational Elements, 2015 report (Archinal et al. 2018).
PLANET_RADIUS_KM = {
    "mercury": 2439.7,
    "venus": 6051.8,
    "mars": 3396.19,
    "jupiter": 71492.0,
    "saturn": 60268.0,
    "uranus": 25559.0,
    "neptune": 24764.0,
}

INNER_PLANETS = {"mercury", "venus"}
OUTER_PLANETS = {"mars", "jupiter", "saturn", "uranus", "neptune"}
ALL_PLANETS = ["mercury", "venus", "mars", "jupiter", "saturn", "uranus",
               "neptune"]

AU_KM = 149_597_870.7

# Below this solar elongation a planet is lost in twilight whatever its altitude.
TOO_NEAR_SUN_DEG = 15.0

DEFAULT_MIN_ALTITUDE_DEG = 25.0
DEFAULT_MIN_HOURS = 1.0


@dataclass(frozen=True)
class PlanetReport:
    """One planet assessed against one night."""

    name: str
    observable: bool

    peak_altitude_deg: float
    peak_time_utc: datetime | None
    transit_time_utc: datetime | None
    hours_above_floor: float

    magnitude: float | None
    apparent_diameter_arcsec: float | None
    illuminated_fraction: float | None
    distance_au: float | None
    elongation_deg: float | None

    trend: str                              # apparition label, see _trend()
    days_to_event: int | None               # to opposition or max elongation
    event_name: str | None
    event_date: date | None

    ring_tilt_deg: float | None = None      # Saturn only
    visibility: "Visibility | None" = None  # set when not observable tonight
    notes: tuple[str, ...] = ()

    @property
    def next_visible_date(self) -> date | None:
        return self.visibility.when if self.visibility else None

    def __post_init__(self) -> None:
        for field_name in ("peak_time_utc", "transit_time_utc"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name,
                                   ensure_utc(value, field=field_name))


def apparent_diameter_arcsec(planet: str, distance_au: float) -> float | None:
    """Angular diameter of the disc at `distance_au`."""
    radius = PLANET_RADIUS_KM.get(planet)
    if radius is None or distance_au <= 0:
        return None
    distance_km = distance_au * AU_KM
    return 2.0 * math.degrees(math.atan(radius / distance_km)) * 3600.0


def saturn_ring_tilt_deg(eph: Ephemeris, when: datetime) -> float:
    """Saturn's ring opening angle as seen from Earth, in degrees.

    PLAN.md §3.4 singles this out as the thing that visibly changes year to
    year. Computed as the angle between the line of sight and Saturn's ring
    plane, using the IAU pole of Saturn (RA 40.589 deg, Dec 83.537 deg, J2000
    — Archinal et al. 2018). Sign gives which face is tilted toward us;
    magnitude near zero means the rings are edge-on.
    """
    when = ensure_utc(when, field="when")
    t = eph.timescale.from_datetime(when)

    earth = eph.kernel["earth"]
    saturn = eph.target("saturn")
    position = earth.at(t).observe(saturn).apparent()

    # Unit vector from Earth to Saturn.
    vector = position.position.au
    length = float(np.linalg.norm(vector))
    if length == 0:
        return 0.0
    direction = np.asarray(vector, dtype=float) / length

    pole_ra = math.radians(40.589)
    pole_dec = math.radians(83.537)
    pole = np.array([
        math.cos(pole_dec) * math.cos(pole_ra),
        math.cos(pole_dec) * math.sin(pole_ra),
        math.sin(pole_dec),
    ])

    # Angle between the line of sight and the ring plane = 90 - angle to pole.
    cosine = float(np.clip(np.dot(direction, pole), -1.0, 1.0))
    return 90.0 - math.degrees(math.acos(cosine))


def _elongation_function(eph: Ephemeris, planet: str):
    """Sun-Earth-planet angle over time, for opposition/elongation searches."""
    earth = eph.kernel["earth"]
    sun, target = eph.target("sun"), eph.target(planet)

    def elongation(t):
        here = earth.at(t)
        return here.observe(sun).separation_from(here.observe(target)).degrees

    elongation.step_days = 3.0
    return elongation


def next_apparition_event(eph: Ephemeris, planet: str, after: date,
                          search_days: int = 900
                          ) -> tuple[str, date, float] | None:
    """Next opposition (outer) or greatest elongation (inner).

    Both are maxima of the solar elongation function: an outer planet peaks at
    180 deg (opposition), an inner one at its greatest elongation. So a single
    `find_maxima` search covers both, and only the label differs.
    """
    ts = eph.timescale
    t0 = ts.from_datetime(datetime(after.year, after.month, after.day, tzinfo=UTC))
    end = after + timedelta(days=search_days)
    t1 = ts.from_datetime(datetime(end.year, end.month, end.day, tzinfo=UTC))

    try:
        times, values = find_maxima(t0, t1, _elongation_function(eph, planet))
    except ValueError:
        return None
    if len(np.atleast_1d(values)) == 0:
        return None

    when = times[0].utc_datetime().replace(tzinfo=UTC).date()
    value = float(np.atleast_1d(values)[0])
    label = "greatest elongation" if planet in INNER_PLANETS else "opposition"
    return label, when, value


def _trend(planet: str, elongation_deg: float | None,
           days_to_event: int | None) -> str:
    """A plain-language apparition label (PLAN.md §3.4)."""
    if elongation_deg is not None and elongation_deg < TOO_NEAR_SUN_DEG:
        return "too near the Sun"
    if days_to_event is None:
        return "unknown"
    if planet in OUTER_PLANETS:
        if days_to_event <= 30:
            return "near opposition - optimal now"
        if days_to_event <= 180:
            return "approaching opposition - improving"
        return "past opposition - window closing"
    if days_to_event <= 21:
        return "near greatest elongation - best now"
    return "approaching greatest elongation"


def _altitudes(eph: Ephemeris, location: Location, planet: str,
               stamps: list[datetime]):
    observer = _observer(eph, location)
    times = eph.timescale.from_datetimes(stamps)
    return observer.at(times).observe(eph.target(planet)).apparent().altaz()[0].degrees


def observing_span(planet: str, window) -> tuple[datetime, datetime] | None:
    """The window in which this planet is worth looking for.

    PLAN.md §3.4 says to search during astronomical night. That is right for
    the outer planets and *wrong* for Mercury and Venus: at mid-northern
    latitudes an inner planet almost never clears a sensible altitude floor
    with the Sun more than 18 deg down, because its elongation caps how far
    from the Sun it can ever get. Applying the astronomical-night rule to
    Venus returns "never observable", which is plainly false for the
    brightest planet in the sky.

    So inner planets are assessed from sunset to sunrise — twilight included,
    which is when you actually observe them — and flagged as twilight objects.
    Outer planets keep the astronomical-night rule.
    """
    if planet in INNER_PLANETS:
        if window.sunset_utc and window.sunrise_utc:
            return window.sunset_utc, window.sunrise_utc
        return None
    spans = window.astronomical_night
    if not spans:
        return None
    return spans[0][0], spans[-1][1]


@dataclass(frozen=True)
class Visibility:
    """Result of a forward search for when a planet becomes observable.

    `best_altitude_deg` matters when `when` is None: a floor of 25 deg is
    simply unreachable for Mercury from mid-northern latitudes, and reporting
    "no return within a year" alone reads as though the planet has vanished.
    Saying it tops out at 19 deg in October is the useful answer.
    """

    when: date | None
    best_altitude_deg: float
    best_date: date | None

    @property
    def floor_unreachable(self) -> bool:
        return self.when is None and self.best_altitude_deg > 0


def next_visibility(planet: str, location: Location, after: date, *,
                    min_altitude_deg: float = DEFAULT_MIN_ALTITUDE_DEG,
                    min_hours: float = DEFAULT_MIN_HOURS,
                    search_days: int = 400,
                    stride_days: int = 5) -> Visibility:
    """The next night this planet clears the floor for long enough.

    PLAN.md §3.4: "If a planet isn't observable tonight, say when it will be."
    Scanned on a stride rather than nightly — planetary visibility changes over
    weeks, and a 5-day step finds the returning window quickly enough while
    keeping the search affordable.
    """
    eph = load_ephemeris()
    best_altitude, best_date = -90.0, None

    for offset in range(stride_days, search_days, stride_days):
        candidate = after + timedelta(days=offset)
        window = night_window(candidate, location)
        span = observing_span(planet, window)
        if span is None:
            continue

        start, end = span
        stamps, cursor = [], start
        while cursor <= end:
            stamps.append(cursor)
            cursor += timedelta(minutes=30)
        if len(stamps) < 2:
            continue

        altitudes = np.asarray(_altitudes(eph, location, planet, stamps))
        peak = float(altitudes.max())
        if peak > best_altitude:
            best_altitude, best_date = peak, candidate

        above = int(np.sum(altitudes >= min_altitude_deg))
        if above * 0.5 >= min_hours:
            return Visibility(when=candidate, best_altitude_deg=peak,
                              best_date=candidate)
    return Visibility(when=None, best_altitude_deg=best_altitude,
                      best_date=best_date)


def report_planet(planet: str, location: Location, window, *,
                  min_altitude_deg: float = DEFAULT_MIN_ALTITUDE_DEG,
                  find_next: bool = True) -> PlanetReport:
    """Full apparition report for one planet on one night."""
    eph = load_ephemeris()
    planet = planet.strip().lower()

    span = observing_span(planet, window)
    if span is None:
        return PlanetReport(
            name=planet, observable=False, peak_altitude_deg=-90.0,
            peak_time_utc=None, transit_time_utc=None, hours_above_floor=0.0,
            magnitude=None, apparent_diameter_arcsec=None,
            illuminated_fraction=None, distance_au=None, elongation_deg=None,
            trend="unknown", days_to_event=None, event_name=None,
            event_date=None, notes=("no astronomical night",),
        )

    start, end = span
    stamps, cursor = [], start
    while cursor <= end:
        stamps.append(cursor)
        cursor += timedelta(minutes=30)

    altitudes = np.asarray(_altitudes(eph, location, planet, stamps))
    peak_index = int(np.argmax(altitudes))
    peak_altitude = float(altitudes[peak_index])
    hours_above = float(np.sum(altitudes >= min_altitude_deg)) * 0.5

    # Physical quantities sampled at peak altitude, the best moment to observe.
    moment = eph.timescale.from_datetime(stamps[peak_index])
    earth = eph.kernel["earth"]
    astrometric = earth.at(moment).observe(eph.target(planet))
    apparent = astrometric.apparent()

    distance = float(astrometric.distance().au)
    try:
        magnitude = float(planetary_magnitude(astrometric))
    except (ValueError, KeyError):
        magnitude = None

    sun_position = earth.at(moment).observe(eph.target("sun"))
    elongation = float(apparent.separation_from(sun_position.apparent()).degrees)
    illumination = float(
        almanac.fraction_illuminated(eph.kernel, ephemeris_key(planet), moment))

    event = next_apparition_event(eph, planet, window.date)
    event_name, event_date, days_to_event = None, None, None
    if event is not None:
        event_name, event_date, _ = event
        days_to_event = (event_date - window.date).days

    observable = peak_altitude >= min_altitude_deg and hours_above >= DEFAULT_MIN_HOURS
    notes: list[str] = []
    if planet in INNER_PLANETS:
        notes.append("twilight object - assessed sunset to sunrise")
    if elongation < TOO_NEAR_SUN_DEG:
        notes.append(f"only {elongation:.0f} deg from the Sun")
        observable = False

    return PlanetReport(
        name=planet,
        observable=observable,
        peak_altitude_deg=peak_altitude,
        peak_time_utc=stamps[peak_index],
        transit_time_utc=stamps[peak_index],
        hours_above_floor=hours_above,
        magnitude=magnitude,
        apparent_diameter_arcsec=apparent_diameter_arcsec(planet, distance),
        illuminated_fraction=illumination,
        distance_au=distance,
        elongation_deg=elongation,
        trend=_trend(planet, elongation, days_to_event),
        days_to_event=days_to_event,
        event_name=event_name,
        event_date=event_date,
        ring_tilt_deg=(saturn_ring_tilt_deg(eph, stamps[peak_index])
                       if planet == "saturn" else None),
        visibility=(next_visibility(planet, location, window.date,
                                    min_altitude_deg=min_altitude_deg)
                    if (not observable and find_next) else None),
        notes=tuple(notes),
    )


def report_all(location: Location, window, *,
               min_altitude_deg: float = DEFAULT_MIN_ALTITUDE_DEG,
               find_next: bool = True) -> list[PlanetReport]:
    """Every planet, observable ones first and brightest within that."""
    reports = [
        report_planet(planet, location, window,
                      min_altitude_deg=min_altitude_deg, find_next=find_next)
        for planet in ALL_PLANETS
    ]
    return sorted(
        reports,
        key=lambda r: (not r.observable,
                       r.magnitude if r.magnitude is not None else 99.0),
    )
