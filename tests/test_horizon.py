"""Obstruction horizon profiles: interpolation, wrapping, and effect on targets."""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from conftest import REFERENCE_DATE, requires_ephemeris
from engine.catalog.loader import load_catalog
from engine.ephem import night_window
from engine.equipment import load_equipment
from engine.horizon import (
    DIRECTIONAL,
    FLAT,
    PRESETS,
    HorizonProfile,
    build,
    parse_horizon,
    preset,
    serialise,
)
from engine.locations import get_location
from engine.targets import DEFAULT_MIN_ALTITUDE_DEG, assess_targets


@pytest.fixture(scope="module")
def kit():
    return load_equipment()


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


# --- presets ----------------------------------------------------------------

def test_flat_is_the_default_and_blocks_nothing():
    assert FLAT.is_flat
    assert FLAT.max_obstruction_deg == 0.0
    for azimuth in range(0, 360, 30):
        assert FLAT.min_altitude_at(azimuth) == 0.0


def test_every_preset_loads():
    for name in PRESETS:
        profile = preset(name)
        assert profile.name == name
        assert profile.points


def test_only_flat_is_not_flagged_generic():
    """The presets are assumptions; flagging them is the honesty mechanism."""
    assert preset("flat").is_generic is False
    for name in ("hilly", "ridge", "trees"):
        assert preset(name).is_generic is True, f"{name} must be flagged generic"


def test_an_explicit_map_is_not_flagged_generic():
    """A measured horizon is real data, so it is not labelled a guess."""
    profile = parse_horizon({0: 18, 90: 5, 180: 5, 270: 22})
    assert profile.is_generic is False
    assert profile.name == "custom"


def test_unknown_preset_raises_with_the_known_names():
    with pytest.raises(KeyError) as exc:
        preset("mountains")
    assert "hilly" in str(exc.value)


# --- interpolation ----------------------------------------------------------

def test_single_point_profile_is_uniform():
    profile = parse_horizon({0: 15.0})
    for azimuth in (0, 90, 180, 270, 359):
        assert profile.min_altitude_at(azimuth) == 15.0


def test_interpolates_between_points():
    profile = parse_horizon({0: 0.0, 180: 20.0})
    assert profile.min_altitude_at(0) == pytest.approx(0.0)
    assert profile.min_altitude_at(90) == pytest.approx(10.0)
    assert profile.min_altitude_at(180) == pytest.approx(20.0)


def test_interpolation_wraps_across_north():
    """A profile with points at 270 and 0 must interpolate the short way.

    Going the long way round would put the minimum in completely the wrong
    part of the sky.
    """
    profile = parse_horizon({0: 10.0, 270: 30.0})
    # Halfway from 270 back to 360/0 is 315.
    assert profile.min_altitude_at(315) == pytest.approx(20.0)


def test_azimuth_is_taken_modulo_360():
    profile = parse_horizon({0: 0.0, 180: 20.0})
    assert profile.min_altitude_at(450) == profile.min_altitude_at(90)
    assert profile.min_altitude_at(-90) == profile.min_altitude_at(270)


def test_clears_compares_against_the_profile():
    profile = parse_horizon({0: 20.0})
    assert profile.clears(0, 25.0) is True
    assert profile.clears(0, 15.0) is False
    assert profile.clears(0, 20.0) is True          # exactly at the limit


# --- do the presets actually do anything ------------------------------------

def test_the_binding_presets_rise_above_the_default_altitude_floor():
    """The bug this whole preset set exists to fix.

    The effective floor is `max(min_altitude, horizon)`, so a profile whose
    maximum sits under the altitude floor cannot change a single result. Every
    preset used to top out at or below 25 deg, which meant the horizon feature
    was inert for every site in the web UI, where the floor is fixed at 25.
    """
    for name in ("trees", "ridge", "valley"):
        assert preset(name).max_obstruction_deg > DEFAULT_MIN_ALTITUDE_DEG, (
            f"{name} cannot affect a result at the default floor"
        )


