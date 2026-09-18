"""The curated reference data, and the ways it could quietly rot.

`engine/reference.py` is hand-written facts. Nothing computes it, so nothing
checks it — which makes it exactly the kind of file that drifts out of step
with the catalogue it is keyed against and keeps looking fine. These tests
cover the failure modes a reader would not spot: an identifier that no longer
resolves, a showpiece with nothing to say about it, a distance off by orders
of magnitude, a year that is a typo.

They cannot check that Halley really found M13 in 1714. That is a claim about
the world, and the only defence against getting it wrong is the sources named
in the module docstring.
"""

from __future__ import annotations

import pytest

from engine.catalog.loader import load_catalog
from engine.reference import (
    MESSIER_FACTS,
    NGC_FACTS,
    PLANET_FACTS,
    deep_sky_facts,
    physical_diameter_ly,
    planet_facts,
)
from engine.showpieces import showpiece_ids


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


# --- the deep-sky table -----------------------------------------------------

def test_every_ngc_key_still_resolves(catalog):
    """The load-bearing check.

    `deep_sky_facts` returns None for an unknown key rather than raising, so a
    stale identifier does not fail — the object silently loses its facts and
    the panel quietly shrinks. This is what notices.
    """
    present = {obj.name for obj in catalog}
    missing = sorted(set(NGC_FACTS) - present)
    assert not missing, f"reference keys no longer in the catalogue: {missing}"


def test_every_showpiece_has_something_to_say(catalog):
    """The curation is scoped to the objects anyone opens. If a showpiece has
    no entry, the two lists have drifted apart."""
    ids = showpiece_ids(catalog)
    bare = sorted(obj.name for obj in catalog
                  if obj.name in ids and not deep_sky_facts(obj.name, obj.messier))
    assert not bare, f"showpieces with no reference data: {bare}"


def test_messier_numbers_are_real(catalog):
    """A typo in a dict key is invisible: M1O1 is not a thing, but 1O1 would
    just never match."""
    real = {obj.messier for obj in catalog if obj.messier}
    assert set(MESSIER_FACTS) <= real


@pytest.mark.parametrize("messier,low,high", [
    (45, 400, 500),            # Pleiades, ~444 ly and very well determined
    (13, 20000, 25000),        # Hercules cluster
    (31, 2_400_000, 2_700_000),  # Andromeda
    (42, 1200, 1500),          # Orion Nebula
    (57, 2000, 2700),          # Ring Nebula
    (51, 20_000_000, 31_000_000),  # Whirlpool
])
def test_landmark_distances_are_in_the_right_range(messier, low, high):
    """Order-of-magnitude bounds on the objects whose distances are settled.

    Deliberately wide: the point is to catch a decimal slip or a light-years
    /parsecs mix-up, not to pin a value the literature still argues about.
    """
    facts = MESSIER_FACTS[messier]
    assert facts.distance_ly is not None
    assert low <= facts.distance_ly <= high


def test_no_distance_is_absurd():
    """Nothing in a visual observing catalogue is further than the observable
    universe, and nothing is closer than the nearest star."""
    for table in (MESSIER_FACTS, NGC_FACTS):
        for key, facts in table.items():
            if facts.distance_ly is None:
                continue
            assert 4 <= facts.distance_ly <= 14_000_000_000, key


def test_discovery_years_are_plausible():
    """Between Ptolemy and now, and never in the future.

    Negative years are BCE — Hipparchus and the Double Cluster — and must stay
    negative rather than being normalised away, since the display depends on
    the sign to write "130 BCE" instead of "130".
    """
    for table in (MESSIER_FACTS, NGC_FACTS):
        for key, facts in table.items():
            if facts.discovered_year is None:
                continue
            assert -400 <= facts.discovered_year <= 2026, key
            assert facts.discovered_year != 0, f"{key}: there is no year zero"


def test_a_discovery_year_always_comes_with_a_discoverer_or_deliberately_not():
    """The Magellanic Clouds have no discoverer and no year, which is right.
    What must not happen is a year with no name attached and no reason."""
    for table in (MESSIER_FACTS, NGC_FACTS):
        for key, facts in table.items():
            if facts.discovered_year is not None:
                assert facts.discovered_by, (
                    f"{key} has a discovery year but nobody to credit"
                )


def test_unknown_objects_get_nothing_rather_than_something(catalog):
    """Twelve thousand catalogue entries have no curated facts, and the right
    answer for them is None -- the UI drops the row entirely."""
    ids = showpiece_ids(catalog)
    anonymous = next(obj for obj in catalog
                     if obj.name not in ids and obj.messier is None)
    assert deep_sky_facts(anonymous.name, anonymous.messier) is None


def test_messier_number_wins_over_the_identifier(catalog):
    """M45 is `Mel022` in this release and could be something else in the
    next. Keying on the Messier number first is what makes that survive."""
    pleiades = next(obj for obj in catalog if obj.messier == 45)
    assert deep_sky_facts(pleiades.name, 45) is MESSIER_FACTS[45]
    assert deep_sky_facts("a-name-that-does-not-exist", 45) is MESSIER_FACTS[45]


