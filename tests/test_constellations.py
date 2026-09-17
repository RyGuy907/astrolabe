"""Constellation names, centroids, and grouping targets by sky region."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from conftest import REFERENCE_DATE, requires_ephemeris
from engine.catalog.loader import load_catalog
from engine.constellations import (
    CONSTELLATION_NAMES,
    centroid,
    constellation_name,
    marks_toward,
)
from engine.ephem import night_window
from engine.equipment import load_equipment
from engine.targets import assess_targets, group_targets


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


@pytest.fixture(scope="module")
def kit():
    return load_equipment()


# --- names ------------------------------------------------------------------

def test_every_catalog_constellation_has_a_name(catalog):
    """A missing mapping would silently show a bare abbreviation."""
    used = {o.constellation.strip() for o in catalog if o.constellation.strip()}
    missing = used - set(CONSTELLATION_NAMES)
    assert not missing, f"no name for {sorted(missing)}"


def test_serpens_is_split_into_two(catalog):
    """OpenNGC splits Serpens, and it must stay split.

    Serpens is the one constellation in two disjoint pieces; a single centroid
    for it would land in Ophiuchus, between the halves.
    """
    assert constellation_name("Se1") == "Serpens Caput"
    assert constellation_name("Se2") == "Serpens Cauda"

    caput = centroid("Se1", catalog)
    cauda = centroid("Se2", catalog)
    assert caput is not None and cauda is not None
    # The two halves are far apart — that is the whole point.
    assert abs(caput.ra_deg - cauda.ra_deg) > 20.0


def test_unknown_abbreviation_falls_back_to_itself():
    assert constellation_name("Xyz") == "Xyz"


def test_the_iau_set_is_complete():
    """88 constellations, with Serpens appearing twice, is 89 entries."""
    assert len(CONSTELLATION_NAMES) == 89


# --- centroids --------------------------------------------------------------

@pytest.mark.parametrize(
    "abbreviation, ra_range, dec_range",
    [
        # Well-known positions, checked loosely — this is a centroid of catalog
        # members, not a published constellation centre.
        ("Ori", (75, 90), (-10, 10)),      # Orion, on the celestial equator
        ("UMa", (150, 190), (40, 65)),     # Ursa Major, far north
        ("Cyg", (295, 320), (33, 52)),     # Cygnus, in the summer Milky Way
        ("Sgr", (270, 290), (-35, -18)),   # Sagittarius, far south
    ],
)
def test_centroids_land_in_the_right_part_of_the_sky(catalog, abbreviation,
                                                     ra_range, dec_range):
    position = centroid(abbreviation, catalog)
    assert position is not None
    assert ra_range[0] <= position.ra_deg <= ra_range[1]
    assert dec_range[0] <= position.dec_deg <= dec_range[1]


@pytest.mark.parametrize("abbreviation", ["Peg", "Psc", "Cas", "And"])
def test_constellations_straddling_ra_zero_are_not_averaged_naively(catalog,
                                                                    abbreviation):
    """The bug a numeric mean of RA would cause.

    Pegasus runs from about 21h to 0h. Averaging its RA values arithmetically
    lands near 180 degrees — the opposite side of the sky. The vector mean
    puts it back where it belongs, so each of these must sit near 0h, not
    anywhere near 180.
    """
    position = centroid(abbreviation, catalog)
    assert position is not None
    # Within 60 degrees of RA 0h, going either way round.
    distance_from_zero = min(position.ra_deg, 360.0 - position.ra_deg)
    assert distance_from_zero < 60.0, (
        f"{abbreviation} centroid at RA {position.ra_deg:.1f} — an arithmetic "
        "mean of RA would do exactly this"
    )


def test_wide_constellations_are_flagged(catalog):
    """Hydra is the longest constellation; one point is a poor summary."""
    hydra = centroid("Hya", catalog)
    assert hydra is not None
    assert hydra.spread_deg > 40.0
    assert hydra.is_wide is True


def test_compact_constellations_are_not_flagged(catalog):
    for abbreviation in ("Sge", "Equ", "CrB"):
        position = centroid(abbreviation, catalog)
        if position is not None:
            assert position.is_wide is False, f"{abbreviation} flagged wide"


def test_centroid_of_an_unknown_constellation_is_none(catalog):
    assert centroid("Xyz", catalog) is None


def test_centroid_reports_its_member_count(catalog):
    position = centroid("UMa", catalog)
    assert position is not None
    actual = sum(1 for o in catalog if o.constellation.strip() == "UMa")
    assert position.member_count == actual


# --- grouping ---------------------------------------------------------------

@requires_ephemeris
def test_group_by_constellation_buckets_by_sky_region(home, kit, catalog):
    window = night_window(REFERENCE_DATE, home)
    results = assess_targets(window, kit, catalog=catalog)
    grouped = group_targets(results, limit_per_group=5, by="constellation")

    assert grouped
    for key, rows in grouped.items():
        assert all(row.obj.constellation.strip() == key for row in rows)
        assert len(rows) <= 5


@requires_ephemeris
def test_constellation_groups_are_ordered_by_their_best_target(home, kit, catalog):
    """Alphabetical would bury tonight's best region under Andromeda."""
    window = night_window(REFERENCE_DATE, home)
    grouped = group_targets(assess_targets(window, kit, catalog=catalog),
                            by="constellation")

    bests = [max(a.score for a in rows) for rows in grouped.values()]
    assert bests == sorted(bests, reverse=True)


