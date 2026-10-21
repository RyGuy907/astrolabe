"""Sky brightness from a light-pollution raster, when one is available.

PLAN.md §2 stores sky brightness as **SQM (mag/arcsec²)** internally and treats
Bortle as a display convenience. This module is the missing half of that: a way
to get an SQM for a coordinate instead of asking the observer to type a Bortle
class from memory.

Where the raster comes from
---------------------------
`sqm_at()` reads a raster of artificial sky brightness from
`config/skybrightness.tif`, or wherever the `ASTRO_SKYBRIGHTNESS_RASTER`
environment variable points; the feature is simply off without one. The file
is built, not committed: `scripts/build_skyglow.py` makes one for the lower 48
from EOG's latest annual VIIRS night lights, with a propagation model fitted to
Falchi et al.'s World Atlas. Falchi's own raster, or a regional export of it,
works here too. See the README.

A raster may say what it is in a `LABEL` tag ("modelled from 2025 satellite
data"), which `source_label()` passes on so the app can show it beside the
class it filled in.

Why a raster and not satellite radiance
---------------------------------------
Light pollution is **not local**. A satellite measures light leaving the ground
beneath each pixel; the sky glow above your head is that light scattered by the
atmosphere from everything for tens of kilometres around. A dark canyon an hour
outside a city has almost no upward radiance of its own and still sits under the
city's dome.

Bridging the two takes a light-propagation model. That modelling *is* Falchi's
contribution, and it is what lightpollutionmap.info's sky-brightness layer
means by "light spread modeled via convolution". So this module expects a raster
that already holds **artificial sky brightness**, not raw radiance. Feeding it
VIIRS radiance directly would produce numbers that are systematically too dark
in exactly the places people drive to.

The conversion is published, not invented
-----------------------------------------
Artificial brightness in mcd/m² becomes SQM by

    SQM = log10(B_total / 1.08e8) / -0.4

with the natural night sky added in at 0.171168465 mcd/m², after
lightpollutionmap.info's published formula. The -1/0.4 is the usual Pogson
factor of -2.5. Two checks anchor it, both in the tests: the natural sky alone
returns exactly 22.00 mag/arcsec², the accepted dark-sky value, and the
conversion round-trips against its own inverse.
"""

from __future__ import annotations

import functools
import json
import math
import os
import re
from pathlib import Path

from .locations import BORTLE_SQM, BORTLE_SQM_LOWER

#: Zenith brightness of the natural night sky — airglow, starlight, zodiacal
#: light — in mcd/m². Alone it corresponds to SQM 22.00, which the tests pin.
NATURAL_SKY_MCD_M2 = 0.171168465

#: Zero point of the mcd/m² to mag/arcsec² relation.
BRIGHTNESS_ZERO_POINT = 1.08e8

# --- calibration against the ground ------------------------------------------
#
# A raster from `scripts/build_skyglow.py` reproduces the Falchi atlas, and was
# shipped with its artificial light multiplied by a flat 1.55 to match Globe at
# Night sky-meter readings. Tested against those readings with regions held
# out (2024-25, 1,539 clear, dark, moonless readings at 564 sites), that factor
# turned out to be the wrong shape: the readings run 0.6-0.7 mag brighter than
# the map in *every* band, pristine sites included, which a factor on
# artificial light alone cannot fix. Fitting the natural sky and the
# artificial light separately does, and cut the held-out error from 0.49 to
# 0.28 mag. What it says, and what is applied here at query time:
#
# * **Artificial light relative to the natural sky is 0.94 of the model's.**
#   Both come out brighter on a citizen meter -- the natural sky by x1.84,
#   which is meter zero-point, solar-maximum airglow and the readers' own
#   bias -- but in proportion to each other they are nearly what the model
#   says. The SQM here keeps the Bortle scale's natural sky of 22.00 and that
#   ratio, so a class means the same balance of light as before; the brighter
#   sky an observer actually stands under is `REAL_SKY_OFFSET` away.
# * **Haze thins with height.** The fitted kernel gave the haze layer no
#   weight, so towns 1-2 km up read ~0.4 mag too bright -- Salt Lake City,
#   Provo, Denver. A 2.5 km scale height above 300 m halves the error at lit
#   sites above 900 m, and naked-eye counts no fit had seen agree.
# * **Lights dim through the night**, about 4.5% an hour. The raster is the
#   satellite's ~01:30 picture, held here as midnight; `at_hour` moves it.
#
# The raster itself is left as built, so these apply to it as it is on disk.
# Only to ours, though: a raster without a CALIBRATION tag (Falchi's) is
# taken as it stands.

