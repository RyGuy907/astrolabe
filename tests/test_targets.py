"""Target filtering, ranking and grouping — including the Phase 1 acceptance check.

PLAN.md §6 Phase 1: "M42 should not appear as a good September evening target;
M31 should rank highly."
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from conftest import REFERENCE_DATE, requires_ephemeris
from engine.catalog.loader import find_object, load_catalog
from engine.ephem import night_window
from engine.equipment import load_equipment
from engine.targets import (
    DEFAULT_GROUP_LIMIT,
    GROUP_ORDER,
    _contiguous_spans,
    _max_possible_altitude,
    _observing_window,
    _required_separation,
    _sky_surface_brightness,
    assess_targets,
    group_targets,
    uses_true_dark,
)
from engine.timeutil import is_aware_utc, to_local


@pytest.fixture(scope="module")
def kit():
    return load_equipment()


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


@pytest.fixture(scope="module")
def assessments(reference_night, kit, catalog):
    return assess_targets(reference_night, kit, catalog=catalog)


@pytest.fixture(scope="module")
def grouped(assessments):
    return group_targets(assessments)


def _rank(assessments, messier: int) -> int | None:
    for index, assessment in enumerate(assessments):
        if assessment.obj.messier == messier:
            return index + 1
    return None


# --- PLAN.md Phase 1 acceptance check ---------------------------------------

@requires_ephemeris
def test_m31_ranks_highly(assessments, grouped):
    """M31 is the marquee September galaxy and must lead its group."""
    rank = _rank(assessments, 31)
    assert rank is not None, "M31 was filtered out entirely"
    assert rank <= 20, f"M31 ranked {rank} overall, expected top 20"
    assert grouped["Galaxies"][0].obj.messier == 31


@requires_ephemeris
def test_m42_is_not_a_good_september_target(assessments, grouped):
    """Orion is a winter object; in mid-September it barely clears dawn.

    The check is that M42 is not *recommended*, not that it is absent — it
    does scrape above the floor before astronomical dawn, and saying so is
    honest. What would be wrong is presenting it as a good target.
    """
    displayed = [a.obj.messier for a in grouped.get("Nebulae", [])]
    assert 42 not in displayed, "M42 appeared in the displayed Nebulae group"

    rank = _rank(assessments, 42)
    if rank is not None:
        assert rank > 100, f"M42 ranked {rank}, too high for mid-September"


@requires_ephemeris
def test_september_evening_favourites_are_present(assessments):
    """Sanity: the autumn showpieces should all survive the filters."""
    for messier in (31, 33, 15, 2, 27, 57, 39):
        assert _rank(assessments, messier) is not None, f"M{messier} was filtered out"


@requires_ephemeris
def test_spring_galaxies_are_absent_in_september(assessments):
    """M51 and M101 are spring objects — too low after moonset in September."""
    for messier in (51, 101):
        assert _rank(assessments, messier) is None, (
            f"M{messier} is a spring galaxy and should not clear the floor"
        )


# --- output shape -----------------------------------------------------------

@requires_ephemeris
def test_every_group_is_capped(assessments):
    grouped = group_targets(assessments, limit_per_group=5)
    assert all(len(items) <= 5 for items in grouped.values())


@requires_ephemeris
def test_groups_come_back_in_display_order(grouped):
    seen = [g for g in grouped if g in GROUP_ORDER]
    assert seen == [g for g in GROUP_ORDER if g in grouped]


@requires_ephemeris
def test_results_are_sorted_by_score(assessments):
    scores = [a.score for a in assessments]
    assert scores == sorted(scores, reverse=True)


@requires_ephemeris
def test_scores_are_in_range(assessments):
    assert all(0.0 <= a.score <= 100.0 for a in assessments)


# --- hard filters -----------------------------------------------------------

@requires_ephemeris
def test_everything_clears_the_altitude_floor(assessments):
    assert all(a.peak_altitude_deg >= 25.0 for a in assessments)


@requires_ephemeris
def test_raising_the_floor_shrinks_the_list(reference_night, kit, catalog):
    low = assess_targets(reference_night, kit, catalog=catalog, min_altitude_deg=20)
    high = assess_targets(reference_night, kit, catalog=catalog, min_altitude_deg=60)
    assert len(high) < len(low)
    assert all(a.peak_altitude_deg >= 60.0 for a in high)


@requires_ephemeris
def test_everything_is_up_long_enough(assessments):
    assert all(a.hours_above_floor >= 0.5 for a in assessments)
    for assessment in assessments:
        start, end = assessment.best_window
        assert (end - start) >= timedelta(minutes=30)


@requires_ephemeris
def test_windows_stay_inside_the_observing_window(reference_night, assessments):
    """A target must never be reported as observable past dawn."""
    span_start, span_end = _observing_window(reference_night)
    for assessment in assessments:
        for start, end in assessment.above_floor:
            assert start >= span_start
            assert end <= span_end, (
                f"{assessment.obj.name} reported up until {end}, past {span_end}"
            )


@requires_ephemeris
def test_moon_separation_is_respected(assessments):
    assert all(a.moon_separation_deg >= a.required_separation_deg
               for a in assessments)


# --- timezone boundary ------------------------------------------------------

@requires_ephemeris
def test_all_target_datetimes_are_aware_utc(assessments):
    """Same invariant as every other engine boundary."""
    for assessment in assessments[:200]:
        assert is_aware_utc(assessment.peak_time_utc)
        for start, end in assessment.above_floor:
            assert is_aware_utc(start) and is_aware_utc(end)


@requires_ephemeris
def test_peak_time_falls_inside_the_window(reference_night, assessments):
    span_start, span_end = _observing_window(reference_night)
    assert all(span_start <= a.peak_time_utc <= span_end for a in assessments)


# --- unit-level pieces ------------------------------------------------------

def test_max_possible_altitude():
    """An object at the observer's own declination passes through the zenith."""
    assert _max_possible_altitude(34.14, 34.14) == pytest.approx(90.0)
    assert _max_possible_altitude(0.0, 34.14) == pytest.approx(55.86)
    # Far-southern objects can never rise at all from a northern site.
    assert _max_possible_altitude(-60.0, 34.14) < 0


