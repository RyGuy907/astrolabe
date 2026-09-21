"""Astronomical seeing estimated from a forecast's vertical profile.

What blurs a planet in a telescope is optical turbulence: pockets of air at
slightly different temperatures, and so slightly different refractive
indices, being mixed by wind. It lives in thin layers -- where the
temperature changes sharply with height and the wind changes speed or
direction across that height -- and the image quality is set by the sum of
it along the line of sight.

A weather model does not forecast turbulence directly, but it does forecast
the two ingredients, temperature and wind at a stack of pressure levels. This
module turns that stack into an estimated seeing disc in arcseconds using the
standard chain:

1. For each layer between adjacent levels, the vertical gradient of potential
   temperature and the wind shear.
2. The refractive-index gradient, M = -79e-6 (P / T^2) d(theta)/dz, with P in
   hPa and T in kelvin -- the optical form of the Gladstone-Dale relation.
3. The turbulence strength, Cn^2 = 2.8 M^2 L0^(4/3) (Tatarskii), with the
   outer scale L0 from the wind shear by the Dewan et al. (1993) fit, which
   has separate constants for the troposphere and the stratosphere.
4. Integrated over height, then the Fried parameter r0 and the seeing
   FWHM = 0.98 lambda / r0 at 500 nm.

**What it cannot see.** A forecast's levels are hundreds of metres to a
kilometre apart and the turbulent layers are tens of metres thick, so the
gradients it measures are smoothed and the absolute Cn^2 comes out too small.
That is what the `scale` parameter absorbs. And the lowest few hundred
metres -- ground heating, the observer's own roof, the telescope's tube -- are
not in the profile at all; `ground` is a constant stand-in for that floor.
Both are fitted against an independent seeing forecast, see
scripts/compare_seeing.py, rather than chosen by hand.

**Not used by the planner.** Evaluated against Meteoblue at seven sites it
showed no usable skill at telling one night from the next at a fixed site,
and 7Timer ranks sites better. It lives in scripts/ beside that comparison,
as a record of what was tried; the results are in compare_seeing.py.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: Wavelength the seeing is quoted at, metres. 500 nm is the convention.
WAVELENGTH_M = 500e-9
RAD_TO_ARCSEC = 206_264.806

#: Above this height the Dewan stratospheric constants apply. A fixed
#: tropopause is crude -- it is lower at high latitude -- but the stratosphere
#: contributes little turbulence either way, so the boundary barely matters.
TROPOPAUSE_M = 11_000.0

#: Layers thinner than this are skipped. They arise only when two pressure
#: levels are reported at almost the same height, and would divide by ~0.
MIN_LAYER_M = 20.0


@dataclass(frozen=True)
class Level:
    """One pressure level of a forecast profile."""

    pressure_hpa: float
    height_m: float              # geopotential height above sea level
    temperature_c: float
    wind_speed_ms: float
    wind_direction_deg: float    # meteorological: where the wind comes from


def _potential_temperature_k(temperature_c: float, pressure_hpa: float) -> float:
    return (temperature_c + 273.15) * (1000.0 / pressure_hpa) ** 0.2857


def _wind_components(speed: float, direction_deg: float) -> tuple[float, float]:
    rad = math.radians(direction_deg)
    return -speed * math.sin(rad), -speed * math.cos(rad)


def _dewan_outer_scale_43(shear_per_s: float, height_m: float) -> float:
    """L0^(4/3) in m^(4/3), from wind shear, by the Dewan (1993) fit."""
    if height_m < TROPOPAUSE_M:
        exponent = 1.64 + 42.0 * shear_per_s
    else:
        exponent = 0.506 + 50.0 * shear_per_s
    return 0.1 ** (4.0 / 3.0) * 10.0 ** exponent


def turbulence_integral(levels: list[Level], site_elevation_m: float) -> float | None:
    """Integrated Cn^2 above the site, m^(1/3), from the resolved layers alone.

    Levels at or below the ground are dropped -- a model reports 1000 hPa
    everywhere, including under a site at 2 km. Near-surface levels (the
    model's 2-180 m fields) can be passed alongside the pressure levels and
    are what let the lowest, usually worst, layer be resolved at all. None
    when fewer than three usable levels remain.
    """
    above = sorted((lv for lv in levels if lv.height_m > site_elevation_m),
                   key=lambda lv: lv.height_m)
    if len(above) < 3:
        return None

    total = 0.0
    for lower, upper in zip(above, above[1:]):
        dz = upper.height_m - lower.height_m
        if dz < MIN_LAYER_M:
            continue
        theta_lo = _potential_temperature_k(lower.temperature_c, lower.pressure_hpa)
        theta_hi = _potential_temperature_k(upper.temperature_c, upper.pressure_hpa)
        dtheta_dz = (theta_hi - theta_lo) / dz

        u_lo, v_lo = _wind_components(lower.wind_speed_ms, lower.wind_direction_deg)
        u_hi, v_hi = _wind_components(upper.wind_speed_ms, upper.wind_direction_deg)
        shear = math.hypot(u_hi - u_lo, v_hi - v_lo) / dz

        pressure = math.sqrt(lower.pressure_hpa * upper.pressure_hpa)
        temperature_k = (lower.temperature_c + upper.temperature_c) / 2.0 + 273.15
        m = -79e-6 * (pressure / temperature_k ** 2) * dtheta_dz

        mid_height = (lower.height_m + upper.height_m) / 2.0
        cn2 = 2.8 * m * m * _dewan_outer_scale_43(shear, mid_height)
        total += cn2 * dz
    return total


def seeing_from_integral(cn2_integral: float) -> float:
    """Seeing FWHM in arcseconds from integrated Cn^2 (m^(1/3)), at 500 nm."""
    if cn2_integral <= 0:
        return 0.0
    k = 2.0 * math.pi / WAVELENGTH_M
    r0 = (0.423 * k * k * cn2_integral) ** (-3.0 / 5.0)
    return 0.98 * WAVELENGTH_M / r0 * RAD_TO_ARCSEC


def estimate_seeing_arcsec(levels: list[Level], site_elevation_m: float, *,
                           scale: float, ground: float) -> float | None:
    """Estimated seeing FWHM in arcseconds, or None if the profile is too thin.

    Turbulent layers add in Cn^2, which is what makes seeing combine as the
    5/3 power rather than linearly: two layers giving 1" each give 1.52"
    together, not 2". So the calibration is applied in that space --
    `scale` multiplies the resolved profile's contribution, `ground` adds the
    unresolved floor -- and converted back at the end.
    """
    integral = turbulence_integral(levels, site_elevation_m)
    if integral is None:
        return None
    free = seeing_from_integral(integral)
    return (scale * free ** (5.0 / 3.0) + ground) ** (3.0 / 5.0)
