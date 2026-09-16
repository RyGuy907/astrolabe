"""The tz-aware-UTC invariant, asserted at every module boundary.

PLAN.md 7 lists naive datetimes as pitfall #1. These tests are the enforcement
mechanism: if a naive datetime can reach or leave an engine function, one of
them fails.
"""

from __future__ import annotations

import ast
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from conftest import REFERENCE_DATE, REPO_ROOT, requires_ephemeris
from engine.ephem import NightWindow, altaz_series
from engine.timeutil import (
    NaiveDatetimeError,
    ensure_utc,
    is_aware_utc,
    local_midnight_utc,
    local_noon_utc,
    resolve_night_date,
    to_local,
)

PDT = ZoneInfo("America/Los_Angeles")


# --- ensure_utc, the gate itself -------------------------------------------

def test_ensure_utc_rejects_naive():
    with pytest.raises(NaiveDatetimeError):
        ensure_utc(datetime(2026, 9, 15, 20, 0))


def test_ensure_utc_converts_other_zones():
    local = datetime(2026, 9, 15, 20, 25, tzinfo=PDT)
    result = ensure_utc(local)
    assert is_aware_utc(result)
    assert result == datetime(2026, 9, 16, 3, 25, tzinfo=timezone.utc)


def test_ensure_utc_rejects_non_datetime():
    with pytest.raises(TypeError):
        ensure_utc("2026-09-15T20:00:00Z")


def test_local_anchors_are_utc():
    assert is_aware_utc(local_noon_utc(REFERENCE_DATE, "America/Los_Angeles"))
    assert is_aware_utc(local_midnight_utc(REFERENCE_DATE, "America/Los_Angeles"))


# --- NightWindow boundary ---------------------------------------------------

@requires_ephemeris
def test_every_night_window_datetime_is_aware_utc(reference_night):
    for name in NightWindow._DATETIME_FIELDS:
        value = getattr(reference_night, name)
        if value is not None:
            assert is_aware_utc(value), f"{name} is not tz-aware UTC: {value!r}"


@requires_ephemeris
def test_every_interval_endpoint_is_aware_utc(reference_night):
    for label in ("astronomical_night", "dark_intervals"):
        for start, end in getattr(reference_night, label):
            assert is_aware_utc(start), f"{label} start not aware UTC: {start!r}"
            assert is_aware_utc(end), f"{label} end not aware UTC: {end!r}"


def test_night_window_rejects_naive_construction(home):
    """Constructing a NightWindow with a naive datetime raises, not coerces."""
    naive = datetime(2026, 9, 15, 20, 0)
    with pytest.raises(NaiveDatetimeError):
        NightWindow(
            date=REFERENCE_DATE, location=home,
            sunset_utc=naive, sunrise_utc=None,
            civil_dusk_utc=None, nautical_dusk_utc=None,
            astronomical_dusk_utc=None, astronomical_dawn_utc=None,
            nautical_dawn_utc=None, civil_dawn_utc=None,
            moonrise_utc=None, moonset_utc=None,
            moon_up_at_dusk=False, moon_waxing=True, moon_illumination=0.0,
            astronomical_night=[], dark_intervals=[],
        )


# --- altaz_series boundary --------------------------------------------------

@requires_ephemeris
def test_altaz_series_rejects_naive_start(home):
    with pytest.raises(NaiveDatetimeError):
        altaz_series(
            "moon", home,
            datetime(2026, 9, 15, 22, 0),
            datetime(2026, 9, 16, 2, 0, tzinfo=timezone.utc),
        )


@requires_ephemeris
def test_altaz_series_rejects_naive_end(home):
    with pytest.raises(NaiveDatetimeError):
        altaz_series(
            "moon", home,
            datetime(2026, 9, 15, 22, 0, tzinfo=timezone.utc),
            datetime(2026, 9, 16, 2, 0),
        )