def test_required_separation_scales_with_moon_brightness(catalog):
    galaxy = find_object("M31", catalog)
    dark = _required_separation(galaxy, 0.0)
    bright = _required_separation(galaxy, 1.0)
    assert dark == pytest.approx(20.0)
    assert bright == pytest.approx(80.0)
    assert bright > dark


def test_clusters_tolerate_moonlight_better_than_galaxies(catalog):
    """PLAN.md 3.3.1 relaxes the threshold for clusters and doubles."""
    galaxy = find_object("M31", catalog)
    cluster = find_object("M13", catalog)
    assert _required_separation(cluster, 0.8) < _required_separation(galaxy, 0.8)


def test_sky_brightness_degrades_when_the_moon_is_up(home):
    dark = _sky_surface_brightness(home, 0.9, moon_is_up=False)
    moonlit = _sky_surface_brightness(home, 0.9, moon_is_up=True)
    assert dark == home.sqm
    assert moonlit < dark          # smaller number = brighter sky


def test_contiguous_spans_finds_runs_and_clamps_the_tail():
    from datetime import datetime, timezone

    step = timedelta(minutes=15)
    times = [datetime(2026, 9, 16, 4, 0, tzinfo=timezone.utc) + step * i
             for i in range(6)]
    window_end = times[-1] + timedelta(minutes=5)   # earlier than a full step

    mask = np.array([True, True, False, False, True, True])
    spans = _contiguous_spans(times, mask, step, window_end)

    assert len(spans) == 2
    assert spans[0] == (times[0], times[2])
    # The open run is extended, but not past the window end.
    assert spans[1] == (times[4], window_end)


def test_contiguous_spans_handles_all_false():
    from datetime import datetime, timezone

    times = [datetime(2026, 9, 16, 4, 0, tzinfo=timezone.utc)]
    assert _contiguous_spans(times, np.array([False]), timedelta(minutes=15),
                             times[0]) == []


# --- behaviour across different nights --------------------------------------

@requires_ephemeris
def test_bright_moon_night_yields_fewer_targets(home, kit, catalog):
    """A near-full moon should knock out faint extended objects."""
    dark_night = night_window(REFERENCE_DATE, home)          # 27% moon
    bright_night = night_window(date(2026, 10, 26), home)    # near full

    dark_count = len(assess_targets(dark_night, kit, catalog=catalog))
    bright_count = len(assess_targets(bright_night, kit, catalog=catalog))
    assert bright_count < dark_count


@requires_ephemeris
def test_orion_returns_in_december(home, kit, catalog):
    """The seasonal counterpart to the M42 check: it should be good in winter."""
    winter = night_window(date(2026, 12, 15), home)
    results = assess_targets(winter, kit, catalog=catalog)
    grouped = group_targets(results)

    rank = _rank(results, 42)
    assert rank is not None, "M42 should be observable in December"
    assert 42 in [a.obj.messier for a in grouped["Nebulae"]], (
        "M42 should be a recommended December nebula"
    )


