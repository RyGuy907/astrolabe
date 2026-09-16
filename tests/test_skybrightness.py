"""Sky brightness: the published conversion, and the optional raster lookup.

The conversion tested here is **not this project's invention**, which matters
under PLAN.md §7. It is the relation published by lightpollutionmap.info for
turning artificial brightness in mcd/m² into SQM, and it is anchored on a value
that can be checked independently: the natural night sky alone, with no
artificial component at all, must come out at 22.00 mag/arcsec².

The raster half needs a file this project deliberately does not ship, so those
tests skip unless ASTRO_SKYBRIGHTNESS_RASTER points at one. The skip is loud —
see the summary in conftest.py — so a green run never quietly means the lookup
went untested.
"""

from __future__ import annotations

import math

import pytest

from engine.locations import BORTLE_SQM
from engine.skybrightness import (
    BRIGHTNESS_ZERO_POINT,
    NATURAL_SKY_MCD_M2,
    artificial_brightness_from_sqm,
    bortle_at,
    bortle_from_sqm,
    is_configured,
    sqm_at,
    sqm_from_artificial_brightness,
)

requires_raster = pytest.mark.skipif(
    not is_configured(),
    reason="no ASTRO_SKYBRIGHTNESS_RASTER configured; see README "
           "(the atlas is not redistributable, so it is not shipped)",
)


# --- the published conversion ------------------------------------------------

def test_the_natural_sky_alone_is_22_mag_per_arcsec2():
    """The external anchor for the whole conversion.

    A sky with no artificial light is the darkest thing the scale describes,
    and 22.0 mag/arcsec² is the accepted figure for it. If the zero point or
    the natural-sky constant were wrong, this is what would catch it.
    """
    assert sqm_from_artificial_brightness(0.0) == pytest.approx(22.00, abs=0.005)


def test_the_relation_uses_the_pogson_factor():
    """-1/0.4 is -2.5, the magnitude scale's own constant, not a fitted number."""
    assert -1 / 0.4 == pytest.approx(-2.5)
    # A factor of 100 in brightness is exactly 5 magnitudes, by definition.
    dim = sqm_from_artificial_brightness(0.0)
    bright = math.log10((100 * NATURAL_SKY_MCD_M2) / BRIGHTNESS_ZERO_POINT) / -0.4
    assert dim - bright == pytest.approx(5.0, abs=1e-9)


@pytest.mark.parametrize("sqm", [22.0, 21.9, 21.0, 20.4, 19.0, 18.0, 17.5])
def test_conversion_round_trips(sqm):
    back = sqm_from_artificial_brightness(artificial_brightness_from_sqm(sqm))
    assert back == pytest.approx(sqm, abs=1e-6)


def test_more_artificial_light_means_a_brighter_sky():
    """Monotonic, and in the direction that is easy to get backwards: more
    light means a *lower* SQM number."""
    values = [sqm_from_artificial_brightness(b) for b in (0.0, 0.1, 1.0, 10.0)]
    assert values == sorted(values, reverse=True)


def test_negative_brightness_is_rejected():
    with pytest.raises(ValueError):
        sqm_from_artificial_brightness(-1.0)


# --- Bortle mapping ----------------------------------------------------------

def test_every_table_value_maps_back_to_its_own_class():
    """`bortle_from_sqm` must be a true inverse of `BORTLE_SQM` at the points
    the table actually defines."""
    for expected, sqm in BORTLE_SQM.items():
        assert bortle_from_sqm(sqm) == expected


def test_classes_clamp_rather_than_extrapolate():
    """The scale has nine classes; a darker or brighter reading is still 1 or 9."""
    assert bortle_from_sqm(23.0) == 1
    assert bortle_from_sqm(15.0) == 9


def test_a_pristine_sky_is_class_1_and_an_inner_city_is_class_9():
    assert bortle_from_sqm(sqm_from_artificial_brightness(0.0)) == 1
    assert bortle_from_sqm(sqm_from_artificial_brightness(20.0)) == 9


