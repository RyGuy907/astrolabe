"""Target filtering, detectability, ranking and grouping (PLAN.md 3.3).

The pipeline, in order:

1. **Cheap prefilter** — declination that can never clear the altitude floor
   from this latitude, and objects with no magnitude at all. Costs no
   ephemeris work and removes most of the catalog.
2. **Alt/az sampling** — vectorized over the survivors across the observing
   window, one Skyfield call per time step.
3. **Hard filters** — peak altitude, continuous time above the floor, moon
   separation, detectability.
4. **Ranking and grouping** — weighted score, capped per group.

Detectability caveat worth stating plainly: the contrast model below is a
heuristic. Limiting magnitude, extinction and surface brightness are each
standard formulae, but the threshold for "an extended object this far below
sky brightness is still visible" is a tuned guess, not something calibrated
against observing reports. It is good enough to rank targets against each
other and should not be read as a detection prediction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
from skyfield.api import Star

from .catalog.loader import DeepSkyObject, load_catalog
from .ephem import Interval, NightWindow, _observer, load_ephemeris
from .equipment import (
    DEFAULT_EXTINCTION_K,
    Equipment,
    Scope,
    airmass,
    extinction_mag,
    telescopic_limiting_mag,
)
from .locations import BORTLE_SQM, Location
from .timeutil import ensure_utc, local_midnight_utc

DEFAULT_MIN_ALTITUDE_DEG = 25.0
DEFAULT_MIN_MINUTES_ABOVE_FLOOR = 30
DEFAULT_STEP = timedelta(minutes=15)
DEFAULT_GROUP_LIMIT = 8

# How far below sky brightness an extended object can sit and still be
# considered detectable in a 200 mm class scope, in mag/arcsec^2. Heuristic.
DEFAULT_CONTRAST_FLOOR = -2.5

# Concentrated-core correction for large, bright extended objects.
#
# **The problem.** The contrast test compares the sky to an object's *mean*
# surface brightness over its full catalogued ellipse. For a large object with
# a concentrated core that statistic describes the faint outer isophote, not
# what anyone sees. From a Bortle 6 sky this engine declared the Orion Nebula,
# the Andromeda Galaxy, the Lagoon, the Eagle and both Magellanic Clouds below
# the detection threshold -- while passing M32, a compact companion nearly
# five magnitudes fainter that happens to be small.
#
# **Which objects.** The catalogue carries no light-profile data, so the core
# brightness cannot be computed. Integrated magnitude is the one independent
# discriminator available, and it is a sharp one: of the 1,883 extended
# objects that fail the contrast test at Bortle 6, exactly twelve are brighter
# than magnitude 8 -- M42, M31, M33, M8, M16, M101, Centaurus A, the Helix,
# both Magellanic Clouds, IC 1805 and NGC 7380. That is not a slice of the
# faint tail; it is a list of the objects the statistic is wrong about. 8.0 is
# the binocular threshold.
#
# **How much.** Half an exponential disc's light falls inside its half-light
# radius, at 1.68 scale lengths, while the catalogued D25 diameter runs to
# roughly 3.2-4 scale lengths. Half the light in 1/3.6 to 1/5.7 of the area is
# 0.65 to 1.13 magnitudes per square arcsecond brighter than the full-extent
# mean; 1.0 is the top of that range and the value used here.
#
# **Why a shift and not an exemption.** The first version of this simply
# stopped calling bright objects too faint, which made M31 immune to light
# pollution: it scored 77 from an inner-city sky, where it is a poor view at
# best. Shifting the surface brightness and re-running the same test keeps the
# sky in the argument. M31 now passes to Bortle 6 and fails from 7 up, which
# is about right.
#
# **What this still gets wrong.** M42 is rejected from Bortle 8, where it is
# plainly visible -- the Trapezium region is orders of magnitude brighter than
# the 90-arcminute mean, and no single constant can express that. Separating
# M42 from M31 needs a concentration index the catalogue does not carry.
BRIGHT_CORE_MAG = 8.0
BRIGHT_CORE_SB_BONUS = 1.0

# Moon separation threshold, PLAN.md 3.3.1: 20 deg + 60 deg * illumination for
# faint extended objects, relaxed for objects that survive moonlight better.
MOON_SEPARATION_BASE_DEG = 20.0
MOON_SEPARATION_ILLUM_DEG = 60.0
MOON_TOLERANT_GROUPS = {"Open Clusters", "Globular Clusters", "Double Stars",
                        "Asterisms"}
MOON_TOLERANT_FACTOR = 0.5

# Only these are judged on surface brightness. A galaxy or a nebula is a
# diffuse glow competing with the sky background across its whole area, so
# mean surface brightness is exactly the right test.
#
# A cluster is not. It is a swarm of stars that resolves into points at
# magnification, and what decides whether you see it is the brightness of
# those stars — its integrated magnitude. Judging clusters on mean surface
# brightness threw out M13 (contrast -3.1 under a full moon) while keeping
# compact planetary nebulae, which is backwards: M13 is one of the few things
# genuinely worth pointing at on a bright night.
DIFFUSE_GROUPS = {"Galaxies", "Nebulae"}

GROUP_ORDER = [
    "Galaxies", "Nebulae", "Globular Clusters", "Open Clusters",
    "Double Stars", "Asterisms",
]


@dataclass(frozen=True)
class TargetAssessment:
    """One catalog object evaluated against one night."""

    obj: DeepSkyObject

    peak_altitude_deg: float
    peak_time_utc: datetime
    above_floor: list[Interval]
    hours_above_floor: float
    best_window: Interval

    moon_separation_deg: float           # minimum while the moon is up
    required_separation_deg: float
    limiting_mag_at_peak: float
    contrast_margin: float | None        # sky SB - object SB, mag/arcsec^2

    # True when the object only clears the altitude floor after local midnight.
    # Worth flagging: "observable tonight" and "observable at 3 a.m." are very
    # different propositions when deciding whether to set an alarm.
    visible_late: bool

    # True when the object is well placed but probably below the detection
    # threshold for this sky - light pollution, moonlight, or both. A flag
    # rather than a filter: "not up tonight" and "up but probably too dim" are
    # different answers, and dropping the second hides that the sky, not the
    # geometry, is what is stopping you.
    too_faint: bool

    score: float
    notes: tuple[str, ...]
    previously_logged: bool = False   # False also means "no log context"

    def __post_init__(self) -> None:
        object.__setattr__(self, "peak_time_utc",
                           ensure_utc(self.peak_time_utc, field="peak_time_utc"))

    @property
    def group(self) -> str:
        return self.obj.group


# A true dark window shorter than this is not worth planning a session around,
# so we fall back to astronomical night and flag the moon instead.
MIN_USEFUL_DARK = timedelta(hours=1)


def _observing_window(window: NightWindow) -> Interval | None:
    """Where to look: true dark if there is a useful amount, else the night.

    The threshold is the point. Under a near-full moon the dark window can be
    a handful of minutes — too short to sample, and too short to observe. A
    bare "is it non-empty?" test made `planner targets` return *nothing* on
    such a night, when what the observer needs is the usual target list with
    the moon's effect flagged.
    """
    if window.dark_intervals:
        span = (window.dark_intervals[0][0], window.dark_intervals[-1][1])
        if (span[1] - span[0]) >= MIN_USEFUL_DARK:
            return span
    if window.astronomical_night:
        return (window.astronomical_night[0][0], window.astronomical_night[-1][1])
    # No astronomical night at all, but some dark: use whatever there is.
    if window.dark_intervals:
        return (window.dark_intervals[0][0], window.dark_intervals[-1][1])
    return None


def uses_true_dark(window: NightWindow) -> bool:
    """Whether `_observing_window` picked the true dark window.

    Callers label their output with this. Reporting "true dark" while actually
    sampling astronomical night would misdescribe the conditions the ranking
    was computed under.
    """
    if not window.dark_intervals:
        return False
    span = (window.dark_intervals[0][0], window.dark_intervals[-1][1])
    return (span[1] - span[0]) >= MIN_USEFUL_DARK


def _sample_times(start: datetime, end: datetime, step: timedelta) -> list[datetime]:
    stamps, cursor = [], start
    while cursor <= end:
        stamps.append(cursor)
        cursor += step
    return stamps


def _max_possible_altitude(dec_deg: float, lat_deg: float) -> float:
    """Altitude at upper transit: 90 - |lat - dec|."""
    return 90.0 - abs(lat_deg - dec_deg)


def _prefilter(catalog: list[DeepSkyObject], location: Location,
               min_altitude_deg: float) -> list[DeepSkyObject]:
    """Drop what geometry or missing data rules out before touching Skyfield."""
    return [
        obj for obj in catalog
        if obj.magnitude is not None
        and _max_possible_altitude(obj.dec_deg, location.lat) >= min_altitude_deg
    ]


def _contiguous_spans(times: list[datetime], mask: np.ndarray,
                      step: timedelta, window_end: datetime) -> list[Interval]:
    """Runs of True in `mask` as time intervals.

    A run that is still open at the last sample is extended by one step, since
    the object stays above the floor until roughly the next sample would have
    been taken — but never past `window_end`, because reporting an object as
    observable after dawn would be a lie.
    """
    spans: list[Interval] = []
    start_index: int | None = None
    for index, flag in enumerate(mask):
        if flag and start_index is None:
            start_index = index
        elif not flag and start_index is not None:
            spans.append((times[start_index], times[index]))
            start_index = None
    if start_index is not None:
        spans.append((times[start_index], min(times[-1] + step, window_end)))
    return spans


def _required_separation(obj: DeepSkyObject, illumination: float) -> float:
    """Moon separation an object needs, scaled by how bright the moon is."""
    required = MOON_SEPARATION_BASE_DEG + MOON_SEPARATION_ILLUM_DEG * illumination
    if obj.group in MOON_TOLERANT_GROUPS:
        required *= MOON_TOLERANT_FACTOR
    return required


def _sky_surface_brightness(location: Location, moon_illumination: float,
                            moon_is_up: bool) -> float:
    """Sky brightness in mag/arcsec^2, degraded when the moon is up.

    The moonlight term is a rough linear penalty: a full moon costs about
    3 mag/arcsec^2 at a dark site, which is the right order of magnitude.
    """
    base = location.sqm if location.sqm is not None else BORTLE_SQM[5]
    if not moon_is_up:
        return base
    return base - 3.0 * moon_illumination


# Magnitude range over which the brightness term runs from 1 to 0: an object
# at the limiting magnitude scores 0, one 10 mag brighter scores 1.
BRIGHTNESS_SPAN_MAG = 10.0

# PLAN.md 3.3.4 asks for "a small novelty bonus for objects not in the
# observation log". Deliberately small: never seeing M13 before is a reason to
# nudge it up the list, not a reason to rank it above a better-placed object.
NOVELTY_BONUS = 3.0


def _score(peak_alt: float, hours: float, window_hours: float,
           magnitude: float | None, limiting_mag: float,
           contrast: float | None, separation: float, required: float,
           transit_in_window: bool, unlogged: bool = False) -> float:
    """Weighted 0-100 blend, PLAN.md 3.3.4.

    Altitude uses sin(alt) rather than alt/90 because what matters is airmass,
    which improves fast off the horizon and slowly near the zenith.

    The brightness term carries the most weight, and it has to. Without it the
    score is a pure geometry contest, and any anonymous mag-12 galaxy sitting
    at the zenith all night outranks M31 — which is exactly what happened
    before this term existed.
    """
    altitude_term = math.sin(math.radians(max(peak_alt, 0.0)))
    duration_term = min(hours / window_hours, 1.0) if window_hours > 0 else 0.0

    if magnitude is None:
        brightness_term = 0.25           # unknown magnitude is not a selling point
    else:
        headroom = (limiting_mag - magnitude) / BRIGHTNESS_SPAN_MAG
        brightness_term = min(max(headroom, 0.0), 1.0)

    if contrast is None:
        # Unknown surface brightness gets a mild penalty rather than a neutral
        # 0.5: most objects missing the data are obscure ones nobody measured.
        contrast_term = 0.35
    else:
        contrast_term = min(max((contrast + 3.0) / 6.0, 0.0), 1.0)

    separation_term = min(separation / required, 1.0) if required > 0 else 1.0
    transit_term = 1.0 if transit_in_window else 0.0

    score = 100.0 * (
        0.30 * brightness_term
        + 0.22 * altitude_term
        + 0.16 * duration_term
        + 0.16 * contrast_term
        + 0.08 * separation_term
        + 0.08 * transit_term
    )
    if unlogged:
        score += NOVELTY_BONUS
    return min(score, 100.0)


def assess_targets(
    window: NightWindow,
    equipment: Equipment,
    scope: Scope | None = None,
    *,
    catalog: list[DeepSkyObject] | None = None,
    min_altitude_deg: float = DEFAULT_MIN_ALTITUDE_DEG,
    min_minutes_above_floor: int = DEFAULT_MIN_MINUTES_ABOVE_FLOOR,
    step: timedelta = DEFAULT_STEP,
    contrast_floor: float = DEFAULT_CONTRAST_FLOOR,
    extinction_k: float = DEFAULT_EXTINCTION_K,
    logged: set[str] | None = None,
    include_too_faint: bool = False,
) -> list[TargetAssessment]:
    """Every catalog object that passes the hard filters, best first.

    `logged` is the set of catalog ids already in the observation log. Objects
    absent from it get PLAN.md 3.3.4's small novelty bonus. Passed in as data
    so the engine never reads the log database itself.

    `include_too_faint` keeps objects that clear every positional test -
    altitude, time up, moon separation - but fall below the detection
    threshold for this sky. They come back flagged `too_faint` and sorted
    to the end of their group, so the list can say "this is up, but the
    moon or the light pollution will beat you" instead of omitting it.
    """
    location = window.location
    scope = scope or equipment.scope()

    span = _observing_window(window)
    if span is None:
        return []
    start, end = span
    times = _sample_times(start, end, step)
    if len(times) < 2:
        return []

    # The midnight that falls *during* this night, for the "late" flag.
    midnight = local_midnight_utc(window.date + timedelta(days=1), location.tz)
    window_hours = (end - start).total_seconds() / 3600.0

    candidates = _prefilter(catalog if catalog is not None else load_catalog(),
                            location, min_altitude_deg)
    if not candidates:
        return []

    eph = load_ephemeris()
    observer = _observer(eph, location)
    moon = eph.target("moon")
    sky_times = eph.timescale.from_datetimes(times)

    stars = Star(
        ra_hours=np.array([o.ra_deg / 15.0 for o in candidates]),
        dec_degrees=np.array([o.dec_deg for o in candidates]),
    )

    # altitude[i][j] = altitude of object j at time i.
    altitudes = np.empty((len(times), len(candidates)))
    azimuths = np.empty((len(times), len(candidates)))
    separations = np.empty((len(times), len(candidates)))
    moon_up = np.empty(len(times), dtype=bool)

    for index in range(len(times)):
        moment = sky_times[index]
        here = observer.at(moment)
        star_positions = here.observe(stars).apparent()
        alt, az, _ = star_positions.altaz()
        altitudes[index] = alt.degrees
        azimuths[index] = az.degrees

        moon_position = here.observe(moon).apparent()
        moon_alt = float(moon_position.altaz()[0].degrees)
        moon_up[index] = moon_alt > 0.0
        separations[index] = star_positions.separation_from(moon_position).degrees

    min_samples = max(1, math.ceil(min_minutes_above_floor
                                   / (step.total_seconds() / 60.0)))
    any_moon_up = bool(moon_up.any())
    sky_sb = _sky_surface_brightness(location, window.moon_illumination, any_moon_up)
    # Limiting magnitude must use the *moonlit* sky, not the site's dark-sky
    # SQM. Using the moonless value let mag-12 galaxies pass under a 97% moon:
    # the surface-brightness contrast test was correctly degraded for
    # moonlight while the point-source magnitude test was not, so compact
    # objects slipped through a filter that should have caught them.
    base_limit = telescopic_limiting_mag(scope, sky_sb, equipment.eye_pupil_mm)

    horizon = location.horizon
    # None means "no log context, do not apply novelty at all"; an empty set
    # means "the log exists and is empty", so everything is genuinely novel.
    # Collapsing the two would silently inflate every score by the bonus.
    has_log_context = logged is not None
    logged = logged or set()

    results: list[TargetAssessment] = []
    for j, obj in enumerate(candidates):
        column = altitudes[:, j]
        # The floor is whichever is higher: the configured altitude floor, or
        # the obstruction horizon in the direction the object actually sits.
        # With the default 25 deg floor and a modest profile the horizon rarely
        # binds; it matters when the floor is lowered or a ridge is steep.
        if horizon.is_flat:
            effective_floor = np.full(len(times), min_altitude_deg)
        else:
            effective_floor = np.maximum(
                min_altitude_deg,
                np.array([horizon.min_altitude_at(a) for a in azimuths[:, j]]),
            )
        above = column >= effective_floor
        if above.sum() < min_samples:
            continue

        spans = _contiguous_spans(times, above, step, end)
        longest = max(spans, key=lambda s: s[1] - s[0])
        if (longest[1] - longest[0]) < timedelta(minutes=min_minutes_above_floor):
            continue

        peak_index = int(np.argmax(column))
        peak_alt = float(column[peak_index])

        # Moon separation only constrains when the moon is actually up.
        if any_moon_up:
            separation = float(separations[moon_up, j].min())
        else:
            separation = 180.0
        required = _required_separation(obj, window.moon_illumination)
        # Moon glare is a sky condition, not a positional one: the object is
        # up and pointable, it is just drowned. Treated like any other
        # too-faint case so the list can say so rather than omitting it.
        swamped_by_moon = separation < required
        if swamped_by_moon and not include_too_faint:
            continue

        limit = base_limit - extinction_mag(max(peak_alt, 1.0), extinction_k)

        notes: list[str] = []
        contrast: float | None = None
        too_faint = False
        diffuse = obj.group in DIFFUSE_GROUPS
        if diffuse and obj.is_extended and obj.surface_brightness is not None:
            # PLAN.md 7: extended objects live or die by surface brightness.
            # A large, bright object is judged on its core rather than on the
            # mean over an extent dominated by faint outer nebulosity. The
            # test itself is unchanged, so a bright enough sky still wins.
            concentrated = (obj.magnitude is not None
                            and obj.magnitude <= BRIGHT_CORE_MAG)
            effective_sb = (obj.surface_brightness
                            - (BRIGHT_CORE_SB_BONUS if concentrated else 0.0))
            contrast = sky_sb - effective_sb
            too_faint = contrast < contrast_floor
            # Only worth saying when the correction changed the answer. An
            # object that clears the floor on its mean too needs no
            # explanation of a distinction that did not arise.
            mean_contrast = sky_sb - obj.surface_brightness
            if concentrated and mean_contrast < contrast_floor:
                notes.append(
                    f"large and bright: outer extent {abs(mean_contrast):.1f} "
                    f"mag/arcsec^2 below this sky, judged on its core"
                )
            if contrast < 0 and not too_faint and not concentrated:
                notes.append(f"{abs(contrast):.1f} mag/arcsec^2 below sky")
        else:
            # Clusters, doubles and asterisms: integrated magnitude decides.
            magnitude = obj.magnitude
            too_faint = bool(magnitude is not None and magnitude > limit)
            if diffuse and obj.surface_brightness is None and obj.is_extended:
                notes.append("no surface brightness in catalog")

        if swamped_by_moon:
            too_faint = True
            notes.append(f"washed out by the moon - {separation:.0f} deg away, "
                         f"needs {required:.0f} deg")
        elif too_faint:
            if not include_too_faint:
                continue
            notes.append(f"below the detection threshold for this sky "
                         f"(limiting mag {limit:.1f})")

        unlogged = has_log_context and obj.name not in logged
        if unlogged:
            notes.append("not yet logged")

        transit_in_window = bool(0 < peak_index < len(times) - 1)
        if not transit_in_window:
            notes.append("transits outside the window")
        # Only when it is close-but-workable; the swamped case above already
        # said it, with the threshold included.
        if any_moon_up and not swamped_by_moon and separation < required * 1.5:
            notes.append(f"{separation:.0f} deg from the moon")

        results.append(
            TargetAssessment(
                obj=obj,
                peak_altitude_deg=peak_alt,
                peak_time_utc=times[peak_index],
                above_floor=spans,
                hours_above_floor=sum(
                    (e - s).total_seconds() for s, e in spans) / 3600.0,
                best_window=longest,
                moon_separation_deg=separation,
                required_separation_deg=required,
                limiting_mag_at_peak=limit,
                contrast_margin=contrast,
                visible_late=spans[0][0] >= midnight,
                too_faint=too_faint,
                score=_score(peak_alt,
                             sum((e - s).total_seconds() for s, e in spans) / 3600.0,
                             window_hours, obj.magnitude, limit, contrast,
                             separation, required, transit_in_window,
                             unlogged),
                notes=tuple(notes),
                # Only meaningful with a log context; without one we do not
                # know, and claiming 'seen before' would be a fabrication.
                previously_logged=has_log_context and not unlogged,
            )
        )

    results.sort(key=lambda a: a.score, reverse=True)
    return results


def _brightness_key(assessment: TargetAssessment) -> tuple[bool, float]:
    """Sort key: solid targets first, then brightest.

    Too-faint objects always sort after the rest of their group. They are
    listed for completeness, not recommended.
    """
    magnitude = assessment.obj.magnitude
    return (assessment.too_faint,
            magnitude if magnitude is not None else 99.0)


def _score_key(assessment: TargetAssessment) -> tuple[bool, float]:
    """Same rule for score ordering: faint last, best score first."""
    return (assessment.too_faint, -assessment.score)


@dataclass(frozen=True)
class CatalogEntry:
    """A catalog object, carrying tonight's assessment when it has one.

    The unfiltered view needs to show objects that are *not* observable
    tonight, so it cannot be a list of assessments. This pairs every catalog
    object with its assessment or None, which is what lets the UI badge each
    row rather than silently omitting the ones that fail.
    """

    obj: DeepSkyObject
    assessment: TargetAssessment | None = None

    @property
    def visible_tonight(self) -> bool:
        return self.assessment is not None

    @property
    def visible_late(self) -> bool:
        return bool(self.assessment and self.assessment.visible_late)

    @property
    def too_faint(self) -> bool:
        return bool(self.assessment and self.assessment.too_faint)

    @property
    def sort_magnitude(self) -> float:
        magnitude = self.obj.magnitude
        return magnitude if magnitude is not None else 99.0


def group_catalog(catalog: list[DeepSkyObject],
                  assessments: list[TargetAssessment],
                  limit_per_group: int | None = None,
                  by: str = "constellation") -> dict[str, list[CatalogEntry]]:
    """Every catalog object, grouped and annotated with tonight's assessment.

    No observability filtering at all — that is the point of the unfiltered
    view. Objects are ordered brightest first within each group, and groups
    are ordered by their best-scoring observable member so tonight's good
    regions still lead. Groups with nothing observable sort last rather than
    disappearing.

    `limit_per_group=None` means no cap, which is the honest default here: a
    default of 1000 quietly truncated Virgo, which has 1051 catalogued objects
    in the Virgo Cluster, while the caller was asking for everything.
    """
    if by not in {"type", "constellation"}:
        raise ValueError(f"unknown grouping {by!r}; use 'type' or 'constellation'")

    by_name = {a.obj.name: a for a in assessments}
    grouped: dict[str, list[CatalogEntry]] = {}
    for obj in catalog:
        key = (obj.group if by == "type"
               else obj.constellation.strip() or "unknown")
        grouped.setdefault(key, []).append(
            CatalogEntry(obj=obj, assessment=by_name.get(obj.name)))

    def group_rank(entries: list[CatalogEntry]) -> float:
        scores = [e.assessment.score for e in entries if e.assessment]
        return max(scores) if scores else -1.0

    return {
        key: (sorted(entries, key=lambda e: e.sort_magnitude)
              if limit_per_group is None
              else sorted(entries, key=lambda e: e.sort_magnitude)[:limit_per_group])
        for key, entries in sorted(grouped.items(),
                                   key=lambda pair: group_rank(pair[1]),
                                   reverse=True)
    }


def group_targets(assessments: list[TargetAssessment],
                  limit_per_group: int = DEFAULT_GROUP_LIMIT,
                  by: str = "type",
                  sort: str = "score") -> dict[str, list[TargetAssessment]]:
    """Bucket the ranked list, capped per bucket.

    `by="type"` uses the observing groups from PLAN.md 3.3.5 in their fixed
    display order. `by="constellation"` buckets by sky region instead — which
    is what you want when the question is "where do I point next", since
    everything in one constellation rises and sets together.

    `sort` orders *within* a bucket. "score" is the ranking model; "brightness"
    is a plain magnitude order, which is the more natural way to read a
    constellation's contents once you have decided to point there — you work
    down from the showpieces rather than trusting a composite number.

    Buckets themselves stay ordered by their best score either way: that
    answers "which region is worth my time tonight", which brightness alone
    does not.
    """
    if by not in {"type", "constellation"}:
        raise ValueError(f"unknown grouping {by!r}; use 'type' or 'constellation'")
    if sort not in {"score", "brightness"}:
        raise ValueError(f"unknown sort {sort!r}; use 'score' or 'brightness'")

    grouped: dict[str, list[TargetAssessment]] = {}
    for assessment in assessments:
        key = (assessment.group if by == "type"
               else assessment.obj.constellation.strip() or "unknown")
        grouped.setdefault(key, []).append(assessment)

    def arrange(items: list[TargetAssessment]) -> list[TargetAssessment]:
        # Cap on score first so a bucket keeps its best targets, then present
        # them in the requested order. Capping on brightness would drop a
        # well-placed object in favour of a brighter one that barely clears
        # the horizon.
        kept = sorted(items, key=_score_key)[:limit_per_group]
        if sort == "brightness":
            kept.sort(key=_brightness_key)
        return kept

    if by == "constellation":
        # Best-scoring constellation first; the fixed GROUP_ORDER is meaningless
        # here and alphabetical would bury tonight's best region.
        return {
            key: arrange(items)
            for key, items in sorted(
                grouped.items(),
                key=lambda pair: max(a.score for a in pair[1]),
                reverse=True,
            )
        }

    ordered: dict[str, list[TargetAssessment]] = {}
    for group in GROUP_ORDER:
        if group in grouped:
            ordered[group] = arrange(grouped[group])
    for group, items in grouped.items():         # anything not in GROUP_ORDER
        if group not in ordered:
            ordered[group] = arrange(items)
    return ordered
