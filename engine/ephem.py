"""Skyfield setup, night windows, and alt/az sampling.

Conventions worth knowing before comparing output against a published table:

* Rise/set for the Sun and Moon use Skyfield's standard refraction
  (-0.5667 deg) plus the body's apparent radius, i.e. upper limb touching the
  horizon. This matches the USNO / timeanddate convention.
* Twilight boundaries are sun *center* altitudes of -6, -12, -18 deg, which is
  the standard definition and excludes refraction.
* The moon-down test behind the true dark window uses that same rise/set
  convention, so "moon below horizon" means the upper limb has set.
"""

from __future__ import annotations

import functools
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from skyfield import almanac
from skyfield.api import Loader, wgs84

from .locations import Location
from .timeutil import UTC, ensure_utc, local_midnight_utc, local_noon_utc

EPHEMERIS_FILE = "de440s.bsp"

# DE440s is ~32 MB. Fetched once into the cache dir, then never again.
_MIN_PLAUSIBLE_BSP_BYTES = 1_000_000

# Regimes reported by skyfield.almanac.dark_twilight_day, darkest first:
#   0 dark (sun < -18)   1 astronomical twilight (-18..-12)
#   2 nautical (-12..-6) 3 civil (-6..sunset)   4 day
_DARK = 0
_ASTRONOMICAL = 1
_NAUTICAL = 2

# Standard refracted horizon for the "is it up right now" test.
_HORIZON_DEG = -0.5667

_BODY_ALIASES = {
    "sun": "sun",
    "moon": "moon",
    "mercury": "mercury",
    "venus": "venus",
    "mars": "mars barycenter",
    "jupiter": "jupiter barycenter",
    "saturn": "saturn barycenter",
    "uranus": "uranus barycenter",
    "neptune": "neptune barycenter",
    "pluto": "pluto barycenter",
}

Interval = tuple[datetime, datetime]


class EphemerisMissingError(RuntimeError):
    """Raised when the ephemeris is absent and downloading is disallowed."""


def ephemeris_dir() -> Path:
    """Cache directory for the vendored ephemeris.

    Override with ASTRO_EPHEM_DIR. Otherwise LOCALAPPDATA on Windows,
    XDG_CACHE_HOME (or ~/.cache) elsewhere.
    """
    override = os.environ.get("ASTRO_EPHEM_DIR")
    if override:
        return Path(override).expanduser()

    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "astro-night-planner" / "ephemeris"


def ephemeris_path() -> Path:
    """Full path to the cached DE440s kernel."""
    return ephemeris_dir() / EPHEMERIS_FILE


def ephemeris_is_cached() -> bool:
    """True if a plausible DE440s kernel is already on disk."""
    path = ephemeris_path()
    return path.exists() and path.stat().st_size > _MIN_PLAUSIBLE_BSP_BYTES


def ephemeris_key(body: str) -> str:
    """Our body name -> the kernel's own key.

    DE440s stores the outer planets as barycenters ("mars barycenter", not
    "mars"), and some Skyfield APIs take the key string rather than a resolved
    target, so the mapping has to be reachable by name too.
    """
    key = _BODY_ALIASES.get(body.strip().lower())
    if key is None:
        known = ", ".join(sorted(_BODY_ALIASES))
        raise KeyError(f"unknown body {body!r}; known: {known}")
    return key


@dataclass(frozen=True)
class Ephemeris:
    """The loaded kernel plus a timescale, passed around together."""

    kernel: object
    timescale: object

    def target(self, body: str):
        """Resolve a body name to a Skyfield ephemeris target."""
        return self.kernel[ephemeris_key(body)]


@functools.lru_cache(maxsize=2)
def load_ephemeris(allow_download: bool | None = None) -> Ephemeris:
    """Load DE440s, downloading it once if the cache is empty.

    This is the only I/O side effect in `engine/`. After the first successful
    call the file is reused verbatim; a cached kernel is never re-fetched.

    Set ASTRO_EPHEM_NO_DOWNLOAD=1 (or pass allow_download=False) to make a
    missing cache a hard error instead of a network fetch.
    """
    if allow_download is None:
        allow_download = os.environ.get("ASTRO_EPHEM_NO_DOWNLOAD", "").lower() not in {
            "1", "true", "yes",
        }

    directory = ephemeris_dir()
    directory.mkdir(parents=True, exist_ok=True)

    if not ephemeris_is_cached() and not allow_download:
        raise EphemerisMissingError(
            f"{ephemeris_path()} is missing and downloading is disabled. "
            f"Place {EPHEMERIS_FILE} there, or allow the one-time fetch."
        )

    loader = Loader(str(directory), verbose=False)
    # timescale(builtin=True) uses data shipped inside Skyfield: no network.
    timescale = loader.timescale(builtin=True)
    kernel = loader(EPHEMERIS_FILE)
    return Ephemeris(kernel=kernel, timescale=timescale)