@requires_ephemeris
def test_altaz_series_returns_aware_utc(home):
    start = datetime(2026, 9, 16, 4, 30, tzinfo=timezone.utc)
    series = altaz_series("moon", home, start, start + timedelta(hours=2),
                          timedelta(minutes=30))
    assert len(series) == 5
    assert all(is_aware_utc(t) for t in series.times_utc)


@requires_ephemeris
def test_altaz_series_accepts_local_input_and_normalizes(home):
    """Aware non-UTC input is accepted and comes back as UTC."""
    start_local = datetime(2026, 9, 15, 21, 30, tzinfo=PDT)
    series = altaz_series("moon", home, start_local,
                          start_local + timedelta(hours=1), timedelta(minutes=30))
    assert all(is_aware_utc(t) for t in series.times_utc)
    assert series.times_utc[0] == start_local.astimezone(timezone.utc)


@requires_ephemeris
def test_altaz_series_rejects_reversed_span(home):
    start = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        altaz_series("moon", home, start, start - timedelta(hours=1))


# --- "tonight" resolution, PLAN.md 3.1 -------------------------------------

@pytest.mark.parametrize(
    "local_clock, expected",
    [
        # After local noon: tonight is the night of today.
        (datetime(2026, 9, 15, 12, 1, tzinfo=PDT), date(2026, 9, 15)),
        (datetime(2026, 9, 15, 21, 0, tzinfo=PDT), date(2026, 9, 15)),
        (datetime(2026, 9, 15, 23, 59, tzinfo=PDT), date(2026, 9, 15)),
        # After midnight but before noon: still the night that began yesterday.
        (datetime(2026, 9, 16, 0, 1, tzinfo=PDT), date(2026, 9, 15)),
        (datetime(2026, 9, 16, 2, 0, tzinfo=PDT), date(2026, 9, 15)),
        (datetime(2026, 9, 16, 11, 59, tzinfo=PDT), date(2026, 9, 15)),
        # Noon exactly rolls over to the upcoming night.
        (datetime(2026, 9, 16, 12, 0, tzinfo=PDT), date(2026, 9, 16)),
    ],
)
def test_resolve_night_date(local_clock, expected):
    assert resolve_night_date(local_clock, "America/Los_Angeles") == expected


def test_resolve_night_date_rejects_naive():
    with pytest.raises(NaiveDatetimeError):
        resolve_night_date(datetime(2026, 9, 15, 21, 0), "America/Los_Angeles")


def test_resolve_night_date_uses_target_zone_not_input_zone():
    """22:00 UTC on the 15th is 15:00 PDT — still the night of the 15th."""
    utc_evening = datetime(2026, 9, 15, 22, 0, tzinfo=timezone.utc)
    assert resolve_night_date(utc_evening, "America/Los_Angeles") == date(2026, 9, 15)
    # ...but 06:00 UTC on the 16th is 23:00 PDT on the 15th, not the 16th.
    utc_late = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)
    assert resolve_night_date(utc_late, "America/Los_Angeles") == date(2026, 9, 15)


def test_resolve_night_date_across_dst_fall_back():
    """US DST ends 2026-11-01. 01:30 local is ambiguous but still pre-noon."""
    assert resolve_night_date(
        datetime(2026, 11, 1, 1, 30, tzinfo=PDT), "America/Los_Angeles"
    ) == date(2026, 10, 31)


def test_to_local_round_trips():
    original = datetime(2026, 9, 16, 3, 25, tzinfo=timezone.utc)
    local = to_local(original, "America/Los_Angeles")
    assert local.hour == 20 and local.minute == 25
    assert local.astimezone(timezone.utc) == original


# --- structural: no naive-datetime footguns or web imports in engine/ -------

ENGINE_FILES = sorted((REPO_ROOT / "engine").rglob("*.py"))

