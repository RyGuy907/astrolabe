"""Place name -> coordinates, via Open-Meteo's keyless geocoding endpoint.

Networked, like `weather.py`, and held to the same contract: it never raises on
a network failure, it returns an explicit empty result instead.

This lives in `engine/` rather than `api/` because PLAN.md §4 requires the API
to be a thin adapter over engine functions, and a Location is an engine
concept. No astronomy depends on it — the engine remains fully usable offline;
only the convenience of typing a place name instead of coordinates is lost.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
REQUEST_TIMEOUT = 15
USER_AGENT = "astro-night-planner/0.1 (personal observing planner)"


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


def search(query: str, count: int = 5) -> list[GeocodeResult]:
    """Look up a place name. Returns [] on any failure — never raises."""
    from .weather import network_enabled

    if not network_enabled() or not query.strip():
        return []

    params = urllib.parse.urlencode({
        "name": query.strip(), "count": max(1, min(count, 20)), "format": "json",
    })
    request = urllib.request.Request(f"{GEOCODE_URL}?{params}",
                                     headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return []

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
    return out