def _site(location: Location):
    """Topocentric site on the WGS84 geoid."""
    return wgs84.latlon(
        latitude_degrees=location.lat,
        longitude_degrees=location.lon,
        elevation_m=location.elevation_m,
    )


def _observer(eph: Ephemeris, location: Location):
    """Earth + site: the vantage point for all apparent positions."""
    return eph.kernel["earth"] + _site(location)


def _to_datetimes(times) -> list[datetime]:
    """Skyfield Time (scalar or array) -> list of tz-aware UTC datetimes."""
    if getattr(times.tt, "shape", ()) == ():
        return [times.utc_datetime().replace(tzinfo=UTC)]
    return [d.replace(tzinfo=UTC) for d in times.utc_datetime()]


def _intersect(a: list[Interval], b: list[Interval]) -> list[Interval]:
    """Intersection of two lists of sorted, non-overlapping intervals."""
    out: list[Interval] = []
    i = j = 0
    while i < len(a) and j < len(b):
        start = max(a[i][0], b[j][0])
        end = min(a[i][1], b[j][1])
        if start < end:
            out.append((start, end))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def _complement(spans: list[Interval], t0: datetime, t1: datetime) -> list[Interval]:
    """Everything in [t0, t1] not covered by `spans`."""
    out: list[Interval] = []
    cursor = t0
    for start, end in spans:
        if start > cursor:
            out.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < t1:
        out.append((cursor, t1))
    return out


def _twilight_states(eph: Ephemeris, location: Location, t0, t1):
    """Sun-regime transitions across the span.

    Returns (times, states) where `states` has one more entry than `times`:
    states[i] holds before times[i], states[i+1] after it.
    """
    fn = almanac.dark_twilight_day(eph.kernel, _site(location))
    times, values = almanac.find_discrete(t0, t1, fn)
    initial = int(fn(t0))
    return _to_datetimes(times), [initial] + [int(v) for v in values]


def _crossing(times, states, regime: int, *, ascending: bool) -> datetime | None:
    """When the sun enters `regime` from above (dusk) or leaves it (dawn)."""
    for index, when in enumerate(times):
        before, after = states[index], states[index + 1]
        if ascending and before == regime and after > regime:
            return when
        if not ascending and after == regime and before > regime:
            return when
    return None


def _rise_set(eph: Ephemeris, location: Location, body: str, t0, t1):
    """First rise and first set of `body` within the span, or None each.

    Across a span of about a day the Sun and Moon each rise and set at most
    once, so "first" is unambiguous here.
    """
    observer = _observer(eph, location)
    target = eph.target(body)

    rise_times, rise_ok = almanac.find_risings(observer, target, t0, t1)
    set_times, set_ok = almanac.find_settings(observer, target, t0, t1)

    def _first(times, flags):
        for when, ok in zip(_to_datetimes(times), flags):
            if bool(ok):
                return when
        return None

    return _first(rise_times, rise_ok), _first(set_times, set_ok)


def _body_up_intervals(eph: Ephemeris, location: Location, body: str,
                       t0, t1) -> list[Interval]:
    """Spans within [t0, t1] where `body` is above the standard horizon."""
    observer = _observer(eph, location)
    target = eph.target(body)

    rise_times, rise_ok = almanac.find_risings(observer, target, t0, t1)
    set_times, set_ok = almanac.find_settings(observer, target, t0, t1)

    rises = [w for w, ok in zip(_to_datetimes(rise_times), rise_ok) if bool(ok)]
    sets = [w for w, ok in zip(_to_datetimes(set_times), set_ok) if bool(ok)]

    start_dt, end_dt = _to_datetimes(t0)[0], _to_datetimes(t1)[0]

    # Altitude at the start of the span decides whether we open mid-interval.
    alt = observer.at(t0).observe(target).apparent().altaz()[0].degrees
    open_at: datetime | None = start_dt if float(alt) > _HORIZON_DEG else None

    intervals: list[Interval] = []
    for when, kind in sorted([(w, "rise") for w in rises] + [(w, "set") for w in sets]):
        if kind == "rise" and open_at is None:
            open_at = when
        elif kind == "set" and open_at is not None:
            intervals.append((open_at, when))
            open_at = None
    if open_at is not None:
        intervals.append((open_at, end_dt))
    return intervals


