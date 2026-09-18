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
    _DIAMETER_LY,
    MESSIER_FACTS,
    NGC_FACTS,
    PLANET_FACTS,
    deep_sky_facts,
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


# --- true size -------------------------------------------------------------
#
# Quoted, not derived. The first version multiplied distance by apparent size,
# which is one line of trigonometry and about 30% low across the board: a
# catalogue diameter is an isophotal extent, cut at whatever brightness the
# survey chose, not the object's edge. M31 came out at 131,000 ly against an
# accepted 152,000 and the Pleiades at 19 against 43. Since the distance
# beside it was already a quoted value, the calculated one was the odd one
# out.

@pytest.mark.parametrize("messier,expected", [
    (31, 152_000),    # Andromeda
    (13, 145),        # Hercules cluster
    (42, 24),         # Orion Nebula
    (57, 1.0),        # Ring Nebula
    (45, 13),         # Pleiades
    (27, 2.9),        # Dumbbell
])
def test_landmark_diameters_are_the_accepted_values(messier, expected):
    """Exact, because these are quoted rather than computed. A test that
    allowed 30% either way would not notice the calculation creeping back."""
    assert MESSIER_FACTS[messier].diameter_ly == pytest.approx(expected)


def test_diameters_are_merged_into_both_tables(catalog):
    """`_DIAMETER_LY` is a separate table in the source -- a diameter and a
    discovery credit come from different references and reading them on one
    line makes neither checkable -- and folded in at import. If the fold
    breaks, every lookup silently loses the field."""
    assert MESSIER_FACTS[31].diameter_ly is not None
    assert NGC_FACTS["NGC5139"].diameter_ly is not None

    andromeda = next(o for o in catalog if o.messier == 31)
    assert deep_sky_facts(andromeda.name, 31).diameter_ly == 152_000


def test_every_diameter_key_matches_a_facts_entry():
    """A key with no matching object is dead data that looks alive."""
    orphans = [key for key in _DIAMETER_LY
               if key not in MESSIER_FACTS and key not in NGC_FACTS]
    assert not orphans, f"diameters keyed to nothing: {orphans}"


def test_no_diameter_is_absurd():
    """A unit slip -- parsecs for light years, or a stray factor of a
    thousand -- would still look like a number."""
    for key, value in _DIAMETER_LY.items():
        assert 0.1 <= value <= 1_000_000, f"{key}: {value} ly across"


def test_a_diameter_never_appears_without_a_distance():
    """They come from the same references. One without the other means one of
    them was transcribed against the wrong object."""
    for table in (MESSIER_FACTS, NGC_FACTS):
        for key, facts in table.items():
            if facts.diameter_ly is not None:
                assert facts.distance_ly is not None, (
                    f"{key} has a diameter but no distance"
                )


def test_a_diameter_is_never_larger_than_its_distance():
    """Not a precision check -- a crude one. Nothing in this catalogue
    subtends anything like a radian, so a diameter exceeding its own distance
    means the two were transcribed in different units."""
    for table in (MESSIER_FACTS, NGC_FACTS):
        for key, facts in table.items():
            if facts.diameter_ly and facts.distance_ly:
                assert facts.diameter_ly < facts.distance_ly, key


# --- the named double stars -------------------------------------------------
#
# These are not in OpenNGC. It is a deep-sky catalogue: its 244 entries typed
# `**` are unnamed NGC pairs, almost all far too faint to point at, and
# Albireo, Mizar and Almach are simply absent. They come from a separate
# vendored CSV built from SIMBAD by `scripts/fetch_double_stars.py`.

def test_the_famous_doubles_are_in_the_catalogue(catalog):
    """The ones anyone would look for by name."""
    by_name = {o.display_name: o for o in catalog}
    for name in ("Albireo", "Mizar", "Almach", "Cor Caroli", "Castor",
                 "The Double Double", "Izar", "Algieba"):
        assert name in by_name, f"{name} is not in the catalogue"
        assert by_name[name].group == "Double Stars"


def test_a_double_shows_its_own_name_not_our_bookkeeping(catalog):
    """Their identifiers are ones this project invented for the vendored CSV,
    so `DBLAlbireo (Albireo)` would be showing internal plumbing to the
    reader. They are known by their name and nothing else."""
    albireo = next(o for o in catalog if o.name == "DBLAlbireo")
    assert albireo.display_name == "Albireo"


def test_every_double_carries_the_facts_that_make_it_worth_finding(catalog):
    """Separation and component magnitudes, which decide whether a given
    telescope can split it -- the only question that matters for a double and
    one no deep-sky field answers."""
    doubles = [o for o in catalog if o.name.startswith("DBL")]
    assert len(doubles) >= 15

    for obj in doubles:
        facts = deep_sky_facts(obj.name, obj.messier)
        assert facts is not None, obj.name
        assert facts.separation_arcsec, obj.name
        assert facts.component_mags, obj.name
        assert facts.note, obj.name


def test_double_star_coordinates_are_where_they_should_be(catalog):
    """A spot check against figures independent of the fetch script.

    Albireo is 19h 30m 43s +27 58' and Alpha Centauri is 14h 39m 36s
    -60 50'. If the sexagesimal conversion or the SIMBAD query drifted, these
    would move by degrees rather than arcseconds.
    """
    albireo = next(o for o in catalog if o.name == "DBLAlbireo")
    assert albireo.ra_deg == pytest.approx(292.680, abs=0.01)
    assert albireo.dec_deg == pytest.approx(27.960, abs=0.01)

    alpha_cen = next(o for o in catalog if o.name == "DBLAlphaCentauri")
    assert alpha_cen.ra_deg == pytest.approx(219.902, abs=0.05)
    assert alpha_cen.dec_deg == pytest.approx(-60.834, abs=0.05)


def test_double_star_distances_are_parallax_derived_and_sane(catalog):
    """Unlike every other distance here these come from parallax, and for
    nearby stars that is trustworthy -- Gaia measured them directly. Alpha
    Centauri at 4.3 ly is the check that the conversion is right."""
    facts = deep_sky_facts("DBLAlphaCentauri", None)
    assert facts.distance_ly == pytest.approx(4.3, abs=0.3)

    achird = deep_sky_facts("DBLAchird", None)
    assert achird.distance_ly == pytest.approx(19.3, abs=1.0)


def test_a_double_is_never_judged_on_surface_brightness(catalog):
    """Surface brightness describes a glow spread over an area, and a pair of
    point sources does not have one.

    The guard is group membership, not geometry. `is_extended` is purely a
    size test and the Double Double genuinely spans 3.5 arcminutes, so it
    passes one; what keeps the contrast test away from it is that
    `DIFFUSE_GROUPS` is galaxies and nebulae only, and a double is scored on
    integrated magnitude like a cluster.
    """
    from engine.targets import DIFFUSE_GROUPS

    doubles = [o for o in catalog if o.name.startswith("DBL")]
    assert doubles
    for obj in doubles:
        assert obj.group not in DIFFUSE_GROUPS, obj.name