# --- the planets ------------------------------------------------------------

def test_every_planet_the_engine_reports_has_facts():
    """`ALL_PLANETS` drives the Planets tab; a gap here is a blank panel."""
    from engine.planets import ALL_PLANETS

    missing = [name for name in ALL_PLANETS if planet_facts(name) is None]
    assert not missing, f"planets with no reference data: {missing}"


def test_the_naked_eye_planets_name_no_discoverer():
    """Not missing data: there is genuinely nobody to credit for a planet
    every ancient culture could see. The UI says so explicitly rather than
    leaving the row out."""
    for name in ("mercury", "venus", "mars", "jupiter", "saturn"):
        assert PLANET_FACTS[name].discovered_by is None
        assert PLANET_FACTS[name].discovered_year is None


def test_the_telescopic_planets_do_name_one():
    assert PLANET_FACTS["uranus"].discovered_year == 1781
    assert "Herschel" in PLANET_FACTS["uranus"].discovered_by
    assert PLANET_FACTS["neptune"].discovered_year == 1846


def test_retrograde_rotation_is_signed_not_dropped():
    """Venus and Uranus turn backwards, and the sign is how the UI knows to
    say so. An absolute value would lose the most interesting fact about
    Venus: its day is longer than its year, and it runs the wrong way."""
    assert PLANET_FACTS["venus"].rotation_hours < 0
    assert PLANET_FACTS["uranus"].rotation_hours < 0
    assert abs(PLANET_FACTS["venus"].rotation_hours) / 24 > (
        PLANET_FACTS["venus"].year_earth_years * 365.25
    )


def test_planet_sizes_are_ordered_as_they_actually_are():
    """A cheap guard on transcription: the gas giants are the big ones."""
    by_size = sorted(PLANET_FACTS, key=lambda n: -PLANET_FACTS[n].equatorial_diameter_km)
    assert by_size[:2] == ["jupiter", "saturn"]
    assert by_size[-1] == "mercury"


def test_planet_lookup_is_case_and_space_insensitive():
    assert planet_facts(" Jupiter ") is PLANET_FACTS["jupiter"]
    assert planet_facts("moon") is None


# --- true size: distance times apparent size --------------------------------

def test_the_small_angle_geometry_is_right():
    """One radian at one light year is one light year across."""
    import math

    assert physical_diameter_ly(1.0, math.degrees(1.0) * 60) == pytest.approx(1.0)
    # Halve the distance, halve the size; double the angle, double the size.
    assert physical_diameter_ly(100, 10) == pytest.approx(
        2 * physical_diameter_ly(50, 10))
    assert physical_diameter_ly(100, 20) == pytest.approx(
        2 * physical_diameter_ly(100, 10))


@pytest.mark.parametrize("messier,accepted_ly,tolerance", [
    (31, 152_000, 0.35),     # Andromeda
    (27, 2.9, 0.35),         # Dumbbell
    (57, 1.0, 0.40),         # Ring
    (1, 11, 0.45),           # Crab
    (13, 145, 0.40),         # Hercules cluster
])
def test_computed_sizes_land_near_the_accepted_ones(catalog, messier,
                                                    accepted_ly, tolerance):
    """Loose bounds on purpose.

    The limit is not the arithmetic, it is what a catalogue's angular size
    means: an isophotal extent, cut at whatever brightness the survey chose.
    These are order-of-magnitude checks that catch a unit slip -- arcminutes
    read as degrees would be 60x out -- not a claim to precision.
    """
    obj = next(o for o in catalog if o.messier == messier)
    computed = physical_diameter_ly(MESSIER_FACTS[messier].distance_ly,
                                    obj.size_arcmin)
    assert computed is not None
    assert abs(computed - accepted_ly) / accepted_ly <= tolerance, (
        f"M{messier}: computed {computed:,.1f} ly against an accepted "
        f"{accepted_ly:,} ly"
    )


def test_a_missing_input_yields_no_answer():
    """Most of the catalogue has no curated distance, and the UI drops the row
    on None. A zero or a NaN would be printed."""
    assert physical_diameter_ly(None, 10) is None
    assert physical_diameter_ly(1000, None) is None
    assert physical_diameter_ly(1000, 0) is None
    assert physical_diameter_ly(0, 10) is None
    assert physical_diameter_ly(-5, 10) is None


def test_no_showpiece_gets_an_absurd_size(catalog):
    """Nothing in a visual catalogue is smaller than a solar system or larger
    than a galaxy cluster. A unit error would blow straight through both."""
    for obj in catalog:
        facts = deep_sky_facts(obj.name, obj.messier)
        if facts is None:
            continue
        size = physical_diameter_ly(facts.distance_ly, obj.size_arcmin)
        if size is None:
            continue
        assert 0.001 <= size <= 2_000_000, f"{obj.name}: {size} ly across"
