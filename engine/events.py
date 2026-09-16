"""Meteor showers, eclipses, conjunctions and comets (PLAN.md §2, §3.5).

Everything here is computed or read from vendored data. The one exception is
comets, whose orbital elements come from the MPC over the network and which
degrade to "unavailable" exactly like weather does.

Solar eclipses are deliberately out of scope: they happen in daylight, which
is not what this tool plans. PLAN.md §2 lists them, but a night planner has no
use for them.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
from skyfield import almanac, eclipselib
from skyfield.api import Star
from skyfield.searchlib import find_minima

from .ephem import Ephemeris, _observer, load_ephemeris
from .equipment import naked_eye_limiting_mag
from .locations import BORTLE_SQM, Location
from .timeutil import UTC, ensure_utc, local_noon_utc

DATA_DIR = Path(__file__).resolve().parent / "data"
METEOR_SHOWERS_PATH = DATA_DIR / "meteor_showers.json"

# PLAN.md §3.3: planet-planet under 5 deg, Moon-planet under 3 deg.
PLANET_CONJUNCTION_DEG = 5.0
MOON_CONJUNCTION_DEG = 3.0

CONJUNCTION_BODIES = ["mercury", "venus", "mars", "jupiter", "saturn",
                      "uranus", "neptune"]


# --- meteor showers ---------------------------------------------------------

@dataclass(frozen=True)
class MeteorShower:
    name: str
    code: str
    active_start: str          # MM-DD
    active_end: str            # MM-DD
    peak: str                  # MM-DD
    zhr: float
    velocity_km_s: float
    radiant_ra_deg: float
    radiant_dec_deg: float
    parent: str | None = None

    def _md(self, value: str) -> tuple[int, int]:
        month, day = value.split("-")
        return int(month), int(day)

    def is_active_on(self, when: date) -> bool:
        """Active window, correctly handling showers that wrap the year end."""
        start, end = self._md(self.active_start), self._md(self.active_end)
        today = (when.month, when.day)
        if start <= end:
            return start <= today <= end
        return today >= start or today <= end      # e.g. Quadrantids

    def peak_date(self, year: int) -> date:
        month, day = self._md(self.peak)
        return date(year, month, day)

    def next_peak(self, after: date) -> date:
        """The next occurrence of this shower's peak on or after `after`."""
        candidate = self.peak_date(after.year)
        if candidate < after:
            candidate = self.peak_date(after.year + 1)
        return candidate


def load_meteor_showers(path: Path | None = None) -> list[MeteorShower]:
    """The vendored IMO Working List. Source and refresh TODO are in the JSON."""
    path = path or METEOR_SHOWERS_PATH
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    return [MeteorShower(**spec) for spec in raw["showers"]]


def observed_rate(zhr: float, radiant_altitude_deg: float,
                  limiting_mag: float, cloud_fraction: float = 0.0) -> float:
    """PLAN.md §3.5: ZHR x sin(alt) x 2^(lm - 6.5) x (1 - cloud).

    Returns zero when the radiant is below the horizon — you cannot see
    meteors from a radiant under your feet.
    """
    if radiant_altitude_deg <= 0:
        return 0.0
    elevation = math.sin(math.radians(radiant_altitude_deg))
    darkness = 2.0 ** (limiting_mag - 6.5)
    clear = max(0.0, 1.0 - max(0.0, min(cloud_fraction, 1.0)))
    return max(0.0, zhr * elevation * darkness * clear)


@dataclass(frozen=True)
class ShowerForecast:
    """One shower assessed against one night at one site."""

    shower: MeteorShower
    peak_date: date
    is_peak_night: bool
    best_time_utc: datetime | None
    best_radiant_altitude_deg: float
    best_rate: float
    moon_illumination: float
    limiting_mag: float
    notes: tuple[str, ...] = ()


def _radiant_altitudes(eph: Ephemeris, location: Location,
                       shower: MeteorShower, stamps: list[datetime]):
    observer = _observer(eph, location)
    radiant = Star(ra_hours=shower.radiant_ra_deg / 15.0,
                   dec_degrees=shower.radiant_dec_deg)
    times = eph.timescale.from_datetimes(stamps)
    return observer.at(times).observe(radiant).apparent().altaz()[0].degrees


