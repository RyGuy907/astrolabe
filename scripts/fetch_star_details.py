"""Build `star_details.csv`: measured and modern data for the star cards.

Development-time only, like `fetch_star_chart_data.py`: it reads the
network, and the engine only ever reads the file it writes.

**Why.** The star cards first derived everything from HYG's four numbers
(Hipparcos distance, spectral type, B-V, absolute magnitude). An audit
(`scripts/audit_star_cards.py`) showed where that runs out: distances past a
few hundred parsecs are rough, sizes estimated from brightness and type are
good to only a quarter or so, and dust, variability and binaries throw
individual stars (Antares, a third too small) much further. Two sources do
better where they reach:

* **Gaia DR3** (ESA/Gaia/DPAC): parallaxes ten to a hundred times better
  than Hipparcos, with a quality flag (RUWE) that says when the astrometric
  fit is trustworthy; spectroscopic temperatures (GSP-Spec) for many
  brighter stars; and FLAME radii and luminosities. Joined on the Hipparcos
  number through Gaia's own `hipparcos2_best_neighbour` cross-match. Gaia
  saturates on the brightest stars (G < ~3), where RUWE flags the result.
* **JMDC** (JMMC Measured stellar Diameters Catalogue, Duvert 2016, VizieR
  II/345): 2,000 angular diameters of ~1,000 stars, all by direct methods
  -- optical and intensity interferometry, lunar occultation. With a
  distance, an angular diameter *is* a radius, no model of the star needed.
  Matched to the chart's stars by position (within 20"), since its
  identifiers mix HD numbers, double-star names and Bayer letters.
  Measurements noted as blended with a binary companion are left out.

Output `engine/catalog/data/star_details.csv`, one row per star with any of:
`hip;plx;plx_err;ruwe;teff_spec;radius_flame;lum_flame;diam_mas;diam_n`
(parallax and error in mas; diameter the limb-darkened angular diameter in
mas and the number of measurements behind it).

Usage:
    python scripts/fetch_star_details.py
"""

from __future__ import annotations

import csv
import http.client
import io
import math
import re
import statistics
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "engine" / "catalog" / "data"
GAIA_TAP = "https://gea.esac.esa.int/tap-server/tap/sync"
JMDC = ("https://vizier.cds.unistra.fr/viz-bin/asu-tsv?-source=II/345/jmdc"
        "&-out.max=unlimited&-out=UDdiam,LDdiam,e_LDdiam,BibCode,Notes&-out.add=_RA,_DE")
BATCH = 500
# Finished Gaia batches, so an interrupted run resumes instead of starting over.
CACHE = Path(__import__("tempfile").gettempdir()) / "astrolabe-gaia-batches"


def stars() -> list[dict]:
    with open(DATA / "stars.csv", encoding="utf-8") as fh:
        return list(csv.DictReader(fh, delimiter=";"))


