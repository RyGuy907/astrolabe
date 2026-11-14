"""Weather clients: Open-Meteo + 7Timer, cached and blended.

**One of the two modules in `engine/` permitted to touch the network**
(the other is `geocode.py`).
PLAN.md §1: the astronomy must work with no network at all; weather and live
feeds may hit it, and every one of them must degrade to "unknown" rather than
crash. Nothing here ever raises on a network failure — callers get a Forecast
with `available=False` and a reason.

Two traps from PLAN.md §7 are handled explicitly:

* **7Timer index inversion.** Its seeing/transparency/cloudcover indices run
  *lower = better*. Getting this backwards produces plausible-looking garbage,
  so every index is converted through `_index_to_quality` at the boundary and
  never used raw.
* **Forecast horizon mismatch.** 7Timer is 72 h, Open-Meteo about 16 days. A
  request past either horizon yields an explicit "no forecast" state for that
  source, not a silently degraded score.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, NamedTuple

from .locations import Location
from .ratelimit import OPEN_METEO, SEVENTIMER, RateLimited, Upstream
from .timeutil import UTC, ensure_utc, now_utc

log = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
SEVENTIMER_URL = "https://www.7timer.info/bin/api.pl"

# PLAN.md §2: cache aggressively, one fetch per location per model run.
CACHE_TTL = timedelta(hours=3)
# How old a cached forecast may be and still stand in when a refresh fails.
# A day-old forecast for tonight is still far better than none, and it is
# labelled as old; past a day it is closer to a guess. Rows older than this
# are useless, so they are also what the cache prunes.
STALE_LIMIT = timedelta(hours=24)
REQUEST_TIMEOUT = 20

# Published horizons. Past these, a source returns no data rather than a guess.
OPEN_METEO_HORIZON = timedelta(days=16)
SEVENTIMER_HORIZON = timedelta(hours=72)

USER_AGENT = "astrolabe/0.1 (personal observing planner)"

# Open-Meteo counts a request for more than 10 variables or 14 days as more
# than one: 12 variables over 16 days is about 1.4. Budgeted as 2.
OPEN_METEO_FORECAST_COST = 2

OPEN_METEO_FIELDS = [
    "cloud_cover", "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high",
    "temperature_2m", "dew_point_2m", "relative_humidity_2m",
    "wind_speed_10m", "wind_gusts_10m", "visibility",
    "precipitation_probability", "is_day",
]


class NetworkDisabled(RuntimeError):
    """Raised internally when network access is switched off. Never escapes."""


def network_enabled() -> bool:
    """False when ASTRO_NO_NETWORK is set, for offline testing."""
    import os

    return os.environ.get("ASTRO_NO_NETWORK", "").lower() not in {"1", "true", "yes"}


# --- data model -------------------------------------------------------------

@dataclass(frozen=True)
class HourlyConditions:
    """Conditions at one instant. Every field is optional — sources differ."""

    time_utc: datetime

    cloud_cover: float | None = None          # percent, total
    cloud_low: float | None = None
    cloud_mid: float | None = None
    cloud_high: float | None = None
    temperature_c: float | None = None
    dew_point_c: float | None = None
    humidity_pct: float | None = None
    wind_speed_kmh: float | None = None
    wind_gust_kmh: float | None = None
    visibility_m: float | None = None
    precipitation_probability: float | None = None

    # Normalized 0-1 where 1 is best. Never the raw 7Timer index.
    seeing_quality: float | None = None
    transparency_quality: float | None = None
    seeing_estimated: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "time_utc",
                           ensure_utc(self.time_utc, field="time_utc"))

    @property
    def dew_point_spread_c(self) -> float | None:
        """Temperature minus dew point. Under 2 C and the optics will fog."""
        if self.temperature_c is None or self.dew_point_c is None:
            return None
        return self.temperature_c - self.dew_point_c


@dataclass(frozen=True)
class Forecast:
    """Blended hourly forecast, or an explicit statement that there is none."""

    location: Location
    hours: list[HourlyConditions] = field(default_factory=list)
    available: bool = False
    unavailable_reason: str | None = None
    sources: tuple[str, ...] = ()
    seeing_estimated: bool = False
    # Set when a refresh failed and a cached copy older than CACHE_TTL stood in:
    # when that copy was fetched, and why it could not be refreshed.
    stale_since: datetime | None = None
    stale_reason: str | None = None
    # Set when Open-Meteo could not be used and the forecast is 7Timer's alone:
    # why not. Three days, three-hourly, coarser cloud, and wind from sustained
    # speed bands rather than gusts -- see `from_7timer`.
    fallback_reason: str | None = None

    def at(self, when: datetime) -> HourlyConditions | None:
        """Nearest hourly sample to `when`, or None if the forecast is empty."""
        if not self.hours:
            return None
        target = ensure_utc(when, field="when")
        return min(self.hours, key=lambda h: abs(h.time_utc - target))

    def covering(self, start: datetime, end: datetime) -> list[HourlyConditions]:
        start, end = ensure_utc(start), ensure_utc(end)
        return [h for h in self.hours if start <= h.time_utc <= end]

    def covers(self, start: datetime, end: datetime,
               tolerance: timedelta = timedelta(hours=1)) -> bool:
        """Does the forecast's *range* span this window?

        Deliberately not "is there a sample inside the window": samples are
        hourly, so a window shorter than an hour — a true dark window of a few
        minutes under a bright moon, say — can legitimately contain none while
        being fully covered by the forecast.
        """
        if not self.hours:
            return False
        start, end = ensure_utc(start), ensure_utc(end)
        first = self.hours[0].time_utc - tolerance
        last = self.hours[-1].time_utc + tolerance
        return start >= first and end <= last


def unavailable(location: Location, reason: str) -> Forecast:
    """The graceful-degradation return. Never raise at a caller boundary."""
    return Forecast(location=location, available=False, unavailable_reason=reason)


# --- 7Timer index conversion (PLAN.md §7: lower = better) -------------------

def _index_to_quality(index: float | None, worst: int) -> float | None:
    """7Timer index (1 = best, `worst` = worst) -> quality in 0..1, 1 = best.

    The inversion lives here and nowhere else. If this function is wrong the
    tests for it fail loudly, rather than the score quietly inverting.
    """
    if index is None:
        return None
    clamped = max(1.0, min(float(index), float(worst)))
    return (worst - clamped) / (worst - 1.0)


#: 7Timer's eight seeing classes as a representative FWHM in arcseconds: the
#: middle of each documented band, and for the open-ended ends a value just
#: past the edge. From the 7Timer ASTRO product documentation:
#: 1 <0.5", 2 0.5-0.75", 3 0.75-1", 4 1-1.25", 5 1.25-1.5", 6 1.5-2",
#: 7 2-2.5", 8 >2.5".
SEEING_CLASS_ARCSEC = (0.4, 0.625, 0.875, 1.125, 1.375, 1.75, 2.25, 2.75)

#: Seeing at or better than this is as good as a backyard telescope can use.
#: An 8-inch aperture resolves about 0.6", but the eye at the eyepiece stops
#: gaining anything well before the air gets that steady.
SEEING_FULL_MARKS_ARCSEC = 1.0
#: Where the quality line passes through: 2" is an ordinary decent night,
#: 3" a soft one where the planets still show their main features.
SEEING_AT_2_ARCSEC = 0.7
SEEING_AT_3_ARCSEC = 0.4
#: Nothing is ever worth zero. A night of 4"+ seeing still shows Saturn's
#: rings and Jupiter's two main belts; it just shows nothing finer.
SEEING_FLOOR = 0.15


def seeing_arcsec_to_quality(arcsec: float | None) -> float | None:
    """Seeing FWHM in arcseconds -> 0..1, the share of planetary detail usable.

    Piecewise linear through (1", 1.0), (2", 0.7), (3", 0.4), falling to a
    floor past that. This replaced a straight line across 7Timer's eight
    classes, which scored them as if they were evenly spaced from perfect to
    useless: it put class 4 -- 1 to 1.25", a genuinely good planetary night --
    at 0.57, and class 8 at exactly zero, when >2.5" is an ordinary night at
    most backyard sites. Squared-ish on top of that by the planetary scoring,
    it made a good night score 43 and an ordinary one about 5.
    """
    if arcsec is None:
        return None
    a = max(0.0, float(arcsec))
    if a <= SEEING_FULL_MARKS_ARCSEC:
        return 1.0
    if a <= 2.0:
        return 1.0 - (a - 1.0) * (1.0 - SEEING_AT_2_ARCSEC)
    if a <= 3.0:
        return SEEING_AT_2_ARCSEC - (a - 2.0) * (SEEING_AT_2_ARCSEC - SEEING_AT_3_ARCSEC)
    return max(SEEING_FLOOR, SEEING_AT_3_ARCSEC - (a - 3.0) * 0.25)


def seeing_index_to_quality(index: float | None) -> float | None:
    """7Timer `seeing` class (1 = under 0.5", 8 = over 2.5") -> quality 0..1.

    Through the class's arcsecond value rather than straight across the class
    numbers: the classes are not evenly spaced in anything that matters, and
    see `seeing_arcsec_to_quality` for what that got wrong. The inversion
    (lower index = better) still happens here and nowhere else. Fractional
    indices interpolate between neighbouring classes; out-of-range ones clamp.
    """
    if index is None:
        return None
    clamped = max(1.0, min(float(index), 8.0))
    lower = int(clamped)
    upper = min(lower + 1, 8)
    frac = clamped - lower
    arcsec = (SEEING_CLASS_ARCSEC[lower - 1] * (1.0 - frac)
              + SEEING_CLASS_ARCSEC[upper - 1] * frac)
    return seeing_arcsec_to_quality(arcsec)


def transparency_index_to_quality(index: float | None) -> float | None:
    """7Timer `transparency`: 1 = best, 8 = worst."""
    return _index_to_quality(index, 8)


def cloudcover_index_to_percent(index: float | None) -> float | None:
    """7Timer `cloudcover`: 1 = 0-6%, 9 = 94-100%. Returns percent."""
    if index is None:
        return None
    clamped = max(1.0, min(float(index), 9.0))
    return (clamped - 1.0) / 8.0 * 100.0


#: 7Timer marks a value it does not have with this.
SEVENTIMER_UNDEFINED = -9999


def _defined(value) -> float | None:
    """A 7Timer number, or None for missing, unparseable or -9999."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number == SEVENTIMER_UNDEFINED else number


def rh_index_to_percent(index: float | None) -> float | None:
    """7Timer ASTRO `rh2m` index -> percent, the middle of its band.

    EXTERNAL SOURCE, the 7Timer documentation (https://www.7timer.info/doc.php),
    checked 2026-11-14: -4 = 0-5%, -3 = 5-10%, ... 14 = 90-95%, then
    15 = 95-99% and 16 = 100%.
    """
    if index is None:
        return None
    i = round(max(-4.0, min(float(index), 16.0)))
    if i == 16:
        return 100.0
    if i == 15:
        return 97.0
    return (i + 4) * 5.0 + 2.5


#: 7Timer's `wind10m.speed` classes as (low, high) in m/s, from the same
#: documentation: 1 calm (<0.3), 2 light, 3 moderate, 4 fresh, 5 strong,
#: 6 gale, 7 storm, 8 hurricane (>32.6). The open top is given a nominal edge.
WIND_CLASS_MS = ((0.0, 0.3), (0.3, 3.4), (3.4, 8.0), (8.0, 10.8),
                 (10.8, 17.2), (17.2, 24.5), (24.5, 32.6), (32.6, 40.0))


def wind_class_to_kmh(index: float | None) -> tuple[float, float] | None:
    """7Timer wind class -> (sustained, gust stand-in) in km/h.

    Sustained is the middle of the band. 7Timer gives no gusts, and the score
    is built on gusts, so the band's top edge stands in: about 1.4 times the
    middle across the bands that matter, a typical gust factor over land, and
    it errs towards calling a night windier rather than calmer.
    """
    if index is None:
        return None
    low, high = WIND_CLASS_MS[round(max(1.0, min(float(index), 8.0))) - 1]
    return (low + high) / 2.0 * 3.6, high * 3.6


def dew_point_c(temperature_c: float | None, humidity_pct: float | None) -> float | None:
    """Dew point from temperature and relative humidity (Magnus formula,
    Sonntag 1990 constants). From 7Timer's whole degrees and 5% bands it is
    good to about a degree: enough for the 2 C fogging warning, no more."""
    if temperature_c is None or humidity_pct is None or humidity_pct <= 0:
        return None
    b, c = 17.62, 243.12
    gamma = (math.log(min(humidity_pct, 100.0) / 100.0)
             + b * temperature_c / (c + temperature_c))
    return c * gamma / (b - gamma)


def estimate_seeing_quality(humidity_pct: float | None,
                            wind_gust_kmh: float | None) -> float | None:
    """Fallback seeing estimate past the 7Timer horizon.

    PLAN.md §2 calls for a humidity-and-wind-shear proxy, clearly labelled as
    estimated. This is exactly that and nothing more — it is a placeholder for
    a real model, and anything using it must surface `seeing_estimated`.
    """
    if humidity_pct is None and wind_gust_kmh is None:
        return None
    quality = 1.0
    if humidity_pct is not None:
        # Above ~70% RH, seeing and transparency both start to suffer.
        quality *= max(0.35, 1.0 - max(0.0, humidity_pct - 70.0) / 60.0)
    if wind_gust_kmh is not None:
        # Gusts imply shear aloft; 40 km/h is a poor-seeing night.
        quality *= max(0.35, 1.0 - max(0.0, wind_gust_kmh - 10.0) / 45.0)
    return max(0.0, min(quality, 1.0))


# --- cache ------------------------------------------------------------------

def cache_path() -> Path:
    from .ephem import ephemeris_dir

    return ephemeris_dir().parent / "weather.sqlite3"


CACHE_DDL = """
CREATE TABLE IF NOT EXISTS weather_cache (
    key         TEXT PRIMARY KEY,
    fetched_at  TEXT NOT NULL,
    payload     TEXT NOT NULL
);
"""


def _cache_key(source: str, location: Location) -> str:
    """Rounded coordinates, per PLAN.md §2 — no point caching per metre."""
    return f"{source}:{location.lat:.2f},{location.lon:.2f}"


def _cache_read(key: str, max_age: timedelta) -> tuple[dict, datetime] | None:
    """The cached payload and when it was fetched, if no older than `max_age`."""
    path = cache_path()
    if not path.exists():
        return None
    try:
        connection = sqlite3.connect(path)
        try:
            connection.executescript(CACHE_DDL)
            row = connection.execute(
                "SELECT fetched_at, payload FROM weather_cache WHERE key = ?", (key,)
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.DatabaseError:
        return None
    if not row:
        return None

    fetched_at = datetime.fromisoformat(row[0])
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=UTC)
    if now_utc() - fetched_at > max_age:
        return None
    try:
        return json.loads(row[1]), fetched_at
    except json.JSONDecodeError:
        return None


def _cache_get(key: str, ttl: timedelta = CACHE_TTL) -> dict | None:
    hit = _cache_read(key, ttl)
    return hit[0] if hit else None


def _cache_put(key: str, payload: dict) -> None:
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        connection = sqlite3.connect(path)
        try:
            connection.executescript(CACHE_DDL)
            now = now_utc()
            connection.execute(
                "INSERT OR REPLACE INTO weather_cache VALUES (?, ?, ?)",
                (key, now.isoformat(), json.dumps(payload)),
            )
            # Every site anyone has looked at leaves a row. Past STALE_LIMIT a
            # row can never be served, so drop those rather than grow forever.
            # All stamps are aware-UTC isoformat, so they compare as text.
            connection.execute(
                "DELETE FROM weather_cache WHERE fetched_at < ?",
                ((now - STALE_LIMIT).isoformat(),),
            )
            connection.commit()
        finally:
            connection.close()
    except sqlite3.DatabaseError:
        pass          # a broken cache must never break a forecast


# --- HTTP -------------------------------------------------------------------

def _get_json(url: str, params: dict, *, upstream: Upstream, cost: int = 1,
              timeout: float = REQUEST_TIMEOUT) -> dict:
    """GET a JSON document, inside `upstream`'s budget (engine/ratelimit.py).

    Every outbound request in the engine comes through here, so every one is
    counted, and a service that says "slow down" -- or keeps failing -- is left
    alone for a while. Raises NetworkDisabled or RateLimited without making
    the call, or the urllib or JSON error if it failed; the callers catch all
    of them and degrade.
    """
    if not network_enabled():
        raise NetworkDisabled("ASTRO_NO_NETWORK is set")
    if not upstream.acquire(cost):
        raise RateLimited(upstream.name)
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{url}?{query}",
                                     headers={"User-Agent": USER_AGENT})
    host = urllib.parse.urlsplit(url).netloc
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # The caller degrades without a word, so this is the only trace of a
        # failure in the server's log. The host only: coordinates stay out.
        log.warning("%s request to %s failed: HTTP %s", upstream.name, host, exc.code)
        upstream.failed(status=exc.code,
                        retry_after=exc.headers.get("Retry-After") if exc.headers else None)
        raise
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.warning("%s request to %s failed: %s: %s",
                    upstream.name, host, type(exc).__name__, exc)
        upstream.failed()
        raise
    upstream.succeeded()
    return payload