@requires_ephemeris
def test_darker_site_admits_more_targets(kit, catalog):
    """Bortle 4 should let through at least as much as Bortle 5."""
    from engine.locations import get_location

    home = get_location("home")                       # Bortle 5
    mountains = get_location("santa_monica_mtns")     # Bortle 4

    at_home = len(assess_targets(night_window(REFERENCE_DATE, home), kit,
                                 catalog=catalog))
    at_altitude = len(assess_targets(night_window(REFERENCE_DATE, mountains), kit,
                                     catalog=catalog))
    assert at_altitude >= at_home




# --- moonlight must degrade the point-source limit, not just contrast -------

@requires_ephemeris
def test_moonlight_degrades_the_limiting_magnitude(home, kit, catalog):
    """A 97% moon must knock out faint objects, not only extended ones.

    The bug this guards: sky brightness was degraded for moonlight in the
    surface-brightness contrast test but *not* in the point-source limiting
    magnitude, so compact mag-12 galaxies passed under a near-full moon while
    diffuse ones were correctly rejected.
    """
    bright_moon = night_window(date(2026, 8, 25), home)      # 97% illuminated
    assert bright_moon.moon_illumination > 0.9

    results = assess_targets(bright_moon, kit, catalog=catalog)
    faint = [a for a in results if a.obj.magnitude and a.obj.magnitude > 11.0]

    assert len(faint) < 10, (
        f"{len(faint)} objects fainter than mag 11 passed under a 97% moon"
    )
    # What survives should be genuinely bright.
    assert results[0].obj.magnitude < 7.0


@requires_ephemeris
def test_a_bright_moon_night_still_returns_a_usable_list(home, kit, catalog):
    """It must not return nothing, either.

    Under a near-full moon the true dark window can be minutes long. Preferring
    it whenever non-empty made the target list empty on exactly the nights an
    observer most needs to know what is still worth looking at.
    """
    bright_moon = night_window(date(2026, 8, 25), home)
    assert bright_moon.dark_hours < 0.5           # a few minutes of true dark

    results = assess_targets(bright_moon, kit, catalog=catalog)
    assert len(results) > 20, "a bright-moon night should still offer targets"
    assert uses_true_dark(bright_moon) is False


@requires_ephemeris
def test_observing_window_falls_back_when_dark_is_too_short(home):
    """The window used must be astronomical night, not the 5-minute sliver."""
    window = night_window(date(2026, 8, 25), home)
    span = _observing_window(window)
    assert span is not None

    start, end = span
    assert (end - start) > timedelta(hours=4)
    assert (start, end) == (window.astronomical_night[0][0],
                            window.astronomical_night[-1][1])


# --- detectability by object class ------------------------------------------

@requires_ephemeris
def test_globular_clusters_survive_a_bright_moon(home, kit, catalog):
    """M13 must not be rejected on mean surface brightness.

    The bug: the contrast test was applied to anything extended, including
    clusters. A globular is a swarm of stars that resolves at magnification,
    so integrated magnitude decides whether you see it — not the average
    brightness smeared over its area. Under a 99% moon M13 scored a contrast
    of -3.1 against a -2.5 floor and was thrown out, while compact planetary
    nebulae stayed. That is backwards: a globular is one of the few things
    genuinely worth pointing at on a bright night.
    """
    bright_moon = night_window(date(2026, 8, 26), home)
    assert bright_moon.moon_illumination > 0.95

    results = assess_targets(bright_moon, kit, catalog=catalog)
    names = {a.obj.name for a in results}

    m13 = find_object("M13", catalog)
    assert m13.name in names, "M13 should be observable under a bright moon"


@requires_ephemeris
def test_diffuse_objects_are_still_rejected_under_a_bright_moon(home, kit, catalog):
    """The contrast test must keep working where it belongs.

    M31 has a mean surface brightness of 22.3; under a 99% moon the sky is
    brighter than the galaxy. Rejecting it is correct.
    """
    bright_moon = night_window(date(2026, 8, 26), home)
    results = assess_targets(bright_moon, kit, catalog=catalog)
    names = {a.obj.name for a in results}

    assert find_object("M31", catalog).name not in names


