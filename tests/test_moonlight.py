"""Krisciunas & Schaefer moonlit sky (engine/moonlight.py) and its use in targets."""

from __future__ import annotations

import numpy as np
import pytest
from conftest import requires_ephemeris

from engine.moonlight import (
    moon_sky_brightness_nl,
    moonlit_sky_sb,
    nanolamberts_from_sb,
    phase_angle_from_illumination,
    sb_from_nanolamberts,
)


def test_units_round_trip_and_anchor():
    # K&S eq. 1: 21.9 mag/arcsec^2 is about 59 nL, a dark site's sky.
    assert float(nanolamberts_from_sb(21.9)) == pytest.approx(59.2, abs=0.2)
    assert float(sb_from_nanolamberts(nanolamberts_from_sb(18.3))) == pytest.approx(18.3)


def test_phase_angle_from_illumination():
    assert phase_angle_from_illumination(1.0) == pytest.approx(0.0)
    assert phase_angle_from_illumination(0.5) == pytest.approx(90.0)
    assert phase_angle_from_illumination(0.0) == pytest.approx(180.0)


def test_a_full_moon_puts_a_dark_site_near_18_5():
    """Measured full-Moon skies at dark sites run about 18-19 mag/arcsec^2.
    K&S's own worked geometry: Moon 60 deg up, target overhead, 60 deg away."""
    sky = float(moonlit_sky_sb(21.9, 0.0, 60.0, 90.0, 60.0, k=0.2))
    assert 18.0 < sky < 19.0


def test_no_moon_means_the_dark_sky():
    assert float(moonlit_sky_sb(21.5, 0.0, -5.0, 60.0, 90.0)) == pytest.approx(21.5)
    assert float(moon_sky_brightness_nl(0.0, -0.1, 60.0, 90.0)) == 0.0


def test_brighter_nearer_the_moon_and_lower_in_the_sky():
    near, far = moonlit_sky_sb(21.5, 30.0, 40.0, 60.0, np.array([15.0, 90.0]))
    assert near < far                               # smaller mag = brighter
    low, high = moonlit_sky_sb(21.5, 30.0, 40.0, np.array([15.0, 80.0]), 90.0)
    assert low < high


def test_a_quarter_moon_is_about_a_tenth_of_a_full_one():
    """The phase curve, not illumination, sets the light: 50% lit is ~9x dimmer."""
    full = float(moon_sky_brightness_nl(0.0, 45.0, 60.0, 90.0))
    quarter = float(moon_sky_brightness_nl(90.0, 45.0, 60.0, 90.0))
    assert 6.0 < full / quarter < 14.0


def test_moonlight_costs_a_bright_sky_less_than_a_dark_one():
    """Light adds: the same Moon over a suburb brightens it by fewer
    magnitudes than over a dark site. The flat model charged both 3 mag."""
    dark_cost = 21.8 - float(moonlit_sky_sb(21.8, 0.0, 45.0, 60.0, 90.0))
    suburb_cost = 19.4 - float(moonlit_sky_sb(19.4, 0.0, 45.0, 60.0, 90.0))
    assert dark_cost > suburb_cost + 1.0


@requires_ephemeris
def test_targets_accept_only_known_moon_models(reference_night):
    from engine.equipment import load_equipment
    from engine.targets import assess_targets

    with pytest.raises(ValueError):
        assess_targets(reference_night, load_equipment(), moon_model="nonsense",
                       catalog=[])


@requires_ephemeris
def test_the_ks_model_assesses_a_night(reference_night):
    """Smoke test: every object judged, each with a limit and a sky reading."""
    from engine.equipment import load_equipment
    from engine.targets import assess_targets

    results = assess_targets(reference_night, load_equipment(), min_altitude_deg=0.0,
                             include_too_faint=True, moon_model="ks")
    assert results
    assert all(np.isfinite(a.limiting_mag_at_peak) for a in results)