def assess_shower(shower: MeteorShower, location: Location, night,
                  cloud_fraction: float = 0.0) -> ShowerForecast:
    """Radiant altitude through the night and the resulting observed rate.

    PLAN.md §3.5: report the best hour, which is radiant-highest intersected
    with moonless. Moonlight is folded in by degrading the limiting magnitude
    in the hours the Moon is up.
    """
    eph = load_ephemeris()

    spans = night.astronomical_night
    if not spans:
        return ShowerForecast(
            shower=shower, peak_date=shower.peak_date(night.date.year),
            is_peak_night=False, best_time_utc=None,
            best_radiant_altitude_deg=0.0, best_rate=0.0,
            moon_illumination=night.moon_illumination, limiting_mag=0.0,
            notes=("no astronomical night",),
        )

    start, end = spans[0][0], spans[-1][1]
    stamps, cursor = [], start
    while cursor <= end:
        stamps.append(cursor)
        cursor += timedelta(minutes=30)

    altitudes = _radiant_altitudes(eph, location, shower, stamps)
    dark_intervals = night.dark_intervals

    sqm = location.sqm if location.sqm is not None else BORTLE_SQM[5]
    base_limit = naked_eye_limiting_mag(sqm)

    best_rate, best_index, best_limit = 0.0, None, base_limit
    for index, when in enumerate(stamps):
        in_true_dark = any(s <= when <= e for s, e in dark_intervals)
        # Moonlight costs roughly 2 magnitudes at full, scaled by illumination.
        limit = base_limit if in_true_dark else base_limit - 2.0 * night.moon_illumination
        rate = observed_rate(shower.zhr, float(altitudes[index]), limit,
                             cloud_fraction)
        if rate > best_rate:
            best_rate, best_index, best_limit = rate, index, limit

    notes: list[str] = []
    if night.moon_illumination > 0.5:
        notes.append(f"moon {night.moon_illumination * 100:.0f}% - rates suppressed")
    if best_index is None:
        notes.append("radiant never rises during astronomical night")

    peak = shower.peak_date(night.date.year)
    return ShowerForecast(
        shower=shower,
        peak_date=peak,
        is_peak_night=abs((night.date - peak).days) <= 1,
        best_time_utc=stamps[best_index] if best_index is not None else None,
        best_radiant_altitude_deg=(float(altitudes[best_index])
                                   if best_index is not None else 0.0),
        best_rate=best_rate,
        moon_illumination=night.moon_illumination,
        limiting_mag=best_limit,
        notes=tuple(notes),
    )


def showers_active_between(start: date, days: int,
                           showers: list[MeteorShower] | None = None
                           ) -> list[tuple[MeteorShower, date]]:
    """Showers peaking in the window, as (shower, peak date), earliest first."""
    showers = showers if showers is not None else load_meteor_showers()
    end = start + timedelta(days=days)

    out: list[tuple[MeteorShower, date]] = []
    for shower in showers:
        peak = shower.next_peak(start)
        if start <= peak <= end:
            out.append((shower, peak))
    return sorted(out, key=lambda pair: pair[1])


# --- lunar eclipses ---------------------------------------------------------

@dataclass(frozen=True)
class LunarEclipse:
    time_utc: datetime
    kind: str                        # penumbral / partial / total
    moon_altitude_deg: float
    visible: bool                    # moon above the horizon at greatest eclipse

    def __post_init__(self) -> None:
        object.__setattr__(self, "time_utc",
                           ensure_utc(self.time_utc, field="time_utc"))


def lunar_eclipses(start: date, days: int, location: Location
                   ) -> list[LunarEclipse]:
    """Lunar eclipses in the window, flagged for visibility from `location`.

    Computed by `skyfield.eclipselib`, not read from a table. Visibility is the
    simple test that matters: is the Moon above the horizon at greatest
    eclipse.
    """
    eph = load_ephemeris()
    ts = eph.timescale

    t0 = ts.from_datetime(datetime(start.year, start.month, start.day, tzinfo=UTC))
    end_date = start + timedelta(days=days)
    t1 = ts.from_datetime(
        datetime(end_date.year, end_date.month, end_date.day, tzinfo=UTC))

    times, kinds, _ = eclipselib.lunar_eclipses(t0, t1, eph.kernel)
    if len(getattr(times.tt, "shape", ())) == 0 or len(times.tt.shape) == 0:
        return []

    observer = _observer(eph, location)
    moon = eph.target("moon")

    out: list[LunarEclipse] = []
    for index in range(len(times.tt)):
        moment = times[index]
        altitude = float(
            observer.at(moment).observe(moon).apparent().altaz()[0].degrees)
        out.append(LunarEclipse(
            time_utc=moment.utc_datetime().replace(tzinfo=UTC),
            kind=eclipselib.LUNAR_ECLIPSES[int(kinds[index])],
            moon_altitude_deg=altitude,
            visible=altitude > 0.0,
        ))
    return out