@requires_ephemeris
def test_diffuse_objects_return_on_a_dark_night(home, kit, catalog):
    dark = night_window(REFERENCE_DATE, home)          # 27% moon
    names = {a.obj.name for a in assess_targets(dark, kit, catalog=catalog)}

    for designation in ("M31", "M57", "M27", "M13"):
        assert find_object(designation, catalog).name in names, designation


# --- sorting ----------------------------------------------------------------

@requires_ephemeris
def test_brightness_sort_orders_within_a_group(home, kit, catalog):
    results = assess_targets(night_window(REFERENCE_DATE, home), kit,
                             catalog=catalog)
    grouped = group_targets(results, limit_per_group=8, by="constellation",
                            sort="brightness")

    for rows in grouped.values():
        magnitudes = [r.obj.magnitude if r.obj.magnitude is not None else 99.0
                      for r in rows]
        assert magnitudes == sorted(magnitudes)


@requires_ephemeris
def test_hercules_leads_with_m13_when_sorted_by_brightness(home, kit, catalog):
    """The case that prompted the fix."""
    results = assess_targets(night_window(REFERENCE_DATE, home), kit,
                             catalog=catalog)
    grouped = group_targets(results, limit_per_group=8, by="constellation",
                            sort="brightness")

    assert "Her" in grouped, "Hercules should have targets in September"
    assert grouped["Her"][0].obj.messier == 13


@requires_ephemeris
def test_buckets_are_capped_on_score_not_brightness(home, kit, catalog):
    """A bright object scraping the horizon must not displace a good one.

    Capping on brightness would keep whatever is brightest regardless of
    whether it is observable, which is the opposite of useful.
    """
    results = assess_targets(night_window(REFERENCE_DATE, home), kit,
                             catalog=catalog)
    by_score = group_targets(results, limit_per_group=5, by="constellation",
                             sort="score")
    by_brightness = group_targets(results, limit_per_group=5, by="constellation",
                                  sort="brightness")

    for key in by_score:
        assert {a.obj.name for a in by_score[key]} == \
               {a.obj.name for a in by_brightness[key]}, (
            f"{key}: the same objects should be kept, only reordered"
        )


def test_an_unknown_sort_raises():
    with pytest.raises(ValueError):
        group_targets([], sort="colour")


# --- visible late -----------------------------------------------------------

@requires_ephemeris
def test_visible_late_flags_objects_that_rise_after_midnight(home, kit, catalog):
    """"Observable tonight" and "observable at 3 a.m." are different offers."""
    from engine.timeutil import local_midnight_utc

    window = night_window(REFERENCE_DATE, home)
    results = assess_targets(window, kit, catalog=catalog)
    midnight = local_midnight_utc(REFERENCE_DATE + timedelta(days=1), home.tz)

    late = [a for a in results if a.visible_late]
    early = [a for a in results if not a.visible_late]
    assert late and early, "expected a mix of early and late targets"

    for assessment in late:
        assert assessment.above_floor[0][0] >= midnight
    for assessment in early:
        assert assessment.above_floor[0][0] < midnight


@requires_ephemeris
def test_late_objects_are_a_minority_on_a_normal_night(home, kit, catalog):
    """Sanity: most of what is up tonight is up before midnight."""
    results = assess_targets(night_window(REFERENCE_DATE, home), kit,
                             catalog=catalog)
    late = sum(1 for a in results if a.visible_late)
    assert 0 < late < len(results) / 2


# --- the unfiltered catalog view --------------------------------------------

@requires_ephemeris
def test_group_catalog_returns_every_object(home, kit, catalog):
    """"All targets" must mean all of them.

    A default cap of 1000 silently truncated Virgo, which has 1051 catalogued
    objects in the Virgo Cluster — so the caller asked for everything and
    quietly got 51 fewer.
    """
    from engine.targets import group_catalog

    results = assess_targets(night_window(REFERENCE_DATE, home), kit,
                             catalog=catalog)
    grouped = group_catalog(catalog, results, by="constellation")

    assert sum(len(v) for v in grouped.values()) == len(catalog)
    assert len(grouped["Vir"]) == sum(1 for o in catalog
                                      if o.constellation.strip() == "Vir")


@requires_ephemeris
def test_group_catalog_annotates_visibility(home, kit, catalog):
    from engine.targets import group_catalog

    results = assess_targets(night_window(REFERENCE_DATE, home), kit,
                             catalog=catalog)
    observable = {a.obj.name for a in results}
    grouped = group_catalog(catalog, results, by="constellation")

    for entries in grouped.values():
        for entry in entries:
            assert entry.visible_tonight == (entry.obj.name in observable)
            if not entry.visible_tonight:
                assert entry.assessment is None
                assert entry.visible_late is False