#: The fitted ratio of artificial to natural light, against the model's.
ARTIFICIAL_RATIO = 0.94
#: Haze above this ground height thins with HAZE_SCALE_M.
HAZE_FLOOR_M = 300.0
HAZE_SCALE_M = 2500.0
#: Fractional change in artificial light per hour after local midnight.
HOURLY_DIMMING = 0.045
#: How much brighter, in mag/arcsec^2, the sky the eye works against is than
#: this module's SQM. Two measured steps: a sky meter reads the whole sky
#: 0.66 brighter than the Bortle scale's 22.00 natural sky (the fitted
#: natural x1.84), and Schaefer's naked-eye relation matched 1,539 star
#: counts taken at the meter's reading less a further 0.17 -- Cinzano's
#: SQM-to-V term. Where anything modelling what the eye sees starts from:
#: `equipment.naked_eye_limiting_mag`, and moonlight in `engine/targets.py`.
REAL_SKY_OFFSET = 0.83

#: Environment variable naming a local artificial-brightness raster.
RASTER_ENV_VAR = "ASTRO_SKYBRIGHTNESS_RASTER"

#: Checked when the environment variable is unset, so a raster can simply be
#: dropped in and used without wiring anything up. Gitignored along with every
#: other `.tif`, because the file is a third-party derived product and is not
#: ours to redistribute.
CONVENTIONAL_RASTER = Path(__file__).resolve().parent.parent / "config" / "skybrightness.tif"


class SkyBrightnessUnavailable(RuntimeError):
    """A raster was configured but could not be used.

    Distinct from "no raster configured", which is not an error and returns
    None — the whole feature is optional.
    """


def sqm_from_artificial_brightness(mcd_per_m2: float) -> float:
    """Artificial brightness (mcd/m²) -> total sky SQM (mag/arcsec²).

    The natural sky is added in, because SQM describes the whole sky rather
    than the artificial component alone. Falchi's raster, and any raster of
    "artificial brightness", holds only the artificial part.
    """
    if mcd_per_m2 < 0:
        raise ValueError(f"artificial brightness cannot be negative: {mcd_per_m2}")
    total = mcd_per_m2 + NATURAL_SKY_MCD_M2
    return math.log10(total / BRIGHTNESS_ZERO_POINT) / -0.4


def artificial_brightness_from_sqm(sqm: float) -> float:
    """The inverse, for tests and for reasoning about the scale.

    Clamped at zero: an SQM darker than the natural sky implies a negative
    artificial component, which is a measurement artefact rather than a place.
    """
    total = BRIGHTNESS_ZERO_POINT * 10 ** (-0.4 * sqm)
    return max(0.0, total - NATURAL_SKY_MCD_M2)


def bortle_from_sqm(sqm: float) -> int:
    """SQM -> Bortle class, by the ranges in `BORTLE_SQM_LOWER`.

    The darkest class whose lower bound the sky reaches; brighter than every
    bound is class 9. Each class's `BORTLE_SQM` value lies inside its range,
    so the two tables agree in both directions.
    """
    for cls in sorted(BORTLE_SQM_LOWER):
        if sqm >= BORTLE_SQM_LOWER[cls]:
            return cls
    return 9