def test_the_non_binding_presets_are_deliberate_and_documented():
    """`flat` and `hilly` are below the floor on purpose, not by oversight.

    Distant rolling terrain genuinely does not matter if you are not observing
    below 25 deg. They stay selectable so a site can be described honestly;
    the UI says outright that they change nothing.
    """
    for name in ("flat", "hilly"):
        assert preset(name).max_obstruction_deg <= DEFAULT_MIN_ALTITUDE_DEG


# --- direction --------------------------------------------------------------

def test_rotating_moves_the_obstruction_to_the_bearing():
    north = preset("ridge")
    west = preset("ridge", facing=270)
    assert north.min_altitude_at(0) == pytest.approx(35.0)
    assert west.min_altitude_at(270) == pytest.approx(35.0)
    # and the direction it came from is now open
    assert west.min_altitude_at(90) == pytest.approx(north.min_altitude_at(180))


def test_rotation_preserves_the_shape():
    """Same profile, read from a different starting azimuth."""
    plain, turned = preset("valley"), preset("valley", facing=130)
    for azimuth in range(0, 360, 10):
        assert turned.min_altitude_at(azimuth + 130) == pytest.approx(
            plain.min_altitude_at(azimuth))


def test_rotation_records_the_bearing_and_keeps_the_name():
    turned = preset("ridge", facing=250)
    assert turned.name == "ridge"
    assert turned.facing == 250
    assert turned.is_generic is True


def test_an_unrotated_preset_has_no_bearing():
    """None means "never rotated", which is different from "facing north"."""
    assert preset("ridge").facing is None


def test_only_the_directional_presets_are_flagged_directional():
    assert DIRECTIONAL == {"ridge", "valley"}
    for name in DIRECTIONAL:
        assert name in PRESETS


def test_a_rotated_profile_is_still_hashable():
    assert hash(preset("ridge", facing=250)) is not None


def test_build_rejoins_a_preset_and_a_separately_supplied_bearing():
    assert build("ridge", 90).facing == 90
    assert build("ridge").facing is None
    assert build(None).is_flat


# --- serialisation round trip -----------------------------------------------

@pytest.mark.parametrize("profile", [
    FLAT,
    preset("trees"),
    preset("ridge", facing=250),
    parse_horizon({0: 31, 90: 6, 180: 4, 270: 24}),
])
def test_every_profile_survives_a_serialise_parse_round_trip(profile):
    """`db/store` has one TEXT column for the horizon, so everything a profile
    carries has to fit in one string and come back identical."""
    back = parse_horizon(serialise(profile))
    assert back.points == profile.points
    assert back.name == profile.name
    assert back.facing == profile.facing
    assert back.is_generic == profile.is_generic


def test_a_measured_profile_does_not_come_back_as_a_fabricated_flat_one():
    """The specific regression.

    `db/store.save_location` used to write `horizon.name`, which is "custom"
    for a measured map, and the ternary turned that into NULL. It reloaded as
    flat with `is_generic=False` — reading downstream as a *measured,
    unobstructed* horizon. A fabricated value wearing the "measured" label is
    worse than an honest preset.
    """
    measured = parse_horizon({0: 31, 90: 6, 180: 4, 270: 24})
    back = parse_horizon(serialise(measured))
    assert not back.is_flat
    assert back.max_obstruction_deg == pytest.approx(31.0)


def test_a_preset_with_a_bearing_parses_from_its_packed_string():
    profile = parse_horizon("ridge@250")
    assert profile.name == "ridge" and profile.facing == 250


def test_a_mapping_can_name_a_preset_and_a_bearing():
    """The readable YAML form: `horizon: {preset: ridge, facing: 250}`."""
    profile = parse_horizon({"preset": "ridge", "facing": 250})
    assert profile.name == "ridge"
    assert profile.facing == 250
    assert profile.is_generic is True


def test_a_bare_mapping_is_still_read_as_a_measured_map():
    """Adding the {preset: ...} form must not swallow the measured form."""
    profile = parse_horizon({0: 18, 90: 5})
    assert profile.name == "custom"
    assert profile.is_generic is False


def test_a_nonsense_bearing_is_rejected(): 
    with pytest.raises(ValueError):
        parse_horizon("ridge@sideways")


