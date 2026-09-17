"""Condition scoring (PLAN.md §3.2).

Two scores, not one — a bright Moon with steady air is an excellent planetary
night and a terrible galaxy night, and collapsing that into a single number
throws away the only thing the observer needs to decide.

    slot_score = 100 x clear x transparency x moon x seeing x wind x dew

Multiplicative on purpose: any single dealbreaker dominates. 90% cloud cannot
be averaged away by superb seeing.

Every factor is reported alongside the score. PLAN.md §3.2: "always show the
factor breakdown so the number is auditable." A score with no breakdown is
not auditable, so `FactorBreakdown` is part of the return type rather than a
debugging extra.

Darkness duration is deliberately *not* folded into the score. A two-hour dark
window that is crystal clear is still a good night — just a short one — so
hours of true dark are reported separately.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from .ephem import Interval, NightWindow, altaz_series
from .timeutil import ensure_utc
from .weather import Forecast, HourlyConditions

SLOT = timedelta(minutes=30)

# Dew warning threshold: within this many degrees C of the dew point, optics fog.
DEW_SPREAD_WARNING_C = 2.0

# Wind: full marks below this, falling linearly to WIND_FLOOR at the ceiling.
WIND_FULL_KMH = 16.0        # ~10 mph
WIND_CEILING_KMH = 40.0     # ~25 mph
WIND_FLOOR = 0.4

# Below this ratio of session mean to best-window score, the good part of a
# session is a slice rather than the whole of it, and the verdict says how
# long it is. 0.7 sits between an ordinary night's smooth variation (0.83 on
# a clear four-hour session here) and one that genuinely clouds over (0.50).
LOPSIDED_RATIO = 0.7

GRADE_BANDS = [
    (90, "A"), (80, "B"), (70, "C"), (60, "D"), (0, "F"),
]


@dataclass(frozen=True)
class FactorBreakdown:
    """The six multiplicative factors behind one slot score, each 0-1."""

    clear: float
    transparency: float
    moon: float
    seeing: float
    wind: float
    dew: float

    @property
    def product(self) -> float:
        return (self.clear * self.transparency * self.moon
                * self.seeing * self.wind * self.dew)

    @classmethod
    def mean_of(cls, breakdowns: list["FactorBreakdown"]) -> "FactorBreakdown":
        """The arithmetic mean of each factor across a night.

        Not a score -- the slot score stays multiplicative, and averaging the
        products would smear a dealbreaker across the whole night, which is
        precisely what the multiplication exists to prevent. This is for
        *describing* a night: "the cloud factor averaged 0.38" is a true
        statement about the night, where the peak slot's 1.00 is a true
        statement about thirty minutes of it.
        """
        if not breakdowns:
            return cls(1.0, 1.0, 1.0, 1.0, 1.0, 1.0)
        count = len(breakdowns)
        return cls(
            clear=sum(b.clear for b in breakdowns) / count,
            transparency=sum(b.transparency for b in breakdowns) / count,
            moon=sum(b.moon for b in breakdowns) / count,
            seeing=sum(b.seeing for b in breakdowns) / count,
            wind=sum(b.wind for b in breakdowns) / count,
            dew=sum(b.dew for b in breakdowns) / count,
        )

    def weakest(self) -> tuple[str, float]:
        """The factor doing the most damage — what to blame the score on."""
        factors = {
            "cloud": self.clear, "transparency": self.transparency,
            "moon": self.moon, "seeing": self.seeing,
            "wind": self.wind, "dew": self.dew,
        }
        name = min(factors, key=factors.get)
        return name, factors[name]


@dataclass(frozen=True)
class SlotScore:
    """One 30-minute slot, scored for both observing modes."""

    time_utc: datetime
    deep_sky: float
    planetary: float
    deep_sky_factors: FactorBreakdown
    planetary_factors: FactorBreakdown
    moon_altitude_deg: float
    dew_warning: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "time_utc",
                           ensure_utc(self.time_utc, field="time_utc"))


@dataclass(frozen=True)
class NightScore:
    """Aggregate verdict for a whole night."""

    slots: list[SlotScore]
    deep_sky_peak: float
    deep_sky_mean: float
    planetary_peak: float
    planetary_mean: float
    best_window: Interval | None
    best_window_score: float
    dark_hours: float
    weather_available: bool
    weather_note: str | None
    seeing_estimated: bool
    dew_warning: bool
    #: Length of the interval actually scored. The astronomical night when no
    #: session was given, the session when one was -- which is what "the good
    #: part covers x of y" must be measured against, or a four-hour session
    #: gets compared against a seven-hour night and always looks like a sliver.
    scored_hours: float = 0.0

    @property
    def mean_factors_deep_sky(self) -> FactorBreakdown:
        """Each factor averaged over the night, for deep sky.

        The grade stays peak-based on purpose -- a two-hour clear window is
        still worth driving out for. But every *description* of the night was
        also being taken from the peak slot, and the peak slot is by
        construction the least cloudy one. On a night that is clear for an
        hour and socked in for five, that meant the breakdown reported
        `clear = 1.00` and the verdict blamed whatever came second. Cloud
        could never be named as the limiting factor on exactly the nights
        where cloud is the whole story.
        """
        return FactorBreakdown.mean_of([s.deep_sky_factors for s in self.slots])

    @property
    def mean_factors_planetary(self) -> FactorBreakdown:
        return FactorBreakdown.mean_of([s.planetary_factors for s in self.slots])

    @property
    def is_gradeable(self) -> bool:
        """False when the score is not a verdict on anything.

        Two ways that happens:

        * No weather. PLAN.md 7: past the forecast horizon we must return an
          explicit "no forecast" state, not a silently degraded score. Without
          weather every weather-derived factor defaults to 1.0, which makes any
          night look clear — the number is real but describes only darkness and
          moonlight, and must not be dressed up as a grade.
        * No slots at all, which is polar summer: the sun never reaches -18
          degrees, so there is no night to grade. Reporting "0, grade F" there
          would read as a bad night rather than no night.
        """
        return self.weather_available and bool(self.slots)

    @property
    def deep_sky_grade(self) -> str:
        return grade(self.deep_sky_peak) if self.is_gradeable else "n/a"

    @property
    def planetary_grade(self) -> str:
        return grade(self.planetary_peak) if self.is_gradeable else "n/a"


def grade(score: float) -> str:
    for threshold, letter in GRADE_BANDS:
        if score >= threshold:
            return letter
    return "F"


# --- individual factors -----------------------------------------------------

def clear_factor(cloud_cover_pct: float | None) -> float:
    """(1 - cloud/100)^1.5. Unknown cloud is treated as clear, and flagged
    elsewhere as "weather unavailable" rather than silently scoring 0."""
    if cloud_cover_pct is None:
        return 1.0
    fraction = max(0.0, min(cloud_cover_pct / 100.0, 1.0))
    return (1.0 - fraction) ** 1.5


def transparency_factor(quality: float | None, high_cloud_pct: float | None,
                        humidity_pct: float | None, *, deep_sky: bool) -> float:
    """Transparency, weighted hard for deep sky and barely at all for planets.

    PLAN.md §3.2: high cirrus kills galaxies but leaves planets workable, so
    the planetary variant is compressed into a 0.8-1.0 band.
    """
    if quality is None:
        base = 1.0
        if high_cloud_pct is not None:
            base *= 1.0 - 0.5 * max(0.0, min(high_cloud_pct / 100.0, 1.0))
        if humidity_pct is not None:
            base *= 1.0 - 0.3 * max(0.0, (humidity_pct - 60.0) / 40.0)
        base = max(0.0, min(base, 1.0))
    else:
        base = max(0.0, min(quality, 1.0))

    if deep_sky:
        return base
    return 0.8 + 0.2 * base


def moon_factor(illumination: float, moon_altitude_deg: float, *,
                deep_sky: bool) -> float:
    """1 - 0.75 * illum * max(0, sin(alt))^0.6 for deep sky; 1.0 for planets.

    The Moon is irrelevant to a planetary session, or is itself the target.
    """
    if not deep_sky:
        return 1.0
    if moon_altitude_deg <= 0:
        return 1.0
    elevation = max(0.0, math.sin(math.radians(moon_altitude_deg))) ** 0.6
    return max(0.0, 1.0 - 0.75 * max(0.0, min(illumination, 1.0)) * elevation)


def seeing_factor(quality: float | None, *, deep_sky: bool) -> float:
    """Mild for deep sky, dominant for planets (PLAN.md §3.2)."""
    if quality is None:
        return 1.0 if deep_sky else 0.85
    clamped = max(0.0, min(quality, 1.0))
    if deep_sky:
        return 0.85 + 0.15 * clamped
    return clamped ** 1.5


def wind_factor(gust_kmh: float | None) -> float:
    """Full marks under ~10 mph, falling to 0.4 by ~25 mph. Dobs shake."""
    if gust_kmh is None:
        return 1.0
    if gust_kmh <= WIND_FULL_KMH:
        return 1.0
    if gust_kmh >= WIND_CEILING_KMH:
        return WIND_FLOOR
    span = (gust_kmh - WIND_FULL_KMH) / (WIND_CEILING_KMH - WIND_FULL_KMH)
    return 1.0 - span * (1.0 - WIND_FLOOR)


def dew_factor(spread_c: float | None) -> tuple[float, bool]:
    """0.85 and a warning flag when the dew point spread is under 2 C."""
    if spread_c is None:
        return 1.0, False
    if spread_c < DEW_SPREAD_WARNING_C:
        return 0.85, True
    return 1.0, False


# --- slot and night scoring -------------------------------------------------

def score_slot(conditions: HourlyConditions | None, illumination: float,
               moon_altitude_deg: float, when: datetime) -> SlotScore:
    """Score one slot for both modes from the conditions nearest to it."""
    cloud = conditions.cloud_cover if conditions else None
    high_cloud = conditions.cloud_high if conditions else None
    humidity = conditions.humidity_pct if conditions else None
    gust = conditions.wind_gust_kmh if conditions else None
    spread = conditions.dew_point_spread_c if conditions else None
    seeing_quality = conditions.seeing_quality if conditions else None
    transparency_quality = conditions.transparency_quality if conditions else None

    clear = clear_factor(cloud)
    wind = wind_factor(gust)
    dew, dew_warning = dew_factor(spread)

    deep_sky = FactorBreakdown(
        clear=clear,
        transparency=transparency_factor(transparency_quality, high_cloud,
                                         humidity, deep_sky=True),
        moon=moon_factor(illumination, moon_altitude_deg, deep_sky=True),
        seeing=seeing_factor(seeing_quality, deep_sky=True),
        wind=wind, dew=dew,
    )
    planetary = FactorBreakdown(
        clear=clear,
        transparency=transparency_factor(transparency_quality, high_cloud,
                                         humidity, deep_sky=False),
        moon=moon_factor(illumination, moon_altitude_deg, deep_sky=False),
        seeing=seeing_factor(seeing_quality, deep_sky=False),
        wind=wind, dew=dew,
    )

    return SlotScore(
        time_utc=when,
        deep_sky=100.0 * deep_sky.product,
        planetary=100.0 * planetary.product,
        deep_sky_factors=deep_sky,
        planetary_factors=planetary,
        moon_altitude_deg=moon_altitude_deg,
        dew_warning=dew_warning,
    )


def _best_contiguous(slots: list[SlotScore], *, deep_sky: bool
                     ) -> tuple[Interval | None, float]:
    """Longest run of consecutive slots at the highest mean, minimum one hour.

    Scans every window of two or more slots and keeps the best mean. The
    minimum stops a single freak 30-minute slot being reported as "the best
    window" when nothing around it is observable.

    **Ties go to the longer window.** A strict `>` kept the first window found
    at the best mean, which on a uniformly good night is the earliest pair of
    slots -- so a flawless eight-hour night reported a one-hour best window
    and implied the other seven were worse. On a night where conditions really
    are even, the best window is the whole night.
    """
    if len(slots) < 2:
        if slots:
            value = slots[0].deep_sky if deep_sky else slots[0].planetary
            return (slots[0].time_utc, slots[0].time_utc + SLOT), value
        return None, 0.0

    values = [s.deep_sky if deep_sky else s.planetary for s in slots]
    best_mean, best_length, best_range = -1.0, 0, None
    for start in range(len(slots)):
        running = 0.0
        for end in range(start, len(slots)):
            running += values[end]
            length = end - start + 1
            if length < 2:
                continue
            mean = running / length
            # A tolerance rather than `==`: these means are sums of floats
            # divided by a count, so two genuinely equal windows differ in the
            # last bits often enough to matter.
            better = mean > best_mean + 1e-9
            same = abs(mean - best_mean) <= 1e-9
            if better or (same and length > best_length):
                best_mean, best_length = mean, length
                best_range = (start, end)

    if best_range is None:
        return None, 0.0
    start_index, end_index = best_range
    return (slots[start_index].time_utc, slots[end_index].time_utc + SLOT), best_mean


def score_night(window: NightWindow, forecast: Forecast | None = None,
                *, slot: timedelta = SLOT,
                session: Interval | None = None) -> NightScore:
    """Score an observing session in slots and aggregate.

    Without a `session` this scores astronomical night, which is the old
    behaviour: the true dark window is deliberately not used, because the moon
    factor is what expresses moonlight and excluding moonlit hours entirely
    would double-count the penalty and hide observable time.

    With one, it scores exactly the hours the observer says they will be out.
    That is the honest denominator for "what will conditions be like" -- see
    `engine/session.py`. A night that is clear until midnight and overcast
    afterwards is a good session and a poor night, and which of those the
    observer needs depends on when they are going home.
    """
    spans = [session] if session else window.astronomical_night
    if not spans:
        return NightScore(
            slots=[], deep_sky_peak=0.0, deep_sky_mean=0.0,
            planetary_peak=0.0, planetary_mean=0.0,
            best_window=None, best_window_score=0.0,
            dark_hours=window.dark_hours,
            weather_available=bool(forecast and forecast.available),
            weather_note="no observing hours at this location on this date",
            seeing_estimated=False, dew_warning=False, scored_hours=0.0,
        )

    start, end = spans[0][0], spans[-1][1]
    stamps: list[datetime] = []
    cursor = start
    while cursor < end:
        stamps.append(cursor)
        cursor += slot
    if not stamps:
        stamps = [start]

    moon = altaz_series("moon", window.location, stamps[0], stamps[-1], slot)
    altitudes = dict(zip(moon.times_utc, moon.alt_deg))

    slots = [
        score_slot(
            forecast.at(when) if forecast and forecast.available else None,
            window.moon_illumination,
            altitudes.get(when, -90.0),
            when,
        )
        for when in stamps
    ]

    deep_sky_values = [s.deep_sky for s in slots]
    planetary_values = [s.planetary for s in slots]
    best_window, best_score = _best_contiguous(slots, deep_sky=True)

    # Whether seeing was estimated must describe *this night's* hours. The
    # Forecast-wide flag is true whenever any of Open-Meteo's 16 days sits past
    # the 7Timer horizon, which says nothing about tonight.
    relevant = (forecast.covering(start, end)
                if forecast and forecast.available else [])
    seeing_estimated = any(h.seeing_estimated for h in relevant)

    return NightScore(
        slots=slots,
        deep_sky_peak=max(deep_sky_values),
        deep_sky_mean=sum(deep_sky_values) / len(deep_sky_values),
        planetary_peak=max(planetary_values),
        planetary_mean=sum(planetary_values) / len(planetary_values),
        best_window=best_window,
        best_window_score=best_score,
        dark_hours=window.dark_hours,
        weather_available=bool(forecast and forecast.available),
        weather_note=(forecast.unavailable_reason
                      if forecast and not forecast.available else None),
        seeing_estimated=seeing_estimated,
        dew_warning=any(s.dew_warning for s in slots),
        scored_hours=(end - start).total_seconds() / 3600.0,
    )


def verdict(score: NightScore, window: NightWindow) -> str:
    """One human line, naming the thing that actually limits the night."""
    if not score.slots:
        return "No astronomical night."

    if not score.weather_available:
        return (f"{score.dark_hours:.1f} h of true dark; "
                f"weather unavailable ({score.weather_note}).")

    # Averaged over the night, not read off the best slot. See
    # `NightScore.mean_factors_deep_sky` for why that distinction matters.
    name, value = score.mean_factors_deep_sky.weakest()

    if score.deep_sky_peak >= 80 and value > 0.85:
        lead = "Good night out"
    elif score.deep_sky_peak >= 60:
        lead = "Workable"
    elif score.deep_sky_peak >= 35:
        lead = "Marginal"
    else:
        lead = "Not worth it"

    if name == "moon":
        detail = (f"the Moon is {window.moon_illumination * 100:.0f}% and up "
                  f"for part of the night")
    elif name == "cloud":
        detail = f"cloud is the limit ({value:.2f} clear factor)"

    elif name == "dew":
        detail = "dew point is close - expect fogging"
    elif name == "wind":
        detail = "wind will shake the scope"
    elif name == "seeing":
        detail = "seeing is soft"
    else:
        detail = "transparency is poor"

    # Just the verdict and what limits it. The dark hours and both peak
    # scores used to be repeated here, and they are already on the dials and
    # in the key facts a few pixels away -- restating them made the line long
    # enough to wrap without telling anyone anything new.
    #
    # The one thing worth adding: how much of the session the good part
    # covers. "Workable" describing a single clear hour inside six of cloud
    # is true and useless.
    #
    # The trigger is how *lopsided* the session is, not how long the best
    # window happens to be. Conditions vary smoothly across any night -- an
    # object culminates, the moon sets -- so the highest-mean run is almost
    # always an hour or two, and keying off its length alone fired this on
    # nearly every night, including perfectly even ones. Comparing the
    # session mean to its best window asks the question that matters: is the
    # rest much worse than the best part, or about the same?
    #
    # Measured against what was actually scored: the session, when given.
    span_hours = score.scored_hours or score.dark_hours
    if (score.best_window is not None and span_hours > 0
            and score.best_window_score > 1.0
            and score.deep_sky_mean < LOPSIDED_RATIO * score.best_window_score):
        start, end = score.best_window
        best_hours = (end - start).total_seconds() / 3600.0
        return (f"{lead} for {best_hours:.1f} h of "
                f"{span_hours:.1f} - {detail}.")
    return f"{lead} - {detail}."