#: Where the decimal scale bottoms out: class 9 has no bright end, so 9.4 is
#: set at the brightest city skies, around SQM 17.0.
_BRIGHTEST_SQM = 17.0


def bortle_decimal_from_sqm(sqm: float) -> float:
    """SQM -> Bortle with a decimal, e.g. 4.3, for a value read off a map.

    A whole class hides most of what a map knows: 20.6 and 21.6 are both
    "Bortle 4" and are not the same sky. The decimal is how far through its
    class's SQM range the sky is, from x.6 at the darker edge to x.4 past the
    whole number at the brighter one, so it never disagrees with the class
    `bortle_from_sqm` gives ("Bortle 4.5" beside a class of 4). Class 1 is
    simply 1.0: no sky is darker than pristine.
    """
    whole = bortle_from_sqm(sqm)
    if whole == 1:
        return 1.0
    dark_edge = BORTLE_SQM_LOWER[whole - 1]
    bright_edge = BORTLE_SQM_LOWER.get(whole, _BRIGHTEST_SQM)
    share = (dark_edge - sqm) / (dark_edge - bright_edge)
    exact = whole - 0.5 + min(max(share, 0.0), 1.0)
    return min(max(round(exact, 1), whole - 0.4), whole + 0.4)


def raster_path() -> Path | None:
    """The configured raster, or None when the feature is switched off.

    The environment variable wins, so a one-off run can point somewhere else;
    otherwise a file at the conventional path is picked up automatically.
    """
    raw = os.environ.get(RASTER_ENV_VAR, "").strip()
    if raw:
        return Path(raw)
    return CONVENTIONAL_RASTER if CONVENTIONAL_RASTER.is_file() else None


@functools.lru_cache(maxsize=1)
def _open_raster(path: str):
    """Open once and keep it open; these files are large and lookups are many."""
    try:
        import rasterio
    except ImportError as exc:                      # pragma: no cover - env
        raise SkyBrightnessUnavailable(
            f"{RASTER_ENV_VAR} is set but rasterio is not installed. "
            'Install the optional extra: pip install -e ".[skybrightness]"'
        ) from exc

    try:
        return rasterio.open(path)
    except Exception as exc:                        # noqa: BLE001 - any GDAL error
        raise SkyBrightnessUnavailable(
            f"could not open the sky-brightness raster at {path}: {exc}"
        ) from exc


@functools.lru_cache(maxsize=4)
def _baked_calibration(path: str) -> float | None:
    """The factor a raster's artificial light was multiplied by when built,
    from its CALIBRATION tag ("artificial x1.55, ..."), or None for a raster
    that is not one of ours."""
    try:
        tag = _open_raster(path).tags().get("CALIBRATION", "")
    except Exception:                    # noqa: BLE001 - optional, never fatal
        return None
    match = re.search(r"x\s*([0-9.]+)", tag)
    return float(match.group(1)) if match else None


def haze_factor(elevation_m: float | None) -> float:
    """How much of the artificial glow a site this high still has above it."""
    if elevation_m is None:
        return 1.0
    return math.exp(-max(0.0, float(elevation_m) - HAZE_FLOOR_M) / HAZE_SCALE_M)


def at_hour(sqm: float, hours_from_midnight: float) -> float:
    """A site's SQM for another time of night than midnight.

    The artificial part brightens before midnight and fades after it, by
    HOURLY_DIMMING an hour; the natural sky is left alone. Clamped to six
    hours either side, past which the fit has no data.
    """
    hours = min(max(float(hours_from_midnight), -6.0), 6.0)
    artificial = artificial_brightness_from_sqm(sqm) * math.exp(-HOURLY_DIMMING * hours)
    return sqm_from_artificial_brightness(artificial)


