"""Scope math against hand-computable values.

The AD8 is 203 mm f/5.9, 1200 mm focal length, so every expected number below
can be checked with a calculator rather than by running the code.

Eyepiece calculations were removed at the user's request, so there is nothing
here about true field, magnification or exit pupil. What remains is the
aperture-derived detectability math that target filtering depends on.
"""

from __future__ import annotations

import pytest

from engine.equipment import (
    airmass,
    extinction_mag,
    limiting_mag_at_altitude,
    load_equipment,
    naked_eye_limiting_mag,
    telescopic_gain,
    telescopic_limiting_mag,
)
from engine.locations import BORTLE_SQM, get_location


@pytest.fixture(scope="module")
def kit():
    return load_equipment()


@pytest.fixture(scope="module")
def ad8(kit):
    return kit.scope()


def test_default_scope_is_the_ad8(kit, ad8):
    assert kit.default_scope == "ad8"
    assert ad8.aperture_mm == 203
    assert ad8.focal_length_mm == 1200
    assert kit.eye_pupil_mm == 6.5


def test_equipment_has_no_eyepiece_concept(kit):
    """Removed at the user's request as common knowledge.

    Pinned as a test so it does not quietly creep back in.
    """
    assert not hasattr(kit, "eyepieces")

    import engine.equipment as equipment

    for name in ("Eyepiece", "FramingOption", "magnification", "true_fov_deg",
                 "exit_pupil_mm", "framing_options", "best_eyepiece",
                 "recommend_eyepiece"):
        assert not hasattr(equipment, name), f"{name} should have been removed"


def test_an_eyepieces_section_in_config_is_ignored(tmp_path):
    """Leftover config must not resurrect the feature or crash the loader."""
    config = tmp_path / "equipment.yaml"
    config.write_text(
        "default_scope: s\n"
        "scopes:\n  s:\n    name: Test\n    aperture_mm: 200\n"
        "    focal_length_mm: 1200\n"
        "eyepieces:\n  - {focal_length_mm: 30, afov_deg: 52, name: '30mm'}\n"
        "observer:\n  eye_pupil_mm: 6.5\n",
        encoding="utf-8",
    )
    kit = load_equipment(config)
    assert kit.scope("s").aperture_mm == 200
    assert not hasattr(kit, "eyepieces")


def test_unknown_scope_key_raises(kit):
    with pytest.raises(KeyError):
        kit.scope("celestron_c8")


# --- limiting magnitude -----------------------------------------------------

def test_telescopic_gain_matches_plan_estimate(ad8):
    """PLAN.md 3.3: 203 mm over a ~7 mm pupil is about +7.3 mag.

    This is an aperture property, independent of the eyepiece in use.
    """
    assert telescopic_gain(203, 7.0) == pytest.approx(7.30, abs=0.02)
    assert telescopic_gain(203, 6.5) == pytest.approx(7.46, abs=0.02)


def test_telescopic_gain_rejects_nonpositive():
    with pytest.raises(ValueError):
        telescopic_gain(0, 6.5)
    with pytest.raises(ValueError):
        telescopic_gain(203, 0)


def test_naked_eye_limiting_mag_anchors():
    """The fit is pinned at the Bortle 1 and Bortle 8 endpoints."""
    assert naked_eye_limiting_mag(BORTLE_SQM[1]) == pytest.approx(7.8, abs=0.01)
    assert naked_eye_limiting_mag(BORTLE_SQM[8]) == pytest.approx(4.3, abs=0.01)
    # Darker sky must never yield a fainter limit.
    values = [naked_eye_limiting_mag(BORTLE_SQM[b]) for b in range(1, 10)]
    assert values == sorted(values, reverse=True)


def test_limiting_mag_at_home_site(ad8):
    """Bortle 5 (SQM 20.4) with the AD8: about mag 13.9 at zenith.

    Naked eye 4.3 + (20.4-18.0)*3.5/3.9 = 6.45, plus 5*log10(203/6.5) = 7.47.
    """
    # An explicit SQM, not whatever the example config currently ships: this
    # test is about the limiting-magnitude maths, and pinning it to a config
    # value made it fail when the seeded site changed.
    sqm = BORTLE_SQM[5]
    assert sqm == 20.4
    assert naked_eye_limiting_mag(sqm) == pytest.approx(6.45, abs=0.01)
    assert telescopic_limiting_mag(ad8, sqm, 6.5) == pytest.approx(13.93, abs=0.02)


# --- airmass and extinction -------------------------------------------------

def test_airmass_at_zenith_is_one():
    assert airmass(90.0) == pytest.approx(1.0, abs=0.001)


def test_airmass_increases_toward_horizon():
    assert airmass(30.0) == pytest.approx(2.0, abs=0.02)
    assert airmass(10.0) > 5.0
    # Pickering stays finite at the horizon where a plain secant diverges.
    assert airmass(0.5) < 100


def test_airmass_below_horizon_raises():
    with pytest.raises(ValueError):
        airmass(0.0)
    with pytest.raises(ValueError):
        airmass(-10.0)


def test_extinction_costs_about_two_tenths_at_30_degrees():
    """k=0.2, airmass 2 -> 0.2 mag lost."""
    assert extinction_mag(30.0) == pytest.approx(0.2, abs=0.01)
    assert extinction_mag(90.0) == pytest.approx(0.0, abs=0.001)


def test_limiting_mag_drops_with_altitude(ad8):
    zenith = limiting_mag_at_altitude(ad8, 20.4, 90.0, 6.5)
    low = limiting_mag_at_altitude(ad8, 20.4, 25.0, 6.5)
    assert zenith > low
    assert zenith - low == pytest.approx(extinction_mag(25.0), abs=1e-6)
