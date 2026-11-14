"""The week ahead: several nights scored side by side.

The dashboard answers "is tonight worth it?". The question just before that
one is usually "which night this week is?", and answering it a night at a
time -- step the date forward, read the dials, step again -- is seven page
loads to learn one thing. This scores a run of consecutive nights in one go,
from one forecast, so they can be laid out next to each other and the good
ones picked out at a glance.

Each night is scored exactly as the dashboard scores it (`score_night` over
the default session), so a night's figures here and on its own dashboard are
the same numbers by construction rather than a cheaper approximation that
could disagree. Two deliberate choices follow from that:

* **The default session, every night.** Hours an observer sets are absolute
  instants for one night; carried to the next they would fall outside it.
  Dusk to 01:00 is the one session every night has, which is also what makes
  the nights comparable with each other.
* **One forecast for the whole run.** Fetched once by the caller and shared,
  so seven nights cost one request, not seven. A night the forecast does not
  reach is scored without weather and so is not gradeable -- the same honest
  "no forecast" the dashboard gives, rather than a grade that only measures
  the Moon.

Past 7Timer's 72 hours seeing and transparency are estimated from humidity
and wind, and each night carries that flag for itself: by the fourth night
of a week the planetary score is largely a cloud-and-wind score.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .ephem import Interval, night_window
from .locations import Location
from .scoring import grade, limiting_factor, score_night
from .session import dark_overlap_hours, resolve_session
from .timeutil import ensure_utc
from .weather import Forecast, unavailable

#: A week, and a cap: Open-Meteo reaches about 16 days, so a longer run would
#: only add nights that are certain to have no forecast.
DEFAULT_NIGHTS = 7
MAX_NIGHTS = 14


@dataclass(frozen=True)
class NightOutlook:
    """One night of the run, summarised for a glance."""

    date: date
    #: The default session it was scored over, or None where the night is
    #: too short to hold one (summer near the poles).
    session: Interval | None

    deep_sky_peak: float
    deep_sky_mean: float
    planetary_peak: float
    planetary_mean: float
    #: The best stretch of the session for deep sky, and its mean score: the
    #: peak alone flatters a night that is clear for an hour and cloudy for
    #: four, and this says which hour.
    best_window: Interval | None
    best_window_score: float
    #: False with no forecast for the night, or no night at all. The scores
    #: are then only darkness and moonlight, and are not a verdict.
    is_gradeable: bool
    weather_available: bool
    weather_note: str | None
    seeing_estimated: bool
    limiting_factor: str | None

    #: Over the session's slots, like the dashboard's figures; None without
    #: a forecast for the night.
    cloud_mean_pct: float | None
    temperature_low_c: float | None

    moon_illumination: float
    moon_waxing: bool
    #: How much of the session is true dark: the Sun 18 degrees down and the
    #: Moon below the horizon.
    dark_hours: float

    def __post_init__(self) -> None:
        for name in ("session", "best_window"):
            span = getattr(self, name)
            if span is not None:
                object.__setattr__(self, name, (ensure_utc(span[0], field=name),
                                                ensure_utc(span[1], field=name)))

    @property
    def deep_sky_grade(self) -> str:
        return grade(self.deep_sky_peak) if self.is_gradeable else "n/a"

    @property
    def planetary_grade(self) -> str:
        return grade(self.planetary_peak) if self.is_gradeable else "n/a"


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def week_outlook(location: Location, first: date, forecast: Forecast | None,
                 nights: int = DEFAULT_NIGHTS) -> list[NightOutlook]:
    """`nights` consecutive nights from `first`, each scored over its default
    session from the one `forecast`.

    Never raises for want of weather: a missing or unavailable forecast, or
    one that does not reach a night, gives that night `is_gradeable=False`
    and says why in `weather_note`.
    """
    if not 1 <= nights <= MAX_NIGHTS:
        raise ValueError(f"nights must be 1-{MAX_NIGHTS}, got {nights}")

    out: list[NightOutlook] = []
    for offset in range(nights):
        day = first + timedelta(days=offset)
        window = night_window(day, location)
        session = resolve_session(window)

        usable = forecast if forecast is not None and forecast.available else None
        if usable is not None and session is not None \
                and not usable.covers(session[0], session[1]):
            usable = None
        if usable is None:
            if forecast is not None and not forecast.available:
                reason = forecast.unavailable_reason or "no forecast"
            elif forecast is None:
                reason = "no forecast"
            elif forecast.fallback_reason:
                reason = f"beyond 7Timer's 3-day forecast; {forecast.fallback_reason}"
            else:
                reason = "outside the forecast's range"
            night_forecast = unavailable(location, reason)
        else:
            night_forecast = usable

        score = score_night(window, night_forecast, session=session)

        clouds: list[float] = []
        temps: list[float] = []
        if usable is not None:
            for slot in score.slots:
                hour = usable.at(slot.time_utc)
                if hour is None:
                    continue
                if hour.cloud_cover is not None:
                    clouds.append(hour.cloud_cover)
                if hour.temperature_c is not None:
                    temps.append(hour.temperature_c)

        out.append(NightOutlook(
            date=day,
            session=session,
            deep_sky_peak=score.deep_sky_peak,
            deep_sky_mean=score.deep_sky_mean,
            planetary_peak=score.planetary_peak,
            planetary_mean=score.planetary_mean,
            best_window=score.best_window,
            best_window_score=score.best_window_score,
            is_gradeable=score.is_gradeable,
            weather_available=score.weather_available,
            weather_note=score.weather_note,
            seeing_estimated=score.seeing_estimated,
            limiting_factor=limiting_factor(score) if score.weather_available else None,
            cloud_mean_pct=_mean(clouds),
            temperature_low_c=min(temps) if temps else None,
            moon_illumination=window.moon_illumination,
            moon_waxing=window.moon_waxing,
            dark_hours=dark_overlap_hours(window, session),
        ))
    return out
