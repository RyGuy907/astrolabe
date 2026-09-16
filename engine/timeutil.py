"""Timezone discipline for the engine.

The rule, enforced here rather than by convention:

* Everything internal is a timezone-aware ``datetime`` in UTC.
* Naive datetimes are a hard error, never silently localized.
* Conversion to local time happens exactly once, at the display layer, via
  `to_local`. Values that have been converted carry a ``_local`` name suffix so
  the boundary stays greppable.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc

# Local clock hour that splits "the night ahead" from "the night in progress".
# PLAN.md 3.1: before local noon you are mid-session, planning nothing new.
NIGHT_ROLLOVER_HOUR = 12


class NaiveDatetimeError(ValueError):
    """Raised when a datetime without tzinfo reaches an engine boundary."""


def ensure_utc(value: datetime, *, field: str = "datetime") -> datetime:
    """Return `value` as tz-aware UTC, rejecting naive datetimes.

    Call this on every datetime entering an engine function. Aware datetimes in
    any zone are converted; naive ones raise rather than being assumed to be
    UTC or local, because both assumptions are wrong roughly half the time.
    """
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be a datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise NaiveDatetimeError(
            f"{field} is naive ({value!r}); engine boundaries require tz-aware "
            "datetimes. Attach a timezone (UTC internally) before calling."
        )
    return value.astimezone(UTC)


def is_aware_utc(value: datetime) -> bool:
    """True if `value` is tz-aware and its offset is exactly UTC."""
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() == timedelta(0)
    )


def to_local(value: datetime, tz: str | ZoneInfo) -> datetime:
    """UTC -> local. The only sanctioned conversion; display layer only."""
    aware = ensure_utc(value)
    zone = ZoneInfo(tz) if isinstance(tz, str) else tz
    return aware.astimezone(zone)


def local_noon_utc(day: date, tz: str | ZoneInfo) -> datetime:
    """12:00 local on `day`, as UTC.

    Used as the anchor for rise/set searches: anchoring on local noon rather
    than midnight keeps a single night's events inside one search span.
    """
    zone = ZoneInfo(tz) if isinstance(tz, str) else tz
    return datetime(day.year, day.month, day.day, 12, 0, tzinfo=zone).astimezone(UTC)


def local_midnight_utc(day: date, tz: str | ZoneInfo) -> datetime:
    """00:00 local on `day`, as UTC.

    The anchor for calendar-day almanac events (moonrise/moonset as published
    by USNO and timeanddate), which are reported per local calendar day rather
    than per observing night.
    """
    zone = ZoneInfo(tz) if isinstance(tz, str) else tz
    return datetime(day.year, day.month, day.day, 0, 0, tzinfo=zone).astimezone(UTC)


def resolve_night_date(now: datetime, tz: str | ZoneInfo) -> date:
    """Which night is "tonight", per PLAN.md 3.1.

    Before local noon, the night in progress started yesterday. From local noon
    onward, tonight is the upcoming night of today.

    `now` must be tz-aware; it is interpreted in `tz` after conversion.
    """
    zone = ZoneInfo(tz) if isinstance(tz, str) else tz
    local = ensure_utc(now, field="now").astimezone(zone)
    if local.hour < NIGHT_ROLLOVER_HOUR:
        return local.date() - timedelta(days=1)
    return local.date()


def now_utc() -> datetime:
    """Current time as tz-aware UTC."""
    return datetime.now(UTC)


def format_utc(value: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Render a UTC datetime for display, or "--" when absent."""
    if value is None:
        return "--"
    return ensure_utc(value).strftime(fmt)


def format_local(value: datetime | None, tz: str | ZoneInfo,
                 fmt: str = "%H:%M") -> str:
    """Render a datetime in local time for display, or "--" when absent."""
    if value is None:
        return "--"
    return to_local(value, tz).strftime(fmt)
