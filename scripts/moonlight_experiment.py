"""Compare the flat moonlight penalty with Krisciunas & Schaefer, across a lunation.

Development-time only; the engine never imports it. For each night of a
lunar cycle and three sky qualities, runs target assessment under both moon
models (engine/targets.py `moon_model`) and reports how many objects each
calls visible, and which showpieces change verdict.

    .venv/Scripts/python.exe scripts/moonlight_experiment.py [--start 2026-10-01] [--days 30]
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.catalog.loader import load_catalog  # noqa: E402
from engine.ephem import night_window  # noqa: E402
from engine.equipment import load_equipment  # noqa: E402
from engine.locations import BORTLE_SQM, Location  # noqa: E402
from engine.session import resolve_session  # noqa: E402
from engine.showpieces import showpiece_ids  # noqa: E402
from engine.targets import assess_targets  # noqa: E402

#: Northern Utah, at three skies: a dark-site, a rural and a suburban reading.
SITES = [
    ("dark (B2, 21.8)", 21.8),
    ("rural (B4, 20.9)", BORTLE_SQM[4]),
    ("suburban (B6, 19.4)", BORTLE_SQM[6]),
]
LAT, LON, ELEV, TZ = 40.48, -111.64, 1500.0, "America/Denver"


def site(label: str, sqm: float) -> Location:
    # Given as atlas_sqm so `location.sqm` is exactly this value.
    return Location(key=label.split()[0], name=label, lat=LAT, lon=LON,
                    elevation_m=ELEV, tz=TZ, bortle=None, atlas_sqm=sqm)


def verdicts(window, location_session, equipment, model):
    results = assess_targets(window, equipment, session=location_session,
                             min_altitude_deg=0.0, include_too_faint=True,
                             moon_model=model)
    return {a.obj.name: a for a in results}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-10-01")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--every", type=int, default=3)
    args = parser.parse_args()

    equipment = load_equipment()
    showpieces = showpiece_ids(load_catalog())
    start = date.fromisoformat(args.start)
    flips: Counter[tuple[str, str]] = Counter()

    print(f"{'night':<11} {'moon':>5}  {'site':<20} {'flat vis':>8} {'ks vis':>7}"
          f" {'showpieces flat/ks':>19}  changed showpieces")
    for offset in range(0, args.days, args.every):
        night = start + timedelta(days=offset)
        for label, sqm in SITES:
            location = site(label, sqm)
            window = night_window(night, location)
            session = resolve_session(window)
            flat = verdicts(window, session, equipment, "flat")
            ks = verdicts(window, session, equipment, "ks")
            names = set(flat) | set(ks)
            flat_ok = {n for n in names if n in flat and not flat[n].too_faint}
            ks_ok = {n for n in names if n in ks and not ks[n].too_faint}
            gained = sorted((ks_ok - flat_ok) & showpieces)
            lost = sorted((flat_ok - ks_ok) & showpieces)
            for n in gained:
                flips[(n, "gained")] += 1
            for n in lost:
                flips[(n, "lost")] += 1
            change = " ".join([f"+{n}" for n in gained] + [f"-{n}" for n in lost])
            print(f"{night.isoformat():<11} {window.moon_illumination:5.0%}  {label:<20}"
                  f" {len(flat_ok):8d} {len(ks_ok):7d}"
                  f" {len(flat_ok & showpieces):>9}/{len(ks_ok & showpieces):<9}"
                  f"  {change[:110]}")
    print()
    print("Showpieces that change most often (+ visible under K&S only, - flat only):")
    for (name, way), count in flips.most_common(25):
        print(f"  {'+' if way == 'gained' else '-'}{name:<10} {count}")


if __name__ == "__main__":
    main()