# --- Open-Meteo -------------------------------------------------------------

class _Fetched(NamedTuple):
    payload: dict | None
    fetched_at: datetime | None = None
    stale: bool = False


def _fetch(key: str, fetch: Callable[[], dict], *, use_cache: bool) -> _Fetched:
    """Fresh cache, else the network, else a stale cache no older than STALE_LIMIT.

    Without the last step, one failed refresh after the TTL ran out -- a
    timeout, or a backoff after a 429 -- turned a perfectly good forecast from
    an hour ago into "no weather".
    """
    if use_cache:
        hit = _cache_read(key, CACHE_TTL)
        if hit is not None:
            return _Fetched(hit[0], hit[1])
    try:
        payload = fetch()
    except (urllib.error.URLError, OSError, ValueError, NetworkDisabled, RateLimited):
        if use_cache:
            hit = _cache_read(key, STALE_LIMIT)
            if hit is not None:
                return _Fetched(hit[0], hit[1], stale=True)
        return _Fetched(None)
    _cache_put(key, payload)
    return _Fetched(payload, now_utc())


def _open_meteo(location: Location, *, use_cache: bool) -> _Fetched:
    return _fetch(
        _cache_key("open-meteo", location),
        lambda: _get_json(OPEN_METEO_URL, {
            "latitude": round(location.lat, 4),
            "longitude": round(location.lon, 4),
            "hourly": ",".join(OPEN_METEO_FIELDS),
            "timezone": "UTC",
            "forecast_days": 16,
        }, upstream=OPEN_METEO, cost=OPEN_METEO_FORECAST_COST),
        use_cache=use_cache,
    )


