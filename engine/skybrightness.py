"""Sky brightness from a light-pollution raster, when one is available.

PLAN.md §2 stores sky brightness as **SQM (mag/arcsec²)** internally and treats
Bortle as a display convenience. This module is the missing half of that: a way
to get an SQM for a coordinate instead of asking the observer to type a Bortle
class from memory.

No data ships with this project
-------------------------------
`sqm_at()` reads a raster **you** supply, named by the
`ASTRO_SKYBRIGHTNESS_RASTER` environment variable. Nothing is vendored, and the
feature is simply off when the variable is unset. That is deliberate:

* The obvious dataset, Falchi et al. 2016 (DOI 10.5880/GFZ.1.4.2016.001), is
  not a public download — it is behind a request form — and is CC BY-NC, so
  redistributing a derivative of it inside an MIT repository would be wrong.
* The obvious alternative, EOG's VIIRS annual composites, now requires an
  account.
* Either file is gigabytes before downsampling, which does not belong in a
  repository this size.

See the README for how to obtain a raster and point this at it.

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
import math
import os
from pathlib import Path

from .locations import BORTLE_SQM

#: Zenith brightness of the natural night sky — airglow, starlight, zodiacal
#: light — in mcd/m². Alone it corresponds to SQM 22.00, which the tests pin.
NATURAL_SKY_MCD_M2 = 0.171168465

#: Zero point of the mcd/m² to mag/arcsec² relation.
BRIGHTNESS_ZERO_POINT = 1.08e8

#: Environment variable naming a local artificial-brightness raster.
RASTER_ENV_VAR = "ASTRO_SKYBRIGHTNESS_RASTER"


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
    """SQM -> nearest Bortle class, the inverse of `BORTLE_SQM`.

    `BORTLE_SQM`'s values are representative points rather than boundaries, so
    "nearest" is the honest reading: it puts the class boundary halfway between
    neighbouring entries. Values beyond either end clamp to 1 or 9 rather than
    extrapolating off a nine-point scale.
    """
    return min(BORTLE_SQM, key=lambda cls: abs(BORTLE_SQM[cls] - sqm))


def raster_path() -> Path | None:
    """The configured raster, or None when the feature is switched off."""
    raw = os.environ.get(RASTER_ENV_VAR, "").strip()
    return Path(raw) if raw else None


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


def sqm_at(lat: float, lon: float) -> float | None:
    """Total sky SQM at a coordinate, or None if no raster is configured.

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

    return sqm_from_artificial_brightness(artificial)


def bortle_at(lat: float, lon: float) -> int | None:
    """Convenience wrapper: coordinate -> Bortle class, or None."""
    sqm = sqm_at(lat, lon)
    return None if sqm is None else bortle_from_sqm(sqm)


def is_configured() -> bool:
    """Whether a raster is available, for callers that want to say so."""
    return raster_path() is not None