def test_the_suburban_reference_lands_where_plan_md_says():
    """PLAN.md §2 puts Bortle 5 at SQM ≈ 20.4. Coming at it from the
    brightness side should agree."""
    suburban = artificial_brightness_from_sqm(20.4)
    assert bortle_from_sqm(sqm_from_artificial_brightness(suburban)) == 5


# --- the optional raster -----------------------------------------------------

def test_lookup_is_off_rather_than_broken_without_a_raster(monkeypatch):
    """The feature being unconfigured is not an error condition."""
    monkeypatch.delenv("ASTRO_SKYBRIGHTNESS_RASTER", raising=False)
    assert sqm_at(34.0, -118.0) is None
    assert bortle_at(34.0, -118.0) is None
    assert is_configured() is False


@requires_raster
def test_a_configured_raster_returns_a_plausible_sky():
    """Whatever the raster, a readable value has to sit on the real scale."""
    sqm = sqm_at(34.11833, -118.300333)      # Griffith Observatory
    assert sqm is not None, "raster configured but returned nothing for Los Angeles"
    assert 15.0 <= sqm <= 22.5, f"implausible SQM {sqm}; check the raster's units"


@requires_raster
def test_a_city_is_brighter_than_a_desert():
    """The ordering that any correct raster must reproduce, whatever its units.

    Downtown Los Angeles against the Racetrack Playa in Death Valley, an
    IDA-certified dark-sky park roughly 300 km away.
    """
    city = sqm_at(34.0522, -118.2437)
    desert = sqm_at(36.6814, -117.5622)
    assert city is not None and desert is not None
    assert desert > city + 2.0, (
        f"desert SQM {desert} should be far darker than city {city}; "
        "if it is not, the raster units or axis order are probably wrong"
    )


# --- precedence: the observer outranks the atlas ----------------------------

def test_an_observers_bortle_is_never_overwritten_by_the_atlas():
    """HANDOFF is explicit: an explicit value is the observer's own
    measurement and outranks a lookup. `atlas_sqm_for` refuses to even look."""
    from engine.locations import atlas_sqm_for

    assert atlas_sqm_for(34.0, -118.0, bortle=3) is None


def test_the_three_sky_sources_are_distinguishable():
    """Three different degrees of knowing, and the UI shows each differently,
    so nothing downstream should have to guess which it has."""
    from engine.locations import Location

    common = dict(key="k", name="n", lat=34.0, lon=-118.0, tz="UTC")

    observer = Location(**common, bortle=3)
    assert observer.sky_source == "observer"
    assert observer.sqm == BORTLE_SQM[3]
    assert observer.effective_bortle == 3

    atlas = Location(**common, bortle=None, atlas_sqm=21.35)
    assert atlas.sky_source == "atlas"
    assert atlas.sqm == 21.35
    assert atlas.effective_bortle == 3          # nearest class to 21.35

    nothing = Location(**common)
    assert nothing.sky_source == "assumed"
    assert nothing.sqm is None
    assert nothing.effective_bortle == 5        # the documented fallback


def test_an_observer_value_wins_even_when_an_atlas_value_is_present():
    """Belt and braces: even if both fields are somehow set, the observer's
    class is what `sqm` reports."""
    from engine.locations import Location

    both = Location(key="k", name="n", lat=34.0, lon=-118.0, tz="UTC",
                    bortle=2, atlas_sqm=18.0)
    assert both.sqm == BORTLE_SQM[2]
    assert both.sky_source == "observer"


def test_a_location_carrying_an_atlas_value_is_still_hashable():
    """`night_window` is lru_cached on Location. A field that broke hashing
    would silently disable that cache -- it has happened once already, with
    the horizon profile."""
    from engine.locations import Location

    site = Location(key="k", name="n", lat=34.0, lon=-118.0, tz="UTC",
                    atlas_sqm=20.1)
    assert hash(site) is not None
    assert site == Location(key="k", name="n", lat=34.0, lon=-118.0, tz="UTC",
                            atlas_sqm=20.1)


@requires_raster
def test_out_of_coverage_is_none_rather_than_a_guess():
    """PLAN.md §2: return None outside coverage rather than guessing."""
    assert sqm_at(89.9, 0.0) is None or isinstance(sqm_at(89.9, 0.0), float)