def fetch_open_meteo(location: Location, *, use_cache: bool = True) -> dict | None:
    """Raw Open-Meteo payload, cached. None on any failure."""
    return _open_meteo(location, use_cache=use_cache).payload


def parse_open_meteo(payload: dict) -> list[HourlyConditions]:
    hourly = (payload or {}).get("hourly") or {}
    stamps = hourly.get("time") or []

    def column(name: str) -> list:
        return hourly.get(name) or [None] * len(stamps)

    columns = {name: column(name) for name in OPEN_METEO_FIELDS}
    out: list[HourlyConditions] = []
    for index, stamp in enumerate(stamps):
        try:
            when = datetime.fromisoformat(stamp).replace(tzinfo=UTC)
        except ValueError:
            continue

        def value(name: str):
            raw = columns[name][index] if index < len(columns[name]) else None
            return float(raw) if raw is not None else None

        out.append(HourlyConditions(
            time_utc=when,
            cloud_cover=value("cloud_cover"),
            cloud_low=value("cloud_cover_low"),
            cloud_mid=value("cloud_cover_mid"),
            cloud_high=value("cloud_cover_high"),
            temperature_c=value("temperature_2m"),
            dew_point_c=value("dew_point_2m"),
            humidity_pct=value("relative_humidity_2m"),
            wind_speed_kmh=value("wind_speed_10m"),
            wind_gust_kmh=value("wind_gusts_10m"),
            visibility_m=value("visibility"),
            precipitation_probability=value("precipitation_probability"),
        ))
    return out


