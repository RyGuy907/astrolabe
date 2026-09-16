"""Shared fixtures. Puts the repo root on sys.path so `engine` imports work."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from engine.ephem import ephemeris_is_cached, night_window  # noqa: E402
from engine.locations import get_location  # noqa: E402

REFERENCE_DATE = date(2026, 9, 15)

requires_ephemeris = pytest.mark.skipif(
    not ephemeris_is_cached(),
    reason="DE440s not in the cache; run `planner night` once to populate it",
)


@pytest.fixture(scope="session")
def home():
    return get_location("home")


@pytest.fixture(scope="session")
def reference_night(home):
    """The night used for external cross-checks: 2026-09-15, Agoura Hills."""
    return night_window(REFERENCE_DATE, home)
