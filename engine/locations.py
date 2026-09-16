"""Observing site resolution from config/locations.yaml."""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .horizon import FLAT, HorizonProfile, parse_horizon

# Bortle -> SQM (mag/arcsec^2), PLAN.md 2. SQM is what we store internally;
# Bortle is a display convenience.
BORTLE_SQM = {
    1: 21.9, 2: 21.7, 3: 21.4, 4: 20.9, 5: 20.4,
    6: 19.4, 7: 18.5, 8: 18.0, 9: 17.5,
}

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
DEFAULT_LOCATIONS_PATH = CONFIG_DIR / "locations.yaml"

#: Optional, gitignored override merged over the committed config.
#:
#: The shipped `locations.yaml` holds public example sites, because a real
#: observing site is usually somebody's home to four decimal places -- about
#: 11 m -- and that does not belong in a public repository. Keep your own
#: sites here instead: same schema, merged on top, and a key defined in both
#: files takes its definition from this one. A `default:` here also wins.
LOCAL_LOCATIONS_PATH = CONFIG_DIR / "locations.local.yaml"


class LocationError(KeyError):
    """Raised when a requested location key is not in the config."""


@dataclass(frozen=True)
class Location:
    """An observing site.

    `tz` is the IANA zone resolved from the coordinates at load time, so no
    engine code has to touch timezonefinder again.
    """

    key: str
    name: str
    lat: float
    lon: float
    elevation_m: float = 0.0
    bortle: int | None = None
    tz: str = field(default="UTC")
    # Optional obstruction horizon. Defaults to flat, i.e. a clear horizon.
    horizon: HorizonProfile = field(default=FLAT)

    def __post_init__(self) -> None:
        if not -90.0 <= self.lat <= 90.0:
            raise ValueError(f"{self.key}: lat {self.lat} out of range")
        if not -180.0 <= self.lon <= 180.0:
            raise ValueError(f"{self.key}: lon {self.lon} out of range")
        if self.bortle is not None and self.bortle not in BORTLE_SQM:
            raise ValueError(f"{self.key}: bortle {self.bortle} not in 1-9")

    @property
    def sqm(self) -> float | None:
        """Sky brightness in mag/arcsec^2, or None if bortle is unset."""
        return None if self.bortle is None else BORTLE_SQM[self.bortle]


@functools.lru_cache(maxsize=8)
def _resolve_tz(lat: float, lon: float) -> str:
    """Coordinates -> IANA zone. Cached; timezonefinder init is not cheap."""
    from timezonefinder import TimezoneFinder

    tz = TimezoneFinder().timezone_at(lat=lat, lng=lon)
    return tz or "UTC"


def _read_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _merged_config(path: Path | None = None) -> dict:
    """The committed config, with any local override merged over it.

    The override is only consulted when the caller did not name an explicit
    path, so tests that point at a temporary config stay hermetic and cannot
    pick up whatever sites the developer happens to have locally.
    """
    if path is not None:
        return _read_yaml(path)

    raw = _read_yaml(DEFAULT_LOCATIONS_PATH)
    if not LOCAL_LOCATIONS_PATH.exists():
        return raw

    local = _read_yaml(LOCAL_LOCATIONS_PATH)
    merged = dict(raw)
    merged["locations"] = {**(raw.get("locations") or {}),
                           **(local.get("locations") or {})}
    if local.get("default"):
        merged["default"] = local["default"]
    return merged


def load_locations(path: Path | None = None) -> dict[str, Location]:
    """Parse locations.yaml into `key -> Location`, resolving timezones."""
    raw = _merged_config(path)

    out: dict[str, Location] = {}
    for key, spec in (raw.get("locations") or {}).items():
        lat = float(spec["lat"])
        lon = float(spec["lon"])
        out[key] = Location(
            key=key,
            name=spec.get("name", key),
            lat=lat,
            lon=lon,
            elevation_m=float(spec.get("elevation_m", 0.0)),
            bortle=spec.get("bortle"),
            tz=spec.get("tz") or _resolve_tz(lat, lon),
            horizon=parse_horizon(spec.get("horizon")),
        )
    return out


def default_location_key(path: Path | None = None) -> str:
    """The `default:` key, from the local override if it sets one."""
    raw = _merged_config(path)
    key = raw.get("default")
    if not key:
        raise LocationError(
            f"{path or DEFAULT_LOCATIONS_PATH} has no `default:` key"
        )
    return key


def get_location(key: str | None = None, path: Path | None = None) -> Location:
    """Look up a location by key, falling back to the configured default."""
    locations = load_locations(path)
    resolved = key or default_location_key(path)
    if resolved not in locations:
        known = ", ".join(sorted(locations)) or "(none)"
        raise LocationError(f"unknown location {resolved!r}; known: {known}")
    return locations[resolved]