# --- validation -------------------------------------------------------------

def test_an_empty_profile_is_rejected():
    with pytest.raises(ValueError):
        HorizonProfile(points=())


def test_out_of_range_values_are_rejected():
    with pytest.raises(ValueError):
        HorizonProfile(points=((0, 120.0),))
    with pytest.raises(ValueError):
        HorizonProfile(points=((400, 10.0),))


def test_a_profile_is_hashable():
    """Location embeds one and is frozen and lru_cached; a dict field would
    have silently made Location unhashable and broken night_window's cache."""
    assert hash(preset("hilly")) is not None
    assert hash(get_location("home")) is not None


# --- config wiring ----------------------------------------------------------

def test_seeded_locations_use_the_hilly_preset():
    for key in ("home", "santa_monica_mtns"):
        horizon = get_location(key).horizon
        assert horizon.name == "hilly"
        assert horizon.is_generic is True


def test_a_location_without_a_horizon_key_gets_flat(tmp_path):
    from engine.locations import load_locations

    config = tmp_path / "locations.yaml"
    config.write_text(
        "default: x\nlocations:\n  x:\n    name: X\n    lat: 34.0\n"
        "    lon: -118.0\n    tz: UTC\n",
        encoding="utf-8",
    )
    assert load_locations(config)["x"].horizon.is_flat


# --- persistence ------------------------------------------------------------
#
# These go through real SQLite rather than just serialise/parse, because the
# bug they cover was in `db/store`, not in the codec. Every one writes to
# `tmp_path`; none of them can see the real location database.

@pytest.mark.parametrize("profile", [
    FLAT,
    preset("hilly"),
    preset("trees"),
    preset("ridge", facing=250),
    parse_horizon({0: 31, 90: 6, 180: 4, 270: 24}),
])
def test_a_horizon_survives_the_sqlite_round_trip(tmp_path, profile):
    from db import store
    from engine.locations import Location

    database = tmp_path / "locations.sqlite3"
    site = Location(key="probe", name="Probe", lat=34.0, lon=-118.0,
                    elevation_m=300.0, bortle=5, tz="America/Los_Angeles",
                    horizon=profile)
    store.save_location(site, database)

    back = store.stored_locations(database)["probe"].horizon
    assert back.points == profile.points
    assert back.name == profile.name
    assert back.facing == profile.facing
    assert back.is_generic == profile.is_generic


def test_a_stored_measured_horizon_does_not_reload_as_a_fake_flat_one(tmp_path):
    """The regression, at the layer it actually happened in.

    The store wrote `horizon.name` — "custom" for a measured map — through a
    ternary that turned it into NULL, so the profile came back flat *and*
    `is_generic=False`: an invented horizon labelled as measured. Losing the
    data would have been bad; mislabelling the loss was worse.
    """
    from db import store
    from engine.locations import Location

    database = tmp_path / "locations.sqlite3"
    measured = parse_horizon({0: 31, 90: 6, 180: 4, 270: 24})
    store.save_location(
        Location(key="probe", name="Probe", lat=34.0, lon=-118.0,
                 elevation_m=300.0, bortle=5, tz="America/Los_Angeles",
                 horizon=measured),
        database,
    )

    back = store.stored_locations(database)["probe"].horizon
    assert not back.is_flat, "a measured horizon was silently lost"
    assert back.max_obstruction_deg == pytest.approx(31.0)


# --- effect on target selection ---------------------------------------------

@requires_ephemeris
def test_a_horizon_below_the_floor_changes_nothing(home, kit, catalog):
    """The hilly preset tops out at 12 deg, under the default 25 deg floor."""
    flat_site = dataclasses.replace(home, horizon=FLAT)

    with_horizon = assess_targets(night_window(REFERENCE_DATE, home), kit,
                                  catalog=catalog, min_altitude_deg=25.0)
    without = assess_targets(night_window(REFERENCE_DATE, flat_site), kit,
                             catalog=catalog, min_altitude_deg=25.0)
    assert len(with_horizon) == len(without)