def gaia(hips: list[int]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for start in range(0, len(hips), BATCH):
        chunk = hips[start:start + BATCH]
        query = f"""
            SELECT h.original_ext_source_id AS hip, g.parallax, g.parallax_error,
                   g.ruwe, a.teff_gspspec, a.radius_flame, a.lum_flame
            FROM gaiadr3.hipparcos2_best_neighbour AS h
            JOIN gaiadr3.gaia_source AS g ON g.source_id = h.source_id
            LEFT JOIN gaiadr3.astrophysical_parameters AS a ON a.source_id = h.source_id
            WHERE h.original_ext_source_id IN ({",".join(map(str, chunk))})"""
        cached = CACHE / f"{chunk[0]}-{chunk[-1]}-{len(chunk)}.csv"
        if cached.exists():
            text = cached.read_text(encoding="utf-8")
        else:
            body = urllib.parse.urlencode({"REQUEST": "doQuery", "LANG": "ADQL",
                                           "FORMAT": "csv", "QUERY": query}).encode()
            text = None
            for attempt in range(5):
                try:
                    with urllib.request.urlopen(GAIA_TAP, data=body, timeout=300) as resp:
                        text = resp.read().decode()
                    break
                except (OSError, http.client.HTTPException) as error:
                    wait = 10 * 2 ** attempt
                    print(f"  gaia: {error!r}; retrying in {wait}s", file=sys.stderr)
                    time.sleep(wait)
            if text is None:
                raise RuntimeError("Gaia archive unreachable after 5 attempts; rerun to resume")
            CACHE.mkdir(exist_ok=True)
            cached.write_text(text, encoding="utf-8")
        for row in csv.DictReader(io.StringIO(text)):
            out[int(row["hip"])] = row
        print(f"  gaia: {min(start + BATCH, len(hips))}/{len(hips)}", file=sys.stderr)
    return out


def jmdc() -> list[tuple[float, float, float, int]]:
    """(ra, dec, limb-darkened diameter mas, measurements) per star measured."""
    with urllib.request.urlopen(JMDC, timeout=300) as resp:
        lines = [l for l in resp.read().decode().splitlines()
                 if l.strip() and not l.startswith("#")]
    header = [h.strip() for h in lines[0].split("\t")]
    by_star: dict[tuple[float, float], list[tuple[float | None, float | None, bool]]] = {}
    for line in lines[3:]:                      # after the header, units, dashes
        row = dict(zip(header, (c.strip() for c in line.split("\t"))))
        try:
            ra, dec = float(row["_RA"]), float(row["_DE"])
        except (KeyError, ValueError):
            continue
        # A measurement its authors note as blended with a companion is the
        # pair's light, not the star's disc (Capella's one entry: "Capella
        # is binary", 6 mas where the primary alone is ~8.5).
        if re.search(r"binary|companion", row.get("Notes", ""), re.I):
            continue
        ld = float(row["LDdiam"]) if row.get("LDdiam") else None
        ud = float(row["UDdiam"]) if row.get("UDdiam") else None
        has_err = bool(row.get("e_LDdiam"))
        by_star.setdefault((round(ra, 4), round(dec, 4)), []).append((ld, ud, has_err))
    out = []
    for (ra, dec), meas in by_star.items():
        # Best first: limb-darkened values with a published error; then any
        # limb-darkened; then uniform-disk, which reads a few per cent small.
        for pick in ([m[0] for m in meas if m[0] and m[2]],
                     [m[0] for m in meas if m[0]],
                     [m[1] * 1.04 for m in meas if m[1]]):
            if pick:
                out.append((ra, dec, statistics.median(pick), len(pick)))
                break
    return out


def main() -> int:
    rows = stars()
    hips = sorted({int(r["hip"]) for r in rows if r["hip"]})
    print(f"stars.csv: {len(rows)} stars, {len(hips)} with a Hipparcos number", file=sys.stderr)
    g = gaia(hips)

    # Match JMDC by position: nearest chart star within 20", brightest on a tie.
    measured = jmdc()
    cell: dict[tuple[int, int], list[int]] = {}
    for i, r in enumerate(rows):
        cell.setdefault((int(float(r["ra"])), int(float(r["dec"]))), []).append(i)
    diam: dict[int, tuple[float, int]] = {}
    for ra, dec, d, n in measured:
        best, best_sep = None, 20 / 3600
        for dra in (-1, 0, 1):
            for ddec in (-1, 0, 1):
                for i in cell.get((int(ra) + dra, int(dec) + ddec), []):
                    r = rows[i]
                    sep = math.hypot((float(r["ra"]) - ra) * math.cos(math.radians(dec)),
                                     float(r["dec"]) - dec)
                    if sep < best_sep:
                        best, best_sep = i, sep
        if best is not None and rows[best]["hip"]:
            diam[int(rows[best]["hip"])] = (d, n)

    out = []
    for hip in hips:
        gr, dm = g.get(hip), diam.get(hip)
        if not gr and not dm:
            continue
        f = lambda k, nd: (f"{float(gr[k]):.{nd}f}" if gr and gr.get(k) not in (None, "") else "")
        out.append([hip, f("parallax", 4), f("parallax_error", 4), f("ruwe", 3),
                    f("teff_gspspec", 0), f("radius_flame", 3), f("lum_flame", 3),
                    f"{dm[0]:.3f}" if dm else "", dm[1] if dm else ""])
    with open(DATA / "star_details.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["hip", "plx", "plx_err", "ruwe", "teff_spec", "radius_flame",
                    "lum_flame", "diam_mas", "diam_n"])
        w.writerows(out)
    count = lambda col: sum(1 for r in out if r[col] != "")
    print(f"star_details.csv: {len(out)} stars; Gaia parallax {count(1)}, "
          f"spectroscopic Teff {count(4)}, FLAME radius {count(5)}, "
          f"measured diameter {count(7)} (of {len(measured)} JMDC stars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