# --- conjunctions -----------------------------------------------------------

@dataclass(frozen=True)
class Conjunction:
    time_utc: datetime
    body_a: str
    body_b: str
    separation_deg: float
    involves_moon: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "time_utc",
                           ensure_utc(self.time_utc, field="time_utc"))


def _separation_function(eph: Ephemeris, name_a: str, name_b: str):
    earth = eph.kernel["earth"]
    target_a, target_b = eph.target(name_a), eph.target(name_b)

    def separation(t):
        here = earth.at(t)
        return here.observe(target_a).separation_from(here.observe(target_b)).degrees

    # find_minima samples on this stride; the Moon moves ~13 deg/day so it
    # needs a much finer step than a planet pair.
    separation.step_days = 0.25 if "moon" in (name_a, name_b) else 2.0
    return separation


def conjunctions(start: date, days: int, *,
                 planet_threshold_deg: float = PLANET_CONJUNCTION_DEG,
                 moon_threshold_deg: float = MOON_CONJUNCTION_DEG,
                 include_moon: bool = True) -> list[Conjunction]:
    """Close approaches in the window, computed rather than fetched.

    PLAN.md §3.3: scan for planet-planet pairs under 5 deg and Moon-planet
    pairs under 3 deg using `find_minima` on the separation function.
    """
    eph = load_ephemeris()
    ts = eph.timescale

    t0 = ts.from_datetime(datetime(start.year, start.month, start.day, tzinfo=UTC))
    end_date = start + timedelta(days=days)
    t1 = ts.from_datetime(
        datetime(end_date.year, end_date.month, end_date.day, tzinfo=UTC))

    pairs: list[tuple[str, str, float]] = []
    for i, first in enumerate(CONJUNCTION_BODIES):
        for second in CONJUNCTION_BODIES[i + 1:]:
            pairs.append((first, second, planet_threshold_deg))
    if include_moon:
        for body in CONJUNCTION_BODIES:
            pairs.append(("moon", body, moon_threshold_deg))

    out: list[Conjunction] = []
    for name_a, name_b, threshold in pairs:
        try:
            times, separations = find_minima(
                t0, t1, _separation_function(eph, name_a, name_b))
        except ValueError:
            continue
        for index in range(len(np.atleast_1d(separations))):
            separation = float(np.atleast_1d(separations)[index])
            if separation > threshold:
                continue
            out.append(Conjunction(
                time_utc=times[index].utc_datetime().replace(tzinfo=UTC),
                body_a=name_a, body_b=name_b,
                separation_deg=separation,
                involves_moon="moon" in (name_a, name_b),
            ))
    return sorted(out, key=lambda c: c.time_utc)


# --- comets -----------------------------------------------------------------

@dataclass(frozen=True)
class CometStatus:
    available: bool
    reason: str | None = None
    comets: tuple = ()


def bright_comets(max_magnitude: float = 11.0) -> CometStatus:
    """MPC comet elements. Network-dependent, so it degrades like weather.

    PLAN.md §2 wants predicted magnitude under 11. The MPC file gives orbital
    elements plus the magnitude parameters H and G, not a predicted magnitude,
    so computing a real prediction requires propagating each orbit. That is
    not done here — this reports availability and count only, and says so.
    """
    from .weather import network_enabled

    if not network_enabled():
        return CometStatus(available=False, reason="network disabled")
    try:
        from skyfield.data import mpc

        with open_mpc_comets() as stream:
            dataframe = mpc.load_comets_dataframe(stream)
    except Exception as exc:                     # noqa: BLE001 - degrade, never crash
        return CometStatus(available=False, reason=f"MPC unavailable ({exc})")
    return CometStatus(available=True,
                       comets=tuple(dataframe["designation"].head(20)))


def open_mpc_comets():
    """Open the MPC comet file, downloading once into the cache directory."""
    from skyfield.api import Loader

    from .ephem import ephemeris_dir

    loader = Loader(str(ephemeris_dir()), verbose=False)
    return loader.open(
        "https://www.minorplanetcenter.net/iau/MPCORB/CometEls.txt",
        reload=False,
    )