def sqm_at(lat: float, lon: float, elevation_m: float | None = None) -> float | None:
    """Total sky SQM at a coordinate, or None if no raster is configured.

    At local midnight, on the Bortle scale's 22.00 natural sky: see the
    calibration notes above. `elevation_m` is the site's height, for the haze
    above it; without one no altitude correction is made.

    Returns None rather than raising for a coordinate outside the raster's
    coverage or over a nodata cell — "we do not know" is a real answer here,
    and PLAN.md §2 asks for it explicitly rather than a guess.
    """
    path = raster_path()
    if path is None:
        return None

    dataset = _open_raster(str(path))
    try:
        values = list(dataset.sample([(lon, lat)]))
    except Exception:                               # noqa: BLE001 - outside coverage
        return None
    if not values or len(values[0]) == 0:
        return None

    artificial = float(values[0][0])
    if not math.isfinite(artificial):
        return None
    nodata = dataset.nodata
    if nodata is not None and artificial == nodata:
        return None
    if artificial < 0:
        # Falchi's grid uses small negative values as fill in places.
        return None

    baked = _baked_calibration(str(path))
    if baked:
        artificial = artificial / baked * ARTIFICIAL_RATIO * haze_factor(elevation_m)
    return sqm_from_artificial_brightness(artificial)


def bortle_at(lat: float, lon: float, elevation_m: float | None = None) -> int | None:
    """Convenience wrapper: coordinate -> Bortle class, or None."""
    sqm = sqm_at(lat, lon, elevation_m)
    return None if sqm is None else bortle_from_sqm(sqm)


def coverage_bounds() -> tuple[float, float, float, float] | None:
    """(west, south, east, north) of the configured raster, or None.

    Lets the UI open the map where the data actually is. A regional export
    covers one area, and centring the picker somewhere with no coverage means
    every click falls back to assuming Bortle 5 -- which works, and is a poor
    first impression. Reading it from the file means swapping the raster moves
    the map with it, rather than leaving a stale constant behind.
    """
    path = raster_path()
    if path is None:
        return None
    try:
        dataset = _open_raster(str(path))
        west, south, east, north = dataset.bounds
        return (float(west), float(south), float(east), float(north))
    except Exception:                    # noqa: BLE001 - optional, never fatal
        return None


def tile_dir() -> Path | None:
    """The configured raster's pre-rendered map tiles, if it has any.

    `scripts/build_skyglow.py tiles` writes them beside the raster as
    `<name>_tiles/`, with a `meta.json` describing them; the site map overlays
    them. None when the raster came without them (Falchi's, say).
    """
    path = raster_path()
    if path is None:
        return None
    tiles = path.with_name(path.stem + "_tiles")
    return tiles if (tiles / "meta.json").is_file() else None


def tile_meta() -> dict | None:
    """The tiles' zoom range, bounds, colour key and version, or None.

    The version names this build of the tiles; the site map puts it in their
    URLs so a rebuilt map is never hidden behind cached old ones. Tiles from
    before builds carried one are versioned by when their metadata was
    written, which changes with every build all the same.
    """
    tiles = tile_dir()
    if tiles is None:
        return None
    try:
        path = tiles / "meta.json"
        meta = json.loads(path.read_text(encoding="utf-8"))
        meta.setdefault("version", str(int(path.stat().st_mtime)))
        return meta
    except (OSError, ValueError):
        return None


def tile_file(z: int, x: int, y: int) -> Path | None:
    """One tile's file, or None where nothing was drawn (a pristine sky)."""
    tiles = tile_dir()
    if tiles is None:
        return None
    path = tiles / str(z) / str(x) / f"{y}.png"
    return path if path.is_file() else None


def source_label() -> str | None:
    """What the configured raster says it is, or None.

    From the file's `LABEL` tag, so a rebuilt map describes itself and there
    is no year here to forget to update. A raster without one -- Falchi's,
    say -- is simply unlabelled.
    """
    path = raster_path()
    if path is None:
        return None
    try:
        label = _open_raster(str(path)).tags().get("LABEL", "").strip()
    except Exception:                    # noqa: BLE001 - optional, never fatal
        return None
    return label or None


def is_configured() -> bool:
    """Whether a raster is available, for callers that want to say so."""
    return raster_path() is not None
