"""Weather clients: Open-Meteo + 7Timer, cached and blended.

**This is the only module in `engine/` permitted to touch the network.**
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
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .locations import Location
from .timeutil import UTC, ensure_utc, now_utc

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
SEVENTIMER_URL = "https://www.7timer.info/bin/api.pl"

# PLAN.md §2: cache aggressively, one fetch per location per model run.
CACHE_TTL = timedelta(hours=3)
REQUEST_TIMEOUT = 20

# Published horizons. Past these, a source returns no data rather than a guess.
OPEN_METEO_HORIZON = timedelta(days=16)
SEVENTIMER_HORIZON = timedelta(hours=72)

USER_AGENT = "astro-night-planner/0.1 (personal observing planner)"

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


def _cache_get(key: str, ttl: timedelta = CACHE_TTL) -> dict | None:
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
    if now_utc() - fetched_at > ttl:
        return None
    try:
        return json.loads(row[1])
    except json.JSONDecodeError:
        return None


def _cache_put(key: str, payload: dict) -> None:
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        connection = sqlite3.connect(path)
        try:
            connection.executescript(CACHE_DDL)
            connection.execute(
                "INSERT OR REPLACE INTO weather_cache VALUES (?, ?, ?)",
                (key, now_utc().isoformat(), json.dumps(payload)),
            )
            connection.commit()
        finally:
            connection.close()
    except sqlite3.DatabaseError:
        pass          # a broken cache must never break a forecast


# --- HTTP -------------------------------------------------------------------

def _get_json(url: str, params: dict) -> dict:
    if not network_enabled():
        raise NetworkDisabled("ASTRO_NO_NETWORK is set")
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{url}?{query}",
                                     headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


# --- Open-Meteo -------------------------------------------------------------

def fetch_open_meteo(location: Location, *, use_cache: bool = True) -> dict | None:
    """Raw Open-Meteo payload, cached. None on any failure."""
    key = _cache_key("open-meteo", location)
    if use_cache:
        cached = _cache_get(key)
        if cached is not None:
            return cached
    try:
        payload = _get_json(OPEN_METEO_URL, {
            "latitude": round(location.lat, 4),
            "longitude": round(location.lon, 4),
            "hourly": ",".join(OPEN_METEO_FIELDS),
            "timezone": "UTC",
            "forecast_days": 16,
        })
    except (urllib.error.URLError, OSError, ValueError, NetworkDisabled):
        return None
    _cache_put(key, payload)
    return payload


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

def fetch_7timer(location: Location, *, use_cache: bool = True) -> dict | None:
    """Raw 7Timer ASTRO payload, cached. None on any failure."""
    key = _cache_key("7timer", location)
    if use_cache:
        cached = _cache_get(key)
        if cached is not None:
            return cached
    params = {
        "lon": round(location.lon, 3),
        "lat": round(location.lat, 3),
        "product": "astro",
        "output": "json",
        "unit": "metric",
    }
    # PLAN.md §2: `ac` corrects for sites well above surrounding terrain.
    if location.elevation_m and location.elevation_m > 1000:
        params["ac"] = round(location.elevation_m / 1000.0)
    try:
        payload = _get_json(SEVENTIMER_URL, params)
    except (urllib.error.URLError, OSError, ValueError, NetworkDisabled):
        return None
    _cache_put(key, payload)
    return payload


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
        out.append(HourlyConditions(
            time_utc=init + timedelta(hours=float(offset)),
            cloud_cover=cloudcover_index_to_percent(entry.get("cloudcover")),
            humidity_pct=None,
            seeing_quality=seeing_index_to_quality(entry.get("seeing")),
            transparency_quality=transparency_index_to_quality(entry.get("transparency")),
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

    meteo_payload = fetch_open_meteo(location, use_cache=use_cache)
    if meteo_payload is None:
        return unavailable(location, "Open-Meteo unreachable")

    seventimer_payload = fetch_7timer(location, use_cache=use_cache)

    forecast = blend(
        location,
        parse_open_meteo(meteo_payload),
        parse_7timer(seventimer_payload) if seventimer_payload else [],
        reference=reference,
    )

    if forecast.available and start is not None and end is not None:
        if not forecast.covers(start, end):
            return unavailable(location, "forecast does not cover that window")
    return forecast