@requires_ephemeris
def test_type_grouping_still_works(home, kit, catalog):
    window = night_window(REFERENCE_DATE, home)
    grouped = group_targets(assess_targets(window, kit, catalog=catalog),
                            by="type")
    assert "Galaxies" in grouped


def test_an_unknown_grouping_raises():
    with pytest.raises(ValueError):
        group_targets([], by="colour")


@requires_ephemeris
def test_september_favours_the_autumn_constellations(home, kit, catalog):
    """Sanity: mid-September evenings belong to Perseus, Cygnus, Cassiopeia."""
    window = night_window(REFERENCE_DATE, home)
    grouped = group_targets(assess_targets(window, kit, catalog=catalog),
                            by="constellation")

    top = list(grouped)[:8]
    autumn = {"Per", "Cyg", "Cas", "And", "Cep", "Lac", "Peg", "Aur"}
    assert autumn & set(top), f"expected an autumn constellation in {top}"
    # Spring constellations should not be leading in September.
    assert "Vir" not in top[:3]


# --- when a constellation is actually up ------------------------------------

@requires_ephemeris
def test_classify_visibility_matches_the_season(home, catalog):
    """Late August at 34 N: autumn is up, winter comes after midnight, the
    far south never rises at all."""
    from datetime import timedelta

    from engine.constellations import classify_visibility
    from engine.targets import _observing_window
    from engine.timeutil import local_midnight_utc

    night = night_window(date(2026, 8, 26), home)
    start, end = _observing_window(night)
    midnight = local_midnight_utc(night.date + timedelta(days=1), home.tz)

    positions = [p for p in (centroid(a, catalog) for a in CONSTELLATION_NAMES) if p]
    verdict = classify_visibility(positions, home, start, end, midnight)

    # Autumn constellations are up in the evening.
    for abbreviation in ("Cyg", "Peg", "And", "Lyr"):
        assert verdict[abbreviation] == "tonight", abbreviation

    # Winter constellations only clear the floor after midnight.
    for abbreviation in ("Ori", "Tau", "Gem", "Aur"):
        assert verdict[abbreviation] == "late", abbreviation

    # Far-southern constellations never rise from 34 N.
    for abbreviation in ("Cru", "Oct", "Aps"):
        assert verdict[abbreviation] == "none", abbreviation


@requires_ephemeris
def test_every_constellation_is_classified(home, catalog):
    from datetime import timedelta

    from engine.constellations import classify_visibility
    from engine.targets import _observing_window
    from engine.timeutil import local_midnight_utc

    night = night_window(REFERENCE_DATE, home)
    start, end = _observing_window(night)
    midnight = local_midnight_utc(night.date + timedelta(days=1), home.tz)

    positions = [p for p in (centroid(a, catalog) for a in CONSTELLATION_NAMES) if p]
    verdict = classify_visibility(positions, home, start, end, midnight)

    assert len(verdict) == len(positions)
    assert set(verdict.values()) <= {"tonight", "late", "none"}