# --- 7Timer -----------------------------------------------------------------

def _7timer(location: Location, *, use_cache: bool) -> _Fetched:
    # No `ac` (altitude correction). 7Timer accepts only 0, 2 or 7 -- km of
    # correction for a site standing well above the terrain around it, a
    # summit rather than a high valley -- and elevation alone cannot tell the
    # two apart. Sending round(elevation_km) produced 1 and 3, which the API
    # does not define.
    return _fetch(
        _cache_key("7timer", location),
        lambda: _get_json(SEVENTIMER_URL, {
            "lon": round(location.lon, 3),
            "lat": round(location.lat, 3),
            "product": "astro",
            "output": "json",
            "unit": "metric",
        }, upstream=SEVENTIMER),
        use_cache=use_cache,
    )


def fetch_7timer(location: Location, *, use_cache: bool = True) -> dict | None:
    """Raw 7Timer ASTRO payload, cached. None on any failure."""
    return _7timer(location, use_cache=use_cache).payload


def parse_7timer(payload: dict) -> list[HourlyConditions]:
    """7Timer ASTRO: 3-hourly steps from `init`, indices converted at entry."""
    if not payload:
        return []
    init_raw = str(payload.get("init") or "")
    try:
        init = datetime.strptime(init_raw, "%Y%m%d%H").replace(tzinfo=UTC)
    except ValueError:
        return []

    out: list[HourlyConditions] = []
    for entry in payload.get("dataseries") or []:
        offset = entry.get("timepoint")
        if offset is None:
            continue
        # Humidity, wind and temperature are used only when Open-Meteo is
        # out (`from_7timer`); otherwise `blend` takes them from Open-Meteo.
        temperature = _defined(entry.get("temp2m"))
        humidity = rh_index_to_percent(_defined(entry.get("rh2m")))
        wind = wind_class_to_kmh(_defined((entry.get("wind10m") or {}).get("speed")))
        out.append(HourlyConditions(
            time_utc=init + timedelta(hours=float(offset)),
            cloud_cover=cloudcover_index_to_percent(_defined(entry.get("cloudcover"))),
            temperature_c=temperature,
            dew_point_c=dew_point_c(temperature, humidity),
            humidity_pct=humidity,
            wind_speed_kmh=wind[0] if wind else None,
            wind_gust_kmh=wind[1] if wind else None,
            seeing_quality=seeing_index_to_quality(_defined(entry.get("seeing"))),
            transparency_quality=transparency_index_to_quality(
                _defined(entry.get("transparency"))),
            seeing_estimated=False,
        ))
    return out