@requires_ephemeris
def test_a_horizon_above_the_floor_removes_targets(home, kit, catalog):
    """Lower the floor under the obstruction and the profile starts to bite."""
    flat_site = dataclasses.replace(home, horizon=FLAT)
    ridge_site = dataclasses.replace(home, horizon=preset("ridge"))

    without = assess_targets(night_window(REFERENCE_DATE, flat_site), kit,
                             catalog=catalog, min_altitude_deg=5.0)
    with_ridge = assess_targets(night_window(REFERENCE_DATE, ridge_site), kit,
                                catalog=catalog, min_altitude_deg=5.0)

    assert len(with_ridge) < len(without)


def _by_name(site, kit, catalog, floor=5.0):
    return {a.obj.name: a for a in
            assess_targets(night_window(REFERENCE_DATE, site), kit,
                           catalog=catalog, min_altitude_deg=floor)}


@requires_ephemeris
def test_a_steeper_horizon_never_lengthens_an_objects_window(home, kit, catalog):
    """The invariant that actually holds: the horizon only raises the floor.

    This replaces an assertion that the *number* of passing targets falls
    monotonically as the horizon steepens. It does not, and the next test
    documents why. What is genuinely guaranteed is per-object: no object can
    spend more time above a higher floor.
    """
    flat_site = dataclasses.replace(home, horizon=FLAT)
    ridge_site = dataclasses.replace(home, horizon=preset("ridge"))

    flat = _by_name(flat_site, kit, catalog)
    ridge = _by_name(ridge_site, kit, catalog)

    shared = set(flat) & set(ridge)
    assert shared, "expected some objects to clear both horizons"
    for name in shared:
        assert ridge[name].hours_above_floor <= flat[name].hours_above_floor + 1e-9, (
            f"{name} gained observable time under a steeper horizon"
        )


@requires_ephemeris
def test_a_steeper_horizon_can_admit_targets_a_shallower_one_rejects(home,
                                                                     kit,
                                                                     catalog):
    """Non-monotonic target counts, and why that is correct behaviour.

    Found when the seeded site changed and an assertion that `flat >= hilly >=
    ridge` failed with 380 / 363 / **366** -- the steepest horizon admitting
    more than the middle one.

    The cause is an interaction between two filters. `moon_separation_deg` is
    the minimum separation *while the moon is up, during the object's
    above-floor window*. Raising the floor trims that window. For an object
    low in the moonlit part of the sky, the trimmed window can fall entirely
    after moonset -- so its moon separation stops being a small number and
    becomes the "moon never up" sentinel, and it passes a filter it previously
    failed.

    That is the right answer, not a bug: if the ridge blocks the object while
    the moon is up, the only time it is observable is after moonset, when the
    moon genuinely is not a problem. The engine is being more careful than the
    intuition was.
    """
    hilly = _by_name(dataclasses.replace(home, horizon=preset("hilly")),
                     kit, catalog)
    ridge = _by_name(dataclasses.replace(home, horizon=preset("ridge")),
                     kit, catalog)

    gained = set(ridge) - set(hilly)
    for name in gained:
        entry = ridge[name]
        # Each one survives precisely because its remaining window dodges the
        # moon, not because the filter got looser.
        assert entry.moon_separation_deg >= entry.required_separation_deg, (
            f"{name} was admitted without clearing the moon-separation rule"
        )


@requires_ephemeris
def test_a_binding_preset_changes_the_list_at_the_default_floor(home, kit,
                                                                catalog):
    """The behaviour the whole preset rework exists to produce.

    Before, every preset topped out at or below 25 deg, so at the default floor
    the horizon control could not change a single target — the feature was
    inert everywhere, and completely inert in the web UI, which pins the floor
    at 25. A preset that describes a real close obstruction has to bite.
    """
    flat_site = dataclasses.replace(home, horizon=FLAT)
    blocked = dataclasses.replace(home, horizon=preset("ridge", facing=180))

    without = assess_targets(night_window(REFERENCE_DATE, flat_site), kit,
                             catalog=catalog,
                             min_altitude_deg=DEFAULT_MIN_ALTITUDE_DEG)
    with_ridge = assess_targets(night_window(REFERENCE_DATE, blocked), kit,
                                catalog=catalog,
                                min_altitude_deg=DEFAULT_MIN_ALTITUDE_DEG)
    assert len(with_ridge) < len(without)