@requires_ephemeris
def test_circumpolar_constellations_are_up_all_night(home, catalog):
    """Ursa Minor never sets from 34 N, so it can never be 'late' or 'none'."""
    from datetime import timedelta

    from engine.constellations import classify_visibility
    from engine.targets import _observing_window
    from engine.timeutil import local_midnight_utc

    night = night_window(REFERENCE_DATE, home)
    start, end = _observing_window(night)
    midnight = local_midnight_utc(night.date + timedelta(days=1), home.tz)

    positions = [p for p in (centroid(a, catalog) for a in ("UMi", "Cas", "Cep")) if p]
    verdict = classify_visibility(positions, home, start, end, midnight)
    assert verdict["UMi"] == "tonight"


# --- viewing windows --------------------------------------------------------

def _assess(home, catalog, when, abbreviations=None):
    from datetime import timedelta

    from engine.constellations import assess_constellations
    from engine.targets import _observing_window
    from engine.timeutil import local_midnight_utc

    night = night_window(when, home)
    start, end = _observing_window(night)
    midnight = local_midnight_utc(night.date + timedelta(days=1), home.tz)
    names = abbreviations or CONSTELLATION_NAMES
    positions = [p for p in (centroid(a, catalog) for a in names) if p]
    return assess_constellations(positions, home, start, end, midnight), start, end


@requires_ephemeris
def test_windows_sit_inside_the_observing_window(home, catalog):
    verdict, start, end = _assess(home, catalog, REFERENCE_DATE)
    for entry in verdict.values():
        if entry.start_utc is None:
            continue
        assert entry.start_utc >= start
        assert entry.end_utc <= end
        assert entry.start_utc < entry.end_utc


@requires_ephemeris
def test_not_visible_constellations_have_no_window(home, catalog):
    verdict, _, _ = _assess(home, catalog, REFERENCE_DATE)
    for entry in verdict.values():
        if entry.status == "none":
            assert entry.start_utc is None and entry.end_utc is None
            assert entry.hours_up == 0.0


@requires_ephemeris
def test_hours_up_never_exceeds_the_window(home, catalog):
    verdict, start, end = _assess(home, catalog, REFERENCE_DATE)
    span_hours = (end - start).total_seconds() / 3600.0
    for entry in verdict.values():
        assert 0.0 <= entry.hours_up <= span_hours + 0.2


@requires_ephemeris
def test_a_late_constellation_starts_after_midnight(home, catalog):
    """Orion in late August: up only in the last hour before dawn."""
    from datetime import timedelta

    from engine.timeutil import local_midnight_utc

    verdict, _, _ = _assess(home, catalog, date(2026, 8, 26), ["Ori", "Cyg"])
    midnight = local_midnight_utc(date(2026, 8, 27), home.tz)

    orion = verdict["Ori"]
    assert orion.status == "late"
    assert orion.start_utc >= midnight
    assert orion.hours_up < 3.0

    cygnus = verdict["Cyg"]
    assert cygnus.status == "tonight"
    assert cygnus.start_utc < midnight
    assert cygnus.hours_up > orion.hours_up


@requires_ephemeris
def test_peak_altitude_is_reported_even_when_never_up(home, catalog):
    """Crux never clears the floor from 34 N, and saying how far below it
    gets is more useful than a bare "no"."""
    verdict, _, _ = _assess(home, catalog, REFERENCE_DATE, ["Cru"])
    crux = verdict["Cru"]
    assert crux.status == "none"
    assert crux.peak_altitude_deg < 25.0


@requires_ephemeris
def test_a_high_declination_constellation_can_dip_below_the_floor(home, catalog):
    """Ursa Minor sits at dec +77, so its lower culmination is about 21 deg
    from 34 N — under a 25 deg floor. Its window must not run all night."""
    verdict, start, end = _assess(home, catalog, date(2026, 8, 26), ["UMi"])
    umi = verdict["UMi"]
    assert umi.status == "tonight"
    span_hours = (end - start).total_seconds() / 3600.0
    assert umi.hours_up < span_hours - 1.0