# --- blending ---------------------------------------------------------------

def _nearest(samples: list[HourlyConditions], when: datetime,
             tolerance: timedelta) -> HourlyConditions | None:
    if not samples:
        return None
    best = min(samples, key=lambda h: abs(h.time_utc - when))
    return best if abs(best.time_utc - when) <= tolerance else None


def blend(location: Location, meteo: list[HourlyConditions],
          seventimer: list[HourlyConditions], *,
          reference: datetime | None = None) -> Forecast:
    """Cloud/humidity/wind from Open-Meteo; seeing/transparency from 7Timer.

    PLAN.md §2's blend rule. Past 7Timer's 72 h horizon the seeing and
    transparency figures fall back to the humidity/wind proxy and the whole
    forecast is flagged `seeing_estimated`.
    """
    if not meteo:
        return unavailable(location, "no Open-Meteo data")

    reference = ensure_utc(reference) if reference else now_utc()
    any_estimated = False
    merged: list[HourlyConditions] = []

    for hour in meteo:
        astro = _nearest(seventimer, hour.time_utc, timedelta(hours=1, minutes=30))
        within_horizon = (hour.time_utc - reference) <= SEVENTIMER_HORIZON

        if astro is not None and within_horizon:
            seeing = astro.seeing_quality
            transparency = astro.transparency_quality
            estimated = False
        else:
            seeing = estimate_seeing_quality(hour.humidity_pct, hour.wind_gust_kmh)
            transparency = seeing
            estimated = seeing is not None

        any_estimated = any_estimated or estimated
        merged.append(HourlyConditions(
            time_utc=hour.time_utc,
            cloud_cover=hour.cloud_cover,
            cloud_low=hour.cloud_low,
            cloud_mid=hour.cloud_mid,
            cloud_high=hour.cloud_high,
            temperature_c=hour.temperature_c,
            dew_point_c=hour.dew_point_c,
            humidity_pct=hour.humidity_pct,
            wind_speed_kmh=hour.wind_speed_kmh,
            wind_gust_kmh=hour.wind_gust_kmh,
            visibility_m=hour.visibility_m,
            precipitation_probability=hour.precipitation_probability,
            seeing_quality=seeing,
            transparency_quality=transparency,
            seeing_estimated=estimated,
        ))

    sources = ("Open-Meteo",) + (("7Timer",) if seventimer else ())
    return Forecast(location=location, hours=merged, available=True,
                    sources=sources, seeing_estimated=any_estimated)