@dataclass(frozen=True)
class NightWindow:
    """Everything about the night of `date` at `location`.

    Twilight fields are optional on purpose: at high latitudes the sun may
    never reach -18 deg, and returning None beats inventing a timestamp.

    `dark_intervals` is a list, not a single pair: a moon that rises partway
    through the night splits true dark in two, and collapsing that into one
    span would be a real correctness bug.
    """

    date: date
    location: Location

    sunset_utc: datetime | None
    sunrise_utc: datetime | None

    civil_dusk_utc: datetime | None          # sun crosses  -6 deg, descending
    nautical_dusk_utc: datetime | None       # sun crosses -12 deg, descending
    astronomical_dusk_utc: datetime | None   # sun crosses -18 deg, descending
    astronomical_dawn_utc: datetime | None   # sun crosses -18 deg, ascending
    nautical_dawn_utc: datetime | None
    civil_dawn_utc: datetime | None

    # Moon rise/set follow the published-almanac convention: events falling in
    # the *local calendar day* of `date`, local midnight to local midnight.
    # That is what USNO and timeanddate print, so these are the comparable
    # numbers. The dark window below does not depend on them — it uses its own
    # moon-up intervals across the night span, so a moon that rose before noon
    # is still handled correctly.
    moonrise_utc: datetime | None
    moonset_utc: datetime | None
    moon_up_at_dusk: bool                    # already up when true dark begins
    moon_waxing: bool
    moon_illumination: float                 # 0-1, at the midpoint of the night

    astronomical_night: list[Interval]       # sun < -18 deg
    dark_intervals: list[Interval]           # sun < -18 deg AND moon down

    _DATETIME_FIELDS = (
        "sunset_utc", "sunrise_utc", "civil_dusk_utc", "nautical_dusk_utc",
        "astronomical_dusk_utc", "astronomical_dawn_utc", "nautical_dawn_utc",
        "civil_dawn_utc", "moonrise_utc", "moonset_utc",
    )

    def __post_init__(self) -> None:
        for name in self._DATETIME_FIELDS:
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, ensure_utc(value, field=name))
        for label in ("astronomical_night", "dark_intervals"):
            spans = [
                (ensure_utc(s, field=label), ensure_utc(e, field=label))
                for s, e in getattr(self, label)
            ]
            object.__setattr__(self, label, spans)

    @staticmethod
    def _hours(spans: list[Interval]) -> float:
        return sum((e - s).total_seconds() for s, e in spans) / 3600.0

    @property
    def astronomical_night_hours(self) -> float:
        return self._hours(self.astronomical_night)

    @property
    def dark_hours(self) -> float:
        """Hours of true dark: sun below -18 deg with the moon down."""
        return self._hours(self.dark_intervals)