@requires_ephemeris
def test_has_gap_detects_a_discontinuous_window(home, catalog):
    """The flag exists because a single start-to-end span would overstate
    the time available for a constellation that dips and returns."""
    verdict, _, _ = _assess(home, catalog, REFERENCE_DATE)
    for entry in verdict.values():
        if entry.start_utc is None:
            continue
        envelope = (entry.end_utc - entry.start_utc).total_seconds() / 3600.0
        assert entry.has_gap == (envelope - entry.hours_up > 0.5)


# ---------------------------------------------------------------------------
# marks_toward: describing a horizon by naming what you can see
#
# The point of these is that the observer's answer ("the lowest thing I can
# make out due north is Cassiopeia") has to become a trustworthy altitude. If
# the list offered is in the wrong part of the sky, or not sorted lowest
# first, the number recorded is wrong and the horizon it builds is worse than
# the preset it replaced.
# ---------------------------------------------------------------------------

#: Mid-evening on the reference night, in UTC. Late enough that the sky has
#: something in it, which is when anyone would actually be doing this.
MARKS_WHEN = datetime(2026, 9, 16, 4, 0, tzinfo=timezone.utc)


@requires_ephemeris
def test_marks_are_sorted_lowest_first(home, catalog):
    """The UI asks for the *lowest* one visible, so the first option offered
    must be the lowest. Anything else invites picking the wrong row."""
    marks = marks_toward(0.0, home, MARKS_WHEN, catalog)
    assert marks, "nothing toward north to choose from"
    altitudes = [m.altitude_deg for m in marks]
    assert altitudes == sorted(altitudes)


@requires_ephemeris
@pytest.mark.parametrize("bearing", [0.0, 90.0, 180.0, 270.0])
def test_marks_lie_within_the_spread_of_the_bearing(bearing, home, catalog):
    """A constellation in the south tells you nothing about the northern
    treeline. Offering one would silently record a bogus obstruction."""
    marks = marks_toward(bearing, home, MARKS_WHEN, catalog, spread_deg=35.0)
    for mark in marks:
        offset = abs((mark.azimuth_deg - bearing + 180.0) % 360.0 - 180.0)
        assert offset <= 35.0, f"{mark.name} is {offset:.0f} deg off {bearing}"


@requires_ephemeris
def test_north_does_not_split_at_the_compass_seam(home, catalog):
    """Azimuth wraps at 360, and a naive difference would drop everything
    just west of north. Both sides of the seam must be offered."""
    marks = marks_toward(0.0, home, MARKS_WHEN, catalog)
    assert any(m.azimuth_deg < 35.0 for m in marks)
    assert any(m.azimuth_deg > 325.0 for m in marks)


@requires_ephemeris
def test_marks_exclude_the_overhead_sky(home, catalog):
    """Something near the zenith cannot be cut off by terrain, so it is not a
    usable marker -- and offering it would let someone record a 70 deg
    horizon by mistake."""
    marks = marks_toward(180.0, home, MARKS_WHEN, catalog, max_altitude_deg=60.0)
    assert all(0.0 <= m.altitude_deg <= 60.0 for m in marks)


@requires_ephemeris
def test_marks_move_with_the_observer(catalog, home):
    """The same bearing at the same instant shows a different sky from a
    different latitude. If it did not, the result would be a lookup table
    rather than a measurement."""
    from engine.horizon import preset
    from engine.locations import Location

    far_south = Location(
        key="far_south", name="Far South", lat=-34.0, lon=home.lon,
        elevation_m=0.0, bortle=5, tz="UTC", horizon=preset("flat"),
    )
    north = {m.abbreviation for m in marks_toward(0.0, home, MARKS_WHEN, catalog)}
    south = {m.abbreviation
             for m in marks_toward(0.0, far_south, MARKS_WHEN, catalog)}
    assert north != south


@requires_ephemeris
def test_marks_require_an_aware_time(home, catalog):
    """PLAN.md's tz-aware-UTC invariant, enforced at the boundary rather than
    left to produce an altitude that is silently hours out."""
    naive = MARKS_WHEN.replace(tzinfo=None)
    with pytest.raises(ValueError):
        marks_toward(0.0, home, naive, catalog)
