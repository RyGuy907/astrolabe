"""Shared fixtures, and the guard that stops a skipped suite looking green.

**Why the strict-mode machinery below exists.** Two things in this suite skip
themselves when their prerequisites are missing: tests needing the DE440s
ephemeris, which is a ~32 MB download rather than a committed file, and the
API tests, which need the optional `[dev]` extras. On a developer's machine
that is a kindness. On a fresh clone it is a trap — the suite reported

    255 passed, 145 skipped        exit code 0

with a further 42 API tests collapsed into a single skip line by a
module-level `importorskip`. A third of the suite silently did not run and the
exit code still said everything was fine.

So: skipping stays the default for local convenience, but

* `ASTRO_REQUIRE_EPHEMERIS=1` turns a missing ephemeris into a hard failure,
  which is what CI sets once it has restored the cache; and
* every run ends with an explicit summary of what was skipped and why, so a
  green result can never quietly mean "most of it did not execute".
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from engine.ephem import ephemeris_is_cached, night_window  # noqa: E402
from engine.horizon import preset  # noqa: E402
from engine.locations import Location, get_location  # noqa: E402

REFERENCE_DATE = date(2026, 9, 15)


def strict_mode() -> bool:
    """True when the environment insists every test actually runs."""
    return os.environ.get("ASTRO_REQUIRE_EPHEMERIS", "").lower() in {"1", "true", "yes"}


_EPHEMERIS_CACHED = ephemeris_is_cached()

if strict_mode() and not _EPHEMERIS_CACHED:
    # Fail at collection rather than skipping 145 tests and exiting 0.
    raise RuntimeError(
        "ASTRO_REQUIRE_EPHEMERIS is set but DE440s is not in the cache, so the "
        "ephemeris-dependent tests would silently skip. Populate the cache "
        "first (`planner night`) or unset the variable."
    )

requires_ephemeris = pytest.mark.skipif(
    not _EPHEMERIS_CACHED,
    reason="DE440s not in the cache; run `planner night` once to populate it",
)


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Say plainly what did not run, whatever the exit code."""
    skipped = terminalreporter.stats.get("skipped", [])
    if not skipped:
        return

    reasons: dict[str, int] = {}
    for report in skipped:
        reason = ""
        if isinstance(getattr(report, "longrepr", None), tuple) and len(report.longrepr) == 3:
            reason = str(report.longrepr[2])
        reason = reason.replace("Skipped: ", "").strip() or "unspecified"
        reasons[reason] = reasons.get(reason, 0) + 1

    # ASCII only: this lands in CI logs and Windows consoles, where a
    # non-cp1252 character renders as a replacement glyph.
    terminalreporter.write_sep("=", "SKIPPED - these tests did not run",
                               yellow=True)
    for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
        terminalreporter.write_line(f"  {count:>4}  {reason}")
    terminalreporter.write_line("")
    terminalreporter.write_line(
        "  A pass count above is not a full run. Set ASTRO_REQUIRE_EPHEMERIS=1 "
        "(with the cache populated) to make ephemeris skips fail instead."
    )


#: A fixed, synthetic observing site: suburban Southern California, Bortle 5.
#:
#: Not a real place and not read from `config/locations.yaml`. Tests that
#: assert astronomy -- which objects clear the filters, what the limiting
#: magnitude is, how the novelty bonus behaves -- need a *stable sky*, and the
#: shipped config is an editable example that anyone may change.
#:
#: This was not hypothetical. When the seeded default moved to Griffith
#: Observatory (Bortle 8, inner Los Angeles) twelve tests failed at once,
#: including Phase 1's acceptance check that M31 ranks highly. Nothing was
#: wrong: from a Bortle 8 sky M31's surface brightness genuinely fails the
#: contrast test. But a change to an example config should not be able to
#: invalidate the suite's astronomical expectations, so those expectations now
#: hang off this instead.
TEST_SITE = Location(
    key="test_site",
    name="Test Site (suburban, Bortle 5)",
    lat=34.0,
    lon=-118.5,
    elevation_m=300.0,
    bortle=5,
    tz="America/Los_Angeles",
    horizon=preset("hilly"),
)


@pytest.fixture(scope="session")
def home():
    """The standard observing site for astronomy assertions.

    Named `home` for historical reasons and to keep the nine test modules
    that use it unchanged; it is `TEST_SITE`, not the configured default.
    Tests that genuinely check config wiring call `get_location("home")`
    directly instead.
    """
    return TEST_SITE


@pytest.fixture(scope="session")
def reference_night(home):
    return night_window(REFERENCE_DATE, home)
