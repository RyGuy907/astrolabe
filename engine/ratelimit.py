"""Request budgets and backoff for the services the engine calls.

A public deployment answers strangers, and every forecast, place search and
terrain lookup it cannot answer from cache is a request from the server's own
address to a free service -- Open-Meteo allows about 600 a minute and 10,000 a
day per address before it blocks it, and 7Timer is a small volunteer-run
service with no published limit at all. Nothing upstream should hear about a
flood aimed at this app. So each service gets:

* **A budget** well inside its published limit: a count per minute and per
  day, shared by every caller in the process. Over budget, the call is not
  made; the caller degrades exactly as it would offline.
* **Backoff.** A 429 or 503 means "slow down": the service is left alone for
  as long as it asked (Retry-After), or ten minutes. Three failures in a row
  of any other kind -- timeouts, refused connections, 5xx -- mean it is
  struggling, and it gets five minutes' peace. Without this, a service that
  is down or refusing got asked again on every page load, which is exactly
  when it should not be.

This module only keeps the books; `weather.py` and `geocode.py` make the
calls and report how they went. It opens no connections itself, so the rule
that only those two modules reach the network still holds.

`SlidingWindow` is the same counting, keyed, for the API's per-visitor limit.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable

MINUTE = 60.0
DAY = 86400.0

#: How long a service is left alone after it says "slow down" without saying
#: for how long, and the bounds on how long it may ask for.
DEFAULT_PAUSE_S = 600.0
MIN_PAUSE_S, MAX_PAUSE_S = 60.0, 3600.0
#: Failures of other kinds in a row before a service is rested, and for how long.
FAILURES_BEFORE_PAUSE = 3
FAILURE_PAUSE_S = 300.0


class RateLimited(RuntimeError):
    """Raised internally when a call is not made: over budget, or backing off.
    Never escapes the networked modules -- they degrade as if offline."""


class Upstream:
    """One external service's budget and health. Thread-safe: the API runs
    endpoints on a thread pool, and they share one budget per service."""

    def __init__(self, name: str, *, per_minute: int, per_day: int,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.name = name
        self.per_minute = per_minute
        self.per_day = per_day
        self._clock = clock
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._calls: deque[tuple[float, int]] = deque()
            self._paused_until = 0.0
            self._pause_note: str | None = None
            self._failures = 0

    # --- before a call ------------------------------------------------------

    def acquire(self, cost: int = 1) -> bool:
        """Spend `cost` from the budget if it is there and the service is not
        being rested. False means: do not make the call."""
        with self._lock:
            now = self._clock()
            if now < self._paused_until:
                return False
            self._forget(now)
            last_minute = sum(c for t, c in self._calls if now - t < MINUTE)
            today = sum(c for _, c in self._calls)
            if last_minute + cost > self.per_minute or today + cost > self.per_day:
                return False
            self._calls.append((now, cost))
            return True

    def _forget(self, now: float) -> None:
        while self._calls and now - self._calls[0][0] >= DAY:
            self._calls.popleft()

    # --- after a call -------------------------------------------------------

    def succeeded(self) -> None:
        with self._lock:
            self._failures = 0

    def failed(self, *, status: int | None = None,
               retry_after: str | None = None) -> None:
        """Report a failed call. `status` is the HTTP status if there was a
        response; `retry_after` its Retry-After header, if any."""
        with self._lock:
            now = self._clock()
            if status in (429, 503):
                pause = _seconds(retry_after)
                pause = DEFAULT_PAUSE_S if pause is None else min(max(pause, MIN_PAUSE_S), MAX_PAUSE_S)
                self._pause(now, pause, f"{self.name} asked for fewer requests (HTTP {status})")
                return
            if status is not None and 400 <= status < 500:
                return      # a bad request is ours to fix, not the service's health
            self._failures += 1
            if self._failures >= FAILURES_BEFORE_PAUSE:
                self._pause(now, FAILURE_PAUSE_S,
                            f"{self.name} failed {self._failures} times in a row")

    def _pause(self, now: float, seconds: float, note: str) -> None:
        self._paused_until = now + seconds
        self._pause_note = note
        self._failures = 0

    # --- for saying why -----------------------------------------------------

    def unavailable_reason(self) -> str | None:
        """Why the last call was not made, if it was this module's doing; None
        when the service is available as far as the budget goes."""
        with self._lock:
            now = self._clock()
            if now < self._paused_until:
                minutes = max(1, round((self._paused_until - now) / 60))
                return f"{self._pause_note}; trying again in about {minutes} min"
            self._forget(now)
            if sum(c for t, c in self._calls if now - t < MINUTE) >= self.per_minute:
                return f"too many {self.name} requests this minute; try again shortly"
            if sum(c for _, c in self._calls) >= self.per_day:
                return f"today's {self.name} request budget is spent; try again tomorrow"
            return None


def _seconds(retry_after: str | None) -> float | None:
    """Retry-After as seconds. The HTTP-date form is rare from APIs and is
    treated as absent, which falls back to the default pause."""
    try:
        return float(retry_after) if retry_after is not None else None
    except ValueError:
        return None


# --- the services -----------------------------------------------------------
#
# Open-Meteo's free tier: 600/min, 5,000/h, 10,000/day per address, and a
# request for more than 10 variables or 14 days counts as more than one. Its
# forecast, geocoding and elevation endpoints share one allowance, so they
# share one budget here, at a tenth of it. 7Timer publishes nothing; a
# forecast is cached for three hours per ~1 km, so 300 a day is 100 sites.

OPEN_METEO = Upstream("Open-Meteo", per_minute=60, per_day=1000)
SEVENTIMER = Upstream("7Timer", per_minute=10, per_day=300)

UPSTREAMS = (OPEN_METEO, SEVENTIMER)


def reset_all() -> None:
    """Every budget and pause back to fresh. For tests."""
    for upstream in UPSTREAMS:
        upstream.reset()


class SlidingWindow:
    """At most `limit` events per `window` seconds for each key.

    For a per-visitor limit: the keys are addresses, and a flood from
    many addresses must not grow the table without bound, so idle keys are
    swept once it passes `max_keys`.
    """

    def __init__(self, limit: int, window: float = MINUTE, *, max_keys: int = 10_000,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self.window = window
        self.max_keys = max_keys
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}

    def hit(self, key: str) -> float | None:
        """Record an event for `key`. None if allowed; otherwise the seconds
        until it would be (and the event is not recorded)."""
        with self._lock:
            now = self._clock()
            hits = self._hits.get(key)
            if hits is None:
                if len(self._hits) >= self.max_keys:
                    self._sweep(now)
                hits = self._hits[key] = deque()
            while hits and now - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(1.0, self.window - (now - hits[0]))
            hits.append(now)
            return None

    def _sweep(self, now: float) -> None:
        idle = [k for k, h in self._hits.items() if not h or now - h[-1] >= self.window]
        for key in idle:
            del self._hits[key]
        if len(self._hits) >= self.max_keys:      # all active: a flood; start over
            self._hits.clear()

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