@functools.lru_cache(maxsize=512)
def night_window(day: date, location: Location) -> NightWindow:
    """Compute the full night of `day` at `location`.

    The night of D runs sunset(D) -> sunrise(D+1). The search span is local
    noon on D to local noon on D+1, which brackets exactly one night.

    Memoized: it is a pure function of (date, location), and the planet
    next-visibility scan asks for the same nights once per planet.
    """
    eph = load_ephemeris()
    ts = eph.timescale

    span_start = local_noon_utc(day, location.tz)
    span_end = local_noon_utc(day + timedelta(days=1), location.tz)
    t0 = ts.from_datetime(span_start)
    t1 = ts.from_datetime(span_end)

    times, states = _twilight_states(eph, location, t0, t1)

    # Entering the nautical regime descending means the sun just passed -6,
    # i.e. civil twilight ended; leaving it ascending is the morning twin.
    civil_dusk = _crossing(times, states, _NAUTICAL, ascending=False)
    civil_dawn = _crossing(times, states, _NAUTICAL, ascending=True)
    nautical_dusk = _crossing(times, states, _ASTRONOMICAL, ascending=False)
    nautical_dawn = _crossing(times, states, _ASTRONOMICAL, ascending=True)
    astro_dusk = _crossing(times, states, _DARK, ascending=False)
    astro_dawn = _crossing(times, states, _DARK, ascending=True)

    sunrise, sunset = _rise_set(eph, location, "sun", t0, t1)

    # Moon events on the local calendar day, per almanac convention.
    day_start = local_midnight_utc(day, location.tz)
    day_end = local_midnight_utc(day + timedelta(days=1), location.tz)
    moonrise, moonset = _rise_set(
        eph, location, "moon",
        ts.from_datetime(day_start), ts.from_datetime(day_end),
    )

    # Astronomical night, clipped to the search span at either end.
    if astro_dusk and astro_dawn:
        night_spans = [(astro_dusk, astro_dawn)]
    elif astro_dusk:
        night_spans = [(astro_dusk, span_end)]
    elif astro_dawn:
        night_spans = [(span_start, astro_dawn)]
    elif states and all(s == _DARK for s in states):
        night_spans = [(span_start, span_end)]   # polar night
    else:
        night_spans = []                          # sun never reaches -18

    moon_up = _body_up_intervals(eph, location, "moon", t0, t1)
    moon_down = _complement(moon_up, span_start, span_end)
    true_dark = _intersect(night_spans, moon_down)

    # Illumination sampled at the midpoint of the night: the figure that
    # matters for planning, rather than an arbitrary midnight value.
    if sunset and sunrise:
        mid = sunset + (sunrise - sunset) / 2
    else:
        mid = span_start + (span_end - span_start) / 2
    illum = float(
        almanac.fraction_illuminated(eph.kernel, "moon", ts.from_datetime(mid))
    )
    # Waxing or waning from the sign of the change, not from a rise/set
    # ordering heuristic that breaks near new and full moon.
    later = float(
        almanac.fraction_illuminated(
            eph.kernel, "moon", ts.from_datetime(mid + timedelta(hours=6))
        )
    )
    waxing = later > illum

    dusk = night_spans[0][0] if night_spans else None
    up_at_dusk = bool(dusk and any(s <= dusk < e for s, e in moon_up))

    return NightWindow(
        date=day,
        location=location,
        sunset_utc=sunset,
        sunrise_utc=sunrise,
        civil_dusk_utc=civil_dusk,
        nautical_dusk_utc=nautical_dusk,
        astronomical_dusk_utc=astro_dusk,
        astronomical_dawn_utc=astro_dawn,
        nautical_dawn_utc=nautical_dawn,
        civil_dawn_utc=civil_dawn,
        moonrise_utc=moonrise,
        moonset_utc=moonset,
        moon_up_at_dusk=up_at_dusk,
        moon_waxing=waxing,
        moon_illumination=illum,
        astronomical_night=night_spans,
        dark_intervals=true_dark,
    )


@dataclass(frozen=True)
class AltAzSeries:
    """Sampled apparent altitude/azimuth for one body."""

    body: str
    location: Location
    times_utc: list[datetime]
    alt_deg: list[float]
    az_deg: list[float]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "times_utc",
            [ensure_utc(t, field="times_utc") for t in self.times_utc],
        )

    def __len__(self) -> int:
        return len(self.times_utc)

    @property
    def peak_altitude(self) -> float | None:
        return max(self.alt_deg) if self.alt_deg else None


def altaz_series(body: str, location: Location, start: datetime, end: datetime,
                 step: timedelta = timedelta(minutes=15)) -> AltAzSeries:
    """Sample apparent alt/az for `body` from `start` to `end` every `step`.

    `start` and `end` must be tz-aware; both are normalized to UTC on entry.
    Sampling includes `start`, and includes `end` when it lands on a step
    boundary.
    """
    start = ensure_utc(start, field="start")
    end = ensure_utc(end, field="end")
    if end < start:
        raise ValueError(f"end {end} precedes start {start}")
    if step.total_seconds() <= 0:
        raise ValueError(f"step must be positive, got {step}")

    eph = load_ephemeris()
    observer = _observer(eph, location)
    target = eph.target(body)

    stamps: list[datetime] = []
    cursor = start
    while cursor <= end:
        stamps.append(cursor)
        cursor += step

    times = eph.timescale.from_datetimes(stamps)
    alt, az, _ = observer.at(times).observe(target).apparent().altaz()

    return AltAzSeries(
        body=body,
        location=location,
        times_utc=stamps,
        alt_deg=[float(v) for v in alt.degrees],
        az_deg=[float(v) for v in az.degrees],
    )
