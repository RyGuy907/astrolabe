"""Place name -> coordinates, and coordinates -> ground height, via Open-Meteo.

Networked, like `weather.py`, and held to the same contract: it never raises on
a network failure, it returns an explicit empty result instead.

This lives in `engine/` rather than `api/` because PLAN.md §4 requires the API
to be a thin adapter over engine functions, and a Location is an engine
concept. No astronomy depends on it — the engine remains fully usable offline;
only the convenience of typing a place name instead of coordinates is lost.

`elevation_at` is the same convenience for a point picked off a map, which has
no name to search for and so used to be saved at 0 m. It asks Open-Meteo's
elevation endpoint -- the terrain model behind the geocoder's own elevations,
about 90 m across, fine enough to tell a ridge from the valley beside it.

Both go through `weather._get_json`, so they spend from the same Open-Meteo
budget as the forecast and back off with it (engine/ratelimit.py). Both cache
their answers -- places and ground do not move -- but never a failure: an
outage used to be remembered as "no elevation here" until a restart.
"""

from __future__ import annotations

import functools
import urllib.error
import urllib.request
from dataclasses import dataclass

from .ratelimit import OPEN_METEO, RateLimited

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
REQUEST_TIMEOUT = 15
USER_AGENT = "astrolabe/0.1 (personal observing planner)"


@dataclass(frozen=True)
class GeocodeResult:
    name: str
    lat: float
    lon: float
    elevation_m: float
    country: str | None = None
    admin1: str | None = None

    @property
    def label(self) -> str:
        parts = [self.name] + [p for p in (self.admin1, self.country) if p]
        return ", ".join(parts)

    def suggested_key(self) -> str:
        """A config-safe key: lowercase, underscores, no punctuation."""
        cleaned = "".join(
            character if character.isalnum() else "_"
            for character in self.name.lower()
        )
        while "__" in cleaned:
            cleaned = cleaned.replace("__", "_")
        return cleaned.strip("_") or "location"


class _Unanswered(Exception):
    """Inside the cached lookups: no answer this time, so nothing is cached."""


def search(query: str, count: int = 5) -> list[GeocodeResult]:
    """Look up a place name. Returns [] on any failure — never raises."""
    # Spacing and case do not change the answer, so they do not miss the cache.
    name = " ".join(query.split()).lower()
    if not name:
        return []
    try:
        return list(_search(name, max(1, min(count, 20))))
    except _Unanswered:
        return []


@functools.lru_cache(maxsize=512)
def _search(name: str, count: int) -> tuple[GeocodeResult, ...]:
    from .weather import NetworkDisabled, _get_json

    try:
        payload = _get_json(GEOCODE_URL, {"name": name, "count": count, "format": "json"},
                            upstream=OPEN_METEO, timeout=REQUEST_TIMEOUT)
    except (urllib.error.URLError, OSError, ValueError, NetworkDisabled, RateLimited):
        raise _Unanswered

    out: list[GeocodeResult] = []
    for entry in payload.get("results") or []:
        try:
            out.append(GeocodeResult(
                name=entry["name"],
                lat=float(entry["latitude"]),
                lon=float(entry["longitude"]),
                elevation_m=float(entry.get("elevation") or 0.0),
                country=entry.get("country"),
                admin1=entry.get("admin1"),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(out)


def elevation_at(lat: float, lon: float) -> float | None:
    """Ground height in metres at a coordinate, or None if it cannot be had.

    Offline, switched off, or an answer that does not parse: None, never an
    exception, and never a guess -- the form then leaves the field for the
    observer rather than filling in 0 m as though it were known.
    """
    try:
        return _elevation(round(lat, 4), round(lon, 4))
    except _Unanswered:
        return None


@functools.lru_cache(maxsize=512)
def _elevation(lat: float, lon: float) -> float | None:
    """Cached per ~10 m of coordinate: ground does not move, and dragging a
    pin back and forth asks about the same few points again. A failure
    raises instead of returning, so it is not cached."""
    from .weather import NetworkDisabled, _get_json

    try:
        payload = _get_json(ELEVATION_URL, {"latitude": lat, "longitude": lon},
                            upstream=OPEN_METEO, timeout=REQUEST_TIMEOUT)
    except (urllib.error.URLError, OSError, ValueError, NetworkDisabled, RateLimited):
        raise _Unanswered
    try:
        value = float(payload["elevation"][0])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    # The terrain model marks "no data" (open ocean, mostly) as NaN.
    return value if value == value else None