# Web *frameworks* are banned everywhere in engine/ without exception.
FORBIDDEN_IMPORTS = {
    "fastapi", "starlette", "uvicorn", "flask", "django",
    "requests", "httpx", "aiohttp", "urllib", "urllib3", "socket", "http",
}

# ...except for a short, explicit allowlist. PLAN.md 1's hard constraint is
# that the *astronomy* works with no network: ephemeris and catalogs are
# vendored. These two modules provide conveniences on top, and both degrade to
# an explicit "unavailable" rather than raising:
#
#   weather.py  - PLAN.md 1 designates it the networked part of the engine.
#   geocode.py  - place name -> coordinates when adding a site at runtime
#                 (PLAN.md 5). It lives in engine/ because PLAN.md 4 requires
#                 the API to stay a thin adapter and Location is an engine
#                 concept. No astronomy depends on it.
#
# The framework bans below still apply to both: they may open a socket, they
# may not import a web framework. Keep this list short — every addition is a
# claim that the engine still works offline.
NETWORK_ALLOWED = {"weather.py", "geocode.py"}
FRAMEWORK_IMPORTS = {"fastapi", "starlette", "uvicorn", "flask", "django"}


@pytest.mark.parametrize("path", ENGINE_FILES, ids=lambda p: p.name)
def test_engine_imports_nothing_web_related(path):
    """PLAN.md 4: keep engine/ free of web and direct-network imports.

    The ephemeris fetch goes through Skyfield's loader, which is the single
    sanctioned exception; engine code itself must not reach for a socket.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])

    banned = FRAMEWORK_IMPORTS if path.name in NETWORK_ALLOWED else FORBIDDEN_IMPORTS
    offenders = found & banned
    assert not offenders, f"{path.name} imports {sorted(offenders)}"


def test_only_weather_reaches_the_network():
    """The network exception must not spread beyond weather.py."""
    networked = set()
    for path in ENGINE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                found.add(node.module.split(".")[0])
        if found & {"urllib", "urllib3", "requests", "httpx", "aiohttp", "socket"}:
            networked.add(path.name)
    assert networked <= NETWORK_ALLOWED, (
        f"unexpected networked engine modules: {sorted(networked - NETWORK_ALLOWED)}"
    )


def test_the_astronomy_modules_are_all_offline():
    """The modules that actually compute astronomy must never touch a socket.

    This is PLAN.md 1's hard constraint stated directly: whatever the
    allowlist grows to, these files stay offline.
    """
    astronomy = {"ephem.py", "targets.py", "planets.py", "events.py",
                 "scoring.py", "equipment.py", "horizon.py", "timeutil.py",
                 "locations.py", "loader.py"}
    for path in ENGINE_FILES:
        if path.name not in astronomy:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = set()
            if isinstance(node, ast.Import):
                names = {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = {node.module.split(".")[0]}
            offenders = names & FORBIDDEN_IMPORTS
            assert not offenders, f"{path.name} imports {sorted(offenders)}"


@pytest.mark.parametrize("path", ENGINE_FILES, ids=lambda p: p.name)
def test_engine_never_calls_utcnow_or_naive_now(path):
    """`datetime.utcnow()` and bare `datetime.now()` both produce naive values."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr == "utcnow":
            pytest.fail(f"{path.name}:{node.lineno} calls utcnow(), which is naive")
        if node.func.attr == "now" and not node.args and not node.keywords:
            pytest.fail(f"{path.name}:{node.lineno} calls now() with no tz")


def test_only_timeutil_converts_to_local():
    """`to_local` is the one conversion point; engine must not call it.

    Engine modules may import timeutil freely, but a call to `to_local` inside
    engine/ means local time has leaked below the display boundary.
    """
    for path in ENGINE_FILES:
        if path.name == "timeutil.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name in {"to_local", "format_local"}:
                    pytest.fail(
                        f"{path.name}:{node.lineno} calls {name}(); local-time "
                        "conversion belongs in the display layer"
                    )