@requires_ephemeris
def test_group_catalog_orders_brightest_first(home, kit, catalog):
    from engine.targets import group_catalog

    results = assess_targets(night_window(REFERENCE_DATE, home), kit,
                             catalog=catalog)
    grouped = group_catalog(catalog, results, by="constellation")

    for entries in grouped.values():
        magnitudes = [e.sort_magnitude for e in entries]
        assert magnitudes == sorted(magnitudes)


@requires_ephemeris
def test_group_catalog_leads_with_tonights_best_region(home, kit, catalog):
    """Regions with nothing observable sort last rather than vanishing."""
    from engine.targets import group_catalog

    results = assess_targets(night_window(REFERENCE_DATE, home), kit,
                             catalog=catalog)
    grouped = group_catalog(catalog, results, by="constellation")

    ranks = [
        max((e.assessment.score for e in entries if e.assessment), default=-1.0)
        for entries in grouped.values()
    ]
    assert ranks == sorted(ranks, reverse=True)
    # Every constellation is present, including the ones out of season.
    assert len(grouped) >= 80


# --- too faint: listed, flagged, sorted last --------------------------------

@requires_ephemeris
def test_too_faint_objects_are_excluded_by_default(home, kit, catalog):
    """The default stays a recommendation list, not a catalogue dump."""
    window = night_window(date(2026, 8, 26), home)
    results = assess_targets(window, kit, catalog=catalog)
    assert not any(a.too_faint for a in results)


@requires_ephemeris
def test_include_too_faint_keeps_them_flagged(home, kit, catalog):
    """Up and pointable but beaten by the sky is a different answer from
    "not up", and worth saying out loud."""
    window = night_window(date(2026, 8, 26), home)
    solid = assess_targets(window, kit, catalog=catalog)
    both = assess_targets(window, kit, catalog=catalog, include_too_faint=True)

    assert len(both) > len(solid)
    faint = [a for a in both if a.too_faint]
    assert faint
    assert len(both) - len(faint) == len(solid)


@requires_ephemeris
def test_moon_glare_counts_as_too_faint_not_as_absent(home, kit, catalog):
    """M31 under a 99% moon is up and pointable — it is just drowned.

    Moon separation is a sky condition, not a positional one, so it belongs
    with the other faintness reasons rather than silently removing the object.
    """
    window = night_window(date(2026, 8, 26), home)
    both = assess_targets(window, kit, catalog=catalog, include_too_faint=True)
    by_name = {a.obj.name: a for a in both}

    m31 = by_name.get(find_object("M31", catalog).name)
    assert m31 is not None, "M31 is above the horizon and should be listed"
    assert m31.too_faint is True
    assert any("moon" in note for note in m31.notes)


@requires_ephemeris
def test_faint_objects_sort_after_solid_ones(home, kit, catalog):
    """Brightness ordering must not float a faint object above a real target."""
    window = night_window(date(2026, 8, 26), home)
    both = assess_targets(window, kit, catalog=catalog, include_too_faint=True)
    grouped = group_targets(both, limit_per_group=1000, by="constellation",
                            sort="brightness")

    for key, rows in grouped.items():
        flags = [r.too_faint for r in rows]
        assert flags == sorted(flags), f"{key}: faint objects are interleaved"

        # ...and brightness still orders within each half.
        for half in (False, True):
            magnitudes = [r.obj.magnitude if r.obj.magnitude is not None else 99.0
                          for r in rows if r.too_faint is half]
            assert magnitudes == sorted(magnitudes)


@requires_ephemeris
def test_a_bright_galaxy_sorts_below_a_dimmer_visible_cluster(home, kit, catalog):
    """The concrete case: M31 at mag 3.4 sits under NGC 7686 at mag 5.6,
    because the moon has washed the galaxy out and not the cluster."""
    window = night_window(date(2026, 8, 26), home)
    both = assess_targets(window, kit, catalog=catalog, include_too_faint=True)
    grouped = group_targets(both, limit_per_group=1000, by="constellation",
                            sort="brightness")

    andromeda = grouped.get("And")
    assert andromeda
    names = [a.obj.name for a in andromeda]
    m31 = find_object("M31", catalog).name
    cluster = find_object("NGC 7686", catalog).name
    assert names.index(cluster) < names.index(m31)
