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
    """Highest-mean run of consecutive slots, minimum one hour.

    Scans every window of two or more slots and keeps the best mean. The
    minimum stops a single freak 30-minute slot being reported as "the best
    window" when nothing around it is observable.
    """
    if len(slots) < 2:
        if slots:
            value = slots[0].deep_sky if deep_sky else slots[0].planetary
            return (slots[0].time_utc, slots[0].time_utc + SLOT), value
        return None, 0.0

    values = [s.deep_sky if deep_sky else s.planetary for s in slots]
    best_mean, best_range = -1.0, None
    for start in range(len(slots)):
        running = 0.0
        for end in range(start, len(slots)):
            running += values[end]
            length = end - start + 1
            if length < 2:
                continue
            mean = running / length
            if mean > best_mean:
                best_mean = mean
                best_range = (start, end)

    if best_range is None:
        return None, 0.0
    start_index, end_index = best_range
    return (slots[start_index].time_utc, slots[end_index].time_utc + SLOT), best_mean


def score_night(window: NightWindow, forecast: Forecast | None = None,
                *, slot: timedelta = SLOT) -> NightScore:
    """Score the astronomical night in slots and aggregate.

    Scoring runs across astronomical night rather than the true dark window,
    because the moon factor is what expresses moonlight — excluding moonlit
    hours entirely would double-count the penalty and hide observable time.
    """
    spans = window.astronomical_night
    if not spans:
        return NightScore(
            slots=[], deep_sky_peak=0.0, deep_sky_mean=0.0,
            planetary_peak=0.0, planetary_mean=0.0,
            best_window=None, best_window_score=0.0,
            dark_hours=window.dark_hours,
            weather_available=bool(forecast and forecast.available),
            weather_note="no astronomical night at this location on this date",
            seeing_estimated=False, dew_warning=False,
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
    )


def verdict(score: NightScore, window: NightWindow) -> str:
    """One human line, naming the thing that actually limits the night."""
    if not score.slots:
        return "No astronomical night."

    if not score.weather_available:
        return (f"{score.dark_hours:.1f} h of true dark; "
                f"weather unavailable ({score.weather_note}).")

    peak = max(score.slots, key=lambda s: s.deep_sky)
    name, value = peak.deep_sky_factors.weakest()

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

    return (f"{lead} - {detail}. {score.dark_hours:.1f} h of true dark, "
            f"deep-sky peak {score.deep_sky_peak:.0f} ({score.deep_sky_grade}), "
            f"planetary peak {score.planetary_peak:.0f} ({score.planetary_grade}).")
