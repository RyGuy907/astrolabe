"""Build the finder-chart star data from d3-celestial.

Development-time only, like `fetch_double_stars.py`: it reads the network,
and the engine only ever reads the files it writes.

**What and why.** A finder chart needs the stars a finder scope shows --
down to about magnitude 8 -- plus the constellation stick figures that make
a patch of sky recognisable, and names for the bright stars you hop from.
d3-celestial (Olaf Frohn, BSD 3-clause) publishes exactly that, derived from
the HYG database (David Nash, CC BY-SA), itself built from Hipparcos, the
Yale Bright Star Catalogue and Gliese. The GeoJSON is trimmed to what a chart
draws:

* `engine/catalog/data/stars.csv` -- `ra;dec;mag;label` for 41,411 stars.
  `label` is the proper name where there is one, otherwise the Bayer letter
  with its constellation ("α Lyr"), otherwise empty.
* `engine/catalog/data/constellation_lines.json` -- each constellation's
  stick figure as a list of polylines of [ra, dec] in degrees.

d3-celestial stores longitude as -180..180; RA here is 0..360.

Usage:
    python scripts/fetch_star_chart_data.py
"""

from __future__ import annotations

import csv
import json
import sys
import urllib.request
from pathlib import Path

SOURCE = "https://raw.githubusercontent.com/ofrohn/d3-celestial/master/data/"
OUT = Path(__file__).resolve().parent.parent / "engine" / "catalog" / "data"


def fetch(name: str):
    with urllib.request.urlopen(SOURCE + name, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def ra_of(longitude: float) -> float:
    return round(longitude % 360.0, 4)


def main() -> int:
    stars = fetch("stars.8.json")["features"]
    names = fetch("starnames.json")
    lines = fetch("constellations.lines.json")["features"]

    rows = []
    for star in stars:
        lon, dec = star["geometry"]["coordinates"]
        entry = names.get(str(star["id"]), {})
        label = entry.get("name") or (
            f"{entry['bayer']} {entry['c']}" if entry.get("bayer") and entry.get("c")
            else "")
        rows.append((ra_of(lon), round(dec, 4), star["properties"]["mag"], label))
    rows.sort(key=lambda r: r[2])     # brightest first: the chart draws in order

    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "stars.csv", "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(["ra", "dec", "mag", "label"])
        writer.writerows(rows)

    figures = {
        feature["id"]: [[[ra_of(lon), round(dec, 4)] for lon, dec in polyline]
                        for polyline in feature["geometry"]["coordinates"]]
        for feature in lines
    }
    with open(OUT / "constellation_lines.json", "w", encoding="utf-8") as fh:
        json.dump(figures, fh, separators=(",", ":"), ensure_ascii=False)

    named = sum(1 for r in rows if r[3])
    print(f"stars.csv: {len(rows)} stars, {named} labelled")
    print(f"constellation_lines.json: {len(figures)} constellations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