def from_7timer(location: Location, seventimer: list[HourlyConditions], *,
                reason: str) -> Forecast:
    """A forecast from 7Timer alone, for when Open-Meteo cannot be had.

    Better than none, and labelled: three days instead of sixteen, a sample
    every three hours, 7Timer's coarser cloud bands, and wind scored from a
    stand-in for gusts (see `wind_class_to_kmh`). Seeing and transparency are
    7Timer's own either way, so they lose nothing. `reason` says why Open-Meteo
    was not used and rides along as `fallback_reason`.
    """
    if not seventimer:
        return unavailable(location, reason)
    hours = sorted(seventimer, key=lambda h: h.time_utc)
    return Forecast(location=location, hours=hours, available=True,
                    sources=("7Timer",), fallback_reason=reason)


def get_forecast(location: Location, *, start: datetime | None = None,
                 end: datetime | None = None, use_cache: bool = True) -> Forecast:
    """The public entry point. Never raises; never partially succeeds silently.

    If `start`/`end` are given and fall past the Open-Meteo horizon, the result
    is an explicit no-forecast rather than a degraded one.
    """
    reference = now_utc()

    if start is not None:
        start = ensure_utc(start, field="start")
        if start - reference > OPEN_METEO_HORIZON:
            days = (start - reference).days
            return unavailable(
                location,
                f"date is {days} days out; beyond the 16-day forecast horizon",
            )
        if start < reference - timedelta(days=1):
            return unavailable(location, "date is in the past; no forecast")

    if not network_enabled():
        return unavailable(location, "network disabled (ASTRO_NO_NETWORK)")

    meteo = _open_meteo(location, use_cache=use_cache)
    seventimer = _7timer(location, use_cache=use_cache)
    seventimer_hours = parse_7timer(seventimer.payload) if seventimer.payload else []

    if meteo.payload is None:
        # Held back by the budget or a backoff, say so: "unreachable" would
        # send someone looking for a fault that is not there. Then score what
        # 7Timer has rather than nothing: in November 2026 Open-Meteo's
        # forecast host stopped answering for days while 7Timer was fine.
        reason = OPEN_METEO.unavailable_reason() or "Open-Meteo unreachable"
        forecast = from_7timer(location, seventimer_hours, reason=reason)
    else:
        forecast = blend(location, parse_open_meteo(meteo.payload),
                         seventimer_hours, reference=reference)

    # An old copy standing in is fine, but not silently: say how old, from the
    # older of the two sources if both are, and why it was not refreshed.
    stale = [f for f in (meteo, seventimer) if f.stale and f.fetched_at]
    if forecast.available and stale:
        if meteo.stale:
            reason = OPEN_METEO.unavailable_reason() or "Open-Meteo unreachable"
        else:
            reason = SEVENTIMER.unavailable_reason() or "7Timer unreachable"
        forecast = replace(forecast,
                           stale_since=min(f.fetched_at for f in stale),
                           stale_reason=reason)

    if forecast.available and start is not None and end is not None:
        if not forecast.covers(start, end):
            if forecast.fallback_reason:
                return unavailable(location, "beyond 7Timer's 3-day forecast; "
                                             f"{forecast.fallback_reason}")
            return unavailable(location, "forecast does not cover that window")
    return forecast
