"""The hours you actually plan to be outside.

Everything here used to be computed across the whole astronomical night, or
across the whole true-dark window. That answers "what is this night like",
which is not the question anyone asks. Nobody observes from dusk to dawn on a
Tuesday. They go out after dinner and come in at one in the morning, and what
they want to know is what is up, how cold it will be and whether it will be
cloudy *during those hours* -- not averaged over six hours of which they will
see two.

So a session is an explicit interval, and the target list, the condition
scores, the temperature and cloud summaries and the visible/late split are all
computed over it.

The default
-----------
Astronomical dusk to 01:00 local. Dusk because there is no point starting
before the sky is dark, and 01:00 because that is roughly when a weeknight
observer gives up. Both ends are clipped to the night: a session cannot start
before the sun is down or run past astronomical dawn, since the engine has
nothing to say about a daylit sky.

Why not sunset to sunrise
-------------------------
Because it made the numbers dishonest in a specific way. A night that is clear
until midnight and overcast afterwards averaged out to "partly cloudy" over a
dusk-to-dawn window, and an object that rises at 04:00 was listed as tonight's
target. Both are true of the night and useless to somebody deciding whether to
set up.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from .ephem import Interval, NightWindow
from .timeutil import ensure_utc

try:                                            # Python 3.9+
    from zoneinfo import ZoneInfo
except ImportError:                             # pragma: no cover
    from backports.zoneinfo import ZoneInfo     # type: ignore

#: When a default session ends, local time. Not midnight: the hours either side
#: of it are some of the best of the night, and cutting at 00:00 would drop
#: them for the sake of a round number.
DEFAULT_END_HOUR = 1

#: A session shorter than this is not worth reporting on -- it usually means
#: the clipping collapsed the interval, at a latitude or date where the night
#: barely exists.
MIN_USEFUL_SESSION = timedelta(minutes=30)


def _local_time_utc(day: date, hour: int, tz: str) -> datetime:
    """`hour`:00 local on `day`, in UTC."""
    zone = ZoneInfo(tz)
    return datetime.combine(day, time(hour=hour), tzinfo=zone).astimezone(
        ZoneInfo("UTC"))


def night_bounds(window: NightWindow) -> Interval | None:
    """The outer limits a session may occupy: sunset to sunrise.

    Astronomical night would be the tighter answer, but an observer setting up
    in late twilight is doing something real, and the scoring already has a
    moon factor and a transparency factor to describe how good it is not.
    """
    if window.sunset_utc is None or window.sunrise_utc is None:
        # Polar day or polar night: fall back to whatever darkness exists.
        if window.astronomical_night:
            return (window.astronomical_night[0][0],
                    window.astronomical_night[-1][1])
        return None
    return (window.sunset_utc, window.sunrise_utc)


def default_session(window: NightWindow) -> Interval | None:
    """Astronomical dusk to 01:00 local, clipped to the night.

    Returns None when there is no usable night at all -- polar summer, where
    the honest answer is that there are no observing hours rather than a
    fabricated interval.
    """
    bounds = night_bounds(window)
    if bounds is None:
        return None
    earliest, latest = bounds

    start = window.astronomical_dusk_utc or earliest
    start = max(start, earliest)

    # 01:00 on the morning *after* the night's date. A night is named for the
    # evening it starts on, so its 01:00 belongs to the following day.
    end = _local_time_utc(window.date + timedelta(days=1),
                          DEFAULT_END_HOUR, window.location.tz)
    end = min(end, latest)

    if end <= start:
        # Dusk falls after 01:00 -- a short summer night at a high latitude.
        # Use what darkness there is rather than returning an inverted span.
        end = latest
    if end - start < MIN_USEFUL_SESSION:
        return (earliest, latest) if latest > earliest else None
    return (start, end)


def resolve_session(window: NightWindow,
                    start: datetime | None = None,
                    end: datetime | None = None) -> Interval | None:
    """The session to compute over: what was asked for, or the default.

    Either end may be given on its own; the other falls back to the default's.
    Both are clipped to the night, so a caller cannot ask for targets at noon
    and be told about them.
    """
    fallback = default_session(window)
    if start is None and end is None:
        return fallback
    if fallback is None:
        return None

    bounds = night_bounds(window)
    if bounds is None:
        return None
    earliest, latest = bounds

    chosen_start = ensure_utc(start, field="session start") if start else fallback[0]
    chosen_end = ensure_utc(end, field="session end") if end else fallback[1]

    chosen_start = min(max(chosen_start, earliest), latest)
    chosen_end = min(max(chosen_end, earliest), latest)
    if chosen_end <= chosen_start:
        return fallback
    return (chosen_start, chosen_end)


def session_hours(session: Interval | None) -> float:
    """Length in hours, or 0.0 for no session."""
    if session is None:
        return 0.0
    return (session[1] - session[0]).total_seconds() / 3600.0


def dark_overlap_hours(window: NightWindow, session: Interval | None) -> float:
    """How much of the session is true dark -- astronomical night, moon down.

    Reported rather than enforced. An observer who wants to be out from dusk
    with the Moon up is not making a mistake, and the scoring already says
    what the Moon costs; this just makes the trade visible.
    """
    if session is None:
        return 0.0
    start, end = session
    total = timedelta()
    for interval_start, interval_end in window.dark_intervals:
        overlap_start = max(start, interval_start)
        overlap_end = min(end, interval_end)
        if overlap_end > overlap_start:
            total += overlap_end - overlap_start
    return total.total_seconds() / 3600.0