@requires_ephemeris
def test_the_bearing_decides_which_targets_are_lost(home, kit, catalog):
    """A southern obstruction and a northern one must not remove the same
    objects, or the bearing is decorative."""
    south = dataclasses.replace(home, horizon=preset("ridge", facing=180))
    north = dataclasses.replace(home, horizon=preset("ridge", facing=0))

    lost_to_south = {a.obj.name for a in
                     assess_targets(night_window(REFERENCE_DATE, south), kit,
                                    catalog=catalog,
                                    min_altitude_deg=DEFAULT_MIN_ALTITUDE_DEG)}
    lost_to_north = {a.obj.name for a in
                     assess_targets(night_window(REFERENCE_DATE, north), kit,
                                    catalog=catalog,
                                    min_altitude_deg=DEFAULT_MIN_ALTITUDE_DEG)}
    assert lost_to_south != lost_to_north


@requires_ephemeris
def test_surviving_targets_still_clear_the_configured_floor(home, kit, catalog):
    """The horizon raises the floor; it must never lower it."""
    site = dataclasses.replace(home, horizon=preset("ridge"))
    results = assess_targets(night_window(REFERENCE_DATE, site), kit,
                             catalog=catalog, min_altitude_deg=25.0)
    assert all(a.peak_altitude_deg >= 25.0 for a in results)


# ---------------------------------------------------------------------------
# A uniform obstruction angle
#
# The web form offers "how high does the terrain reach?" in five-degree steps
# instead of asking someone to match their site to a description written for
# somebody else's. The risk in that is the label: a ring is an assumption, and
# if it ever round-tripped as `is_generic=False` it would outrank a real
# measurement in every warning the app prints.
# ---------------------------------------------------------------------------

def test_a_uniform_angle_applies_to_every_bearing():
    profile = parse_horizon(20)
    for azimuth in range(0, 360, 30):
        assert profile.min_altitude_at(azimuth) == pytest.approx(20.0)


def test_a_uniform_angle_is_flagged_generic():
    """One angle in every direction is an assumption about a place, not a
    survey of it. Real terrain is not a ring."""
    assert parse_horizon(20).is_generic is True
    assert parse_horizon("35").is_generic is True


def test_a_measured_map_still_outranks_it():
    """The distinction the flag exists to carry."""
    assert parse_horizon({0: 24.0, 90: 0.0}).is_generic is False


def test_zero_degrees_is_flat_not_a_generic_ring():
    """Nothing in the way leaves no assumption to warn about, and returning
    FLAT keeps the round trip exact."""
    profile = parse_horizon(0)
    assert profile.name == "flat"
    assert profile.is_generic is False
    assert profile == parse_horizon(None)


@pytest.mark.parametrize("spec", [5, 20, 45, "10", "37.5"])
def test_a_uniform_angle_survives_the_round_trip(spec):
    """Through the single TEXT column `db/store` keeps locations in.

    Serialising a one-point ring as a JSON map would reload it as a *measured*
    profile -- a fabricated value wearing the honest label, which is the exact
    bug `serialise` was written to fix for measured profiles."""
    profile = parse_horizon(spec)
    restored = parse_horizon(serialise(profile))
    assert restored == profile
    assert restored.is_generic is True


@pytest.mark.parametrize("degrees", [-1, 90, 130])
def test_an_impossible_angle_is_rejected(degrees):
    """Terrain does not reach past the zenith. Better a 422 than a profile
    that silently excludes the entire sky."""
    with pytest.raises(ValueError):
        parse_horizon(degrees)


def test_a_boolean_is_not_an_angle():
    """`bool` is an `int` subclass, so `horizon: true` would otherwise parse
    as a one-degree ring instead of being rejected as the typo it is."""
    with pytest.raises((ValueError, TypeError)):
        parse_horizon(True)
