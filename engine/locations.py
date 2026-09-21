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
    #: The observer's own Bortle class, or None. This is a *measurement the
    #: observer made*, and it outranks the atlas: HANDOFF is explicit that an
    #: explicit value must never be silently overwritten by a lookup.
    bortle: int | None = None
    tz: str = field(default="UTC")
    # Optional obstruction horizon. Defaults to flat, i.e. a clear horizon.
    horizon: HorizonProfile = field(default=FLAT)
    #: SQM read from a light-pollution atlas, when one is configured and the
    #: observer gave no Bortle. Filled at construction rather than lazily in a
    #: property, so `Location` stays a frozen value rather than something that
    #: does I/O when you look at it -- and stays hashable, which `night_window`
    #: depends on for its cache.
    atlas_sqm: float | None = None

    def __post_init__(self) -> None:
        if not -90.0 <= self.lat <= 90.0:
            raise ValueError(f"{self.key}: lat {self.lat} out of range")
        if not -180.0 <= self.lon <= 180.0:
            raise ValueError(f"{self.key}: lon {self.lon} out of range")
        if self.bortle is not None and self.bortle not in BORTLE_SQM:
            raise ValueError(f"{self.key}: bortle {self.bortle} not in 1-9")

    @property
    def sqm(self) -> float | None:
        """Sky brightness in mag/arcsec^2, or None if nothing is known.

        PLAN.md §2: SQM is the internal unit and Bortle is display only. The
        observer's own class wins; the atlas fills in only where they gave
        none.
        """
        if self.bortle is not None:
            return BORTLE_SQM[self.bortle]
        return self.atlas_sqm

    @property
    def sky_source(self) -> str:
        """Where `sqm` came from: "observer", "atlas", or "assumed".

        "assumed" is the honest name for having nothing: `engine/targets.py`
        falls back to Bortle 5, which sets the limiting magnitude and the
        contrast test and therefore decides which objects appear at all. That
        fallback is reasonable; it happening invisibly is not, so every layer
        above can and does say which of the three it is looking at.
        """
        if self.bortle is not None:
            return "observer"
        if self.atlas_sqm is not None:
            return "atlas"
        return "assumed"

    @property
    def effective_bortle(self) -> int:
        """The class the engine will actually filter by, however it was got.

        Display only, and deliberately never None: it answers "what is being
        used", which with the Bortle-5 fallback always has an answer.
        """
        from .skybrightness import bortle_from_sqm

        sqm = self.sqm
        return 5 if sqm is None else bortle_from_sqm(sqm)


@functools.lru_cache(maxsize=1)
def _timezone_finder():
    """One TimezoneFinder for the process. Building one takes about a second,
    and caching only the *answer* per coordinate meant every new coordinate
    -- each site in the editor, each map click -- paid that again."""
    from timezonefinder import TimezoneFinder

    return TimezoneFinder()


@functools.lru_cache(maxsize=256)
def _resolve_tz(lat: float, lon: float) -> str:
    """Coordinates -> IANA zone."""
    tz = _timezone_finder().timezone_at(lat=lat, lng=lon)
    return tz or "UTC"


def atlas_sqm_for(lat: float, lon: float, bortle: int | None) -> float | None:
    """Look up the atlas, but only where the observer supplied nothing.

    Imported lazily because `skybrightness` imports `BORTLE_SQM` from this
    module; at module scope the two would form an import cycle. Any failure is
    swallowed -- an optional lookup must not be able to stop a site loading.
    """
    if bortle is not None:
        return None
    try:
        from .skybrightness import sqm_at

        return sqm_at(lat, lon)
    except Exception:                    # noqa: BLE001 - optional, never fatal
        return None


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
        bortle = spec.get("bortle")
        out[key] = Location(
            key=key,
            name=spec.get("name", key),
            lat=lat,
            lon=lon,
            elevation_m=float(spec.get("elevation_m", 0.0)),
            bortle=bortle,
            tz=spec.get("tz") or _resolve_tz(lat, lon),
            horizon=parse_horizon(spec.get("horizon")),
            atlas_sqm=atlas_sqm_for(lat, lon, bortle),
        )
    return out


def default_location_key(path: Path | None = None) -> str | None:
    """Which site to open on, or None when there is nothing to open on.

    The shipped config has no sites and no default, deliberately: every answer
    this planner gives depends on where you are standing, and a plausible
    default belonging to somebody else is worse than no answer. So "no sites
    yet" is an ordinary state to be handled, not an error to be raised.

    Order of preference: an explicit `default:` that actually resolves, then
    the single site if there is only one, then the first by key, then None.
    """
    raw = _merged_config(path)
    known = load_locations(path)
    if not known:
        return None

    configured = raw.get("default")
    if configured and configured in known:
        return configured
    return sorted(known)[0]


def get_location(key: str | None = None, path: Path | None = None) -> Location:
    """Look up a location by key, falling back to the default if there is one."""
    locations = load_locations(path)
    resolved = key or default_location_key(path)
    if resolved is None:
        raise LocationError(
            "no observing sites are configured. Add one with the \"Sites...\" "
            "button in the web UI, or in config/locations.yaml"
        )
    if resolved not in locations:
        known = ", ".join(sorted(locations)) or "(none)"
        raise LocationError(f"unknown location {resolved!r}; known: {known}")
    return locations[resolved]
