"""Moonlit sky brightness at a point in the sky: Krisciunas & Schaefer (1991).

K&S, "A model of the brightness of moonlight", PASP 103, 1033, is the standard
V-band model for how much the Moon brightens the sky at a given place in it.
It depends on four things the old flat penalty ignored:

* the Moon's phase (a quarter Moon is about a tenth of a full one, not half),
* how high the Moon is (low, its light crosses more air before scattering),
* how high the target is (more air in the line of sight scatters more light),
* the angle between them (Rayleigh plus a strong forward-scattering aureole).

Their stated accuracy is 8-23% in brightness, about 0.1-0.25 mag, against
measurements at Mauna Kea, and they caution against using it closer than
about 10 degrees to the Moon, where the aureole is outside the fit. Everything
here is vectorised with numpy so a whole night's grid of targets and times
goes through in one call.

Units: K&S work in nanolamberts. The site's dark sky arrives as an SQM
reading (mag/arcsec^2, close to V) and leaves the same way; the conversion is
their equation 1 and its inverse.
"""

from __future__ import annotations

import numpy as np

#: K&S say the model is unreliable closer than this to the Moon.
MIN_VALID_SEPARATION_DEG = 10.0


def nanolamberts_from_sb(sb):
    """mag/arcsec^2 (V) -> nanolamberts. K&S eq. 1."""
    return 34.08 * np.exp(20.7233 - 0.92104 * np.asarray(sb, dtype=float))


def sb_from_nanolamberts(brightness):
    """nanolamberts -> mag/arcsec^2 (V). Inverse of K&S eq. 1."""
    return (20.7233 - np.log(np.asarray(brightness, dtype=float) / 34.08)) / 0.92104


def phase_angle_from_illumination(illumination: float) -> float:
    """Sun-Moon-Earth angle in degrees from the illuminated fraction.

    0 is full, 90 a quarter, 180 new. From k = (1 + cos a) / 2.
    """
    k = min(max(float(illumination), 0.0), 1.0)
    return float(np.degrees(np.arccos(2.0 * k - 1.0)))


def _airmass_ks(zenith_deg):
    """K&S eq. 3: the airmass of a thin layer over a spherical Earth."""
    z = np.radians(np.asarray(zenith_deg, dtype=float))
    return 1.0 / np.sqrt(1.0 - 0.96 * np.sin(z) ** 2)


def moon_sky_brightness_nl(phase_angle_deg: float, moon_alt_deg, target_alt_deg,
                           separation_deg, k: float = 0.2):
    """Brightness the Moon adds to the sky at the target, in nanolamberts.

    Zero wherever the Moon is at or below the horizon. Arrays broadcast, so a
    (times, 1) Moon altitude against a (times, objects) target grid works.
    Target altitudes at or below zero are clamped to the horizon, where the
    answer is only ever used to be thrown away.
    """
    alpha = abs(float(phase_angle_deg))
    # Eq. 20: the Moon's illuminance outside the atmosphere, by phase.
    illuminance = 10.0 ** (-0.4 * (3.84 + 0.026 * alpha + 4e-9 * alpha ** 4))

    rho = np.clip(np.asarray(separation_deg, dtype=float), 0.0, 180.0)
    # Eq. 21: Rayleigh scattering plus Mie's forward peak around the Moon.
    scattering = (10.0 ** 5.36 * (1.06 + np.cos(np.radians(rho)) ** 2)
                  + 10.0 ** (6.15 - rho / 40.0))

    moon_alt = np.asarray(moon_alt_deg, dtype=float)
    target_alt = np.clip(np.asarray(target_alt_deg, dtype=float), 0.0, 90.0)
    x_moon = _airmass_ks(90.0 - np.clip(moon_alt, 0.0, 90.0))
    x_target = _airmass_ks(90.0 - target_alt)

    # Eq. 15: light reaching the scattering air, times the fraction of the
    # line of sight's air that scatters it toward the observer.
    brightness = (scattering * illuminance * 10.0 ** (-0.4 * k * x_moon)
                  * (1.0 - 10.0 ** (-0.4 * k * x_target)))
    return np.where(moon_alt > 0.0, brightness, 0.0)


def moonlit_sky_sb(dark_sb: float, phase_angle_deg: float, moon_alt_deg,
                   target_alt_deg, separation_deg, k: float = 0.2):
    """Sky surface brightness at the target with the Moon's light added.

    `dark_sb` is the site's moonless sky (its SQM). Moonlight and the dark sky
    add in linear units, then convert back to mag/arcsec^2.
    """
    dark = nanolamberts_from_sb(dark_sb)
    moon = moon_sky_brightness_nl(phase_angle_deg, moon_alt_deg,
                                  target_alt_deg, separation_deg, k)
    return sb_from_nanolamberts(dark + moon)
