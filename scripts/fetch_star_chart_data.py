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

* `engine/catalog/data/stars.csv` -- `ra;dec;mag;label;hip;dist_pc;spect;ci;absmag`
  for 41,411 stars. `label` is the proper name where there is one, otherwise
  the Bayer letter with its constellation ("α Lyr"), otherwise empty. The
  last five, for the chart's star card, come from HYG itself, joined on the
  Hipparcos number (d3-celestial's star id): distance in parsecs, spectral
  type, B-V colour index and absolute visual magnitude. Empty where HYG has
  nothing -- or, for distance, where it has only its 100,000 pc "unknown".
  **Names follow the IAU.** d3-celestial's names include historical ones
  given to several stars at once (four Terebellums, three Propus), and a
  few spellings the IAU does not use. The IAU Working Group on Star Names
  now assigns each name to exactly one star, published with its Hipparcos
  number (the WGSN list, maintained by E. Mamajek). So an IAU name goes on
  its star alone, in the IAU spelling, and comes off any other star, which
  falls back to its Bayer letter; a traditional name the IAU has not
  adopted stays, on the brightest star carrying it only. An audit of the
  star cards against published data found the duplicates: its worst
  "misses" were the right numbers for the wrong star.
* `engine/catalog/data/constellation_lines.json` -- per constellation, its
  name, d3-celestial's rank (1 the most prominent 22, 2 the next 24, 3 the
  faint rest), a label position, and its stick figure as polylines of
  [ra, dec] in degrees. The rank is what lets the whole-sky overview show
  only the constellations people navigate by.

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
IAU_NAMES = "https://www.pas.rochester.edu/~emamajek/WGSN/IAU-CSN.txt"
HYG = ("https://raw.githubusercontent.com/astronexus/HYG-Database/main/"
       "hyg/CURRENT/hygdata_v41.csv")
OUT = Path(__file__).resolve().parent.parent / "engine" / "catalog" / "data"


def fetch(name: str):
    with urllib.request.urlopen(SOURCE + name, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_hyg() -> dict[int, tuple[str, str, str, str]]:
    """HIP number -> (distance pc, spectral type, B-V, absolute magnitude)."""
    import io
    with urllib.request.urlopen(HYG, timeout=600) as response:
        text = io.TextIOWrapper(response, encoding="utf-8")
        out = {}
        for row in csv.DictReader(text):
            if not row["hip"]:
                continue
            dist = row["dist"]
            # HYG's placeholder for "no usable parallax".
            if not dist or float(dist) >= 100000:
                dist = ""
            out[int(row["hip"])] = (dist, row["spect"].strip(), row["ci"], row["absmag"])
        return out


def fetch_iau_names() -> dict[int, str]:
    """HIP number -> IAU-approved proper name (WGSN), for stars that have one."""
    with urllib.request.urlopen(IAU_NAMES, timeout=120) as response:
        text = response.read().decode("utf-8")
    out = {}
    for line in text.splitlines():
        if not line.strip() or line.startswith(("#", "$")):
            continue
        fields = line.rstrip().rstrip("*").split()
        try:
            hip = int(fields[-5])        # ... Vmag band HIP HD RA Dec date
        except (ValueError, IndexError):
            continue                     # "_": no Hipparcos number (exoplanet hosts)
        out[hip] = line[18:36].strip()   # "Name/Diacritics": Bélénos, not Belenos
    return out


def ra_of(longitude: float) -> float:
    return round(longitude % 360.0, 4)


def main() -> int:
    stars = fetch("stars.8.json")["features"]
    names = fetch("starnames.json")
    lines = fetch("constellations.lines.json")["features"]
    meta = {f["id"]: f for f in fetch("constellations.json")["features"]}
    hyg = fetch_hyg()
    iau = fetch_iau_names()
    iau_names = set(iau.values())

    rows = []
    for star in stars:
        lon, dec = star["geometry"]["coordinates"]
        entry = names.get(str(star["id"]), {})
        bayer = (f"{entry['bayer']} {entry['c']}" if entry.get("bayer") and entry.get("c")
                 else "")
        hip = int(star["id"])
        if hip in iau:
            label = iau[hip]
        elif entry.get("name") and entry["name"] not in iau_names:
            label = entry["name"]
        else:
            label = bayer                # its name belongs to another star
        dist, spect, ci, absmag = hyg.get(hip, ("", "", "", ""))
        rows.append((ra_of(lon), round(dec, 4), star["properties"]["mag"], label,
                     hip if hip in hyg else "",
                     f"{float(dist):.2f}" if dist else "", spect,
                     f"{float(ci):.3f}" if ci else "",
                     f"{float(absmag):.2f}" if absmag and dist else ""))
    rows.sort(key=lambda r: r[2])     # brightest first: the chart draws in order
    # A traditional name on several stars stays on the brightest only.
    bayer_of = {}
    for star in stars:
        entry = names.get(str(star["id"]), {})
        if entry.get("bayer") and entry.get("c"):
            bayer_of[int(star["id"])] = f"{entry['bayer']} {entry['c']}"
    seen: set[str] = set()
    for k, row in enumerate(rows):
        label, hip = row[3], row[4]
        if label and label in seen and label not in iau_names:
            rows[k] = (*row[:3], bayer_of.get(hip, "") if hip else "", *row[4:])
        elif label:
            seen.add(label)

    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "stars.csv", "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(["ra", "dec", "mag", "label", "hip", "dist_pc", "spect", "ci", "absmag"])
        writer.writerows(rows)

    figures = {}
    for feature in lines:
        info = meta.get(feature["id"], {})
        props = info.get("properties", {})
        label = info.get("geometry", {}).get("coordinates")
        figures[feature["id"]] = {
            "name": props.get("name", feature["id"]),
            "rank": int(props.get("rank", feature["properties"].get("rank", 3))),
            "label": [ra_of(label[0]), round(label[1], 4)] if label else None,
            "lines": [[[ra_of(lon), round(dec, 4)] for lon, dec in polyline]
                      for polyline in feature["geometry"]["coordinates"]],
        }
    with open(OUT / "constellation_lines.json", "w", encoding="utf-8") as fh:
        json.dump(figures, fh, separators=(",", ":"), ensure_ascii=False)

    named = sum(1 for r in rows if r[3])
    placed = sum(1 for r in rows if r[4] and r[4] in iau and r[3] == iau[r[4]])
    print(f"IAU names: {placed} of {len(iau)} placed on their stars")
    joined = sum(1 for r in rows if r[4] != "")
    print(f"stars.csv: {len(rows)} stars, {named} labelled, {joined} matched in HYG")
    print(f"constellation_lines.json: {len(figures)} constellations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
