"""Build `engine/catalog/data/double_stars.csv` from SIMBAD.

**Why this is a script and not part of the engine.** The famous visual doubles
are stars, and OpenNGC is a deep-sky catalogue: it has 244 entries typed `**`,
none of them named, almost all faint NGC pairs nobody points a telescope at.
Albireo, Mizar, Almach and Cor Caroli are simply not in it. So they have to
come from somewhere else, and the somewhere else is SIMBAD.

Run at development time, never at runtime. `engine/` reaches the network from
exactly two modules and this is not one of them -- the output is a vendored
CSV alongside the OpenNGC ones, and the engine only ever reads that file.

**Why a separate CSV rather than adding rows to `addendum.csv`.** That file
ships with OpenNGC and is theirs; mixing our rows into it would confuse the
provenance and be overwritten by the next catalogue update.

Usage:
    python scripts/fetch_double_stars.py
"""

from __future__ import annotations

import csv
import io
import sys
import urllib.parse
import urllib.request
from pathlib import Path

TAP = "https://simbad.u-strasbg.fr/simbad/sim-tap/sync"
OUT = Path(__file__).resolve().parent.parent / "engine" / "catalog" / "data" / "double_stars.csv"

#: The doubles worth a beginner's time, with the identifier SIMBAD resolves
#: and the separation and component magnitudes that make each one interesting.
#:
#: Separations are the figure quoted in observing guides rather than a current
#: ephemeris: several of these are physical binaries whose separation changes
#: over decades, and none changes fast enough to matter for deciding whether
#: your telescope can split it.
#:
#: Component magnitudes are the pair's, quoted from observing guides. SIMBAD
#: carries a V magnitude for the system only when one has been measured for
#: the composite, which for a resolved pair it often has not -- so the
#: brighter component is used as the catalogue magnitude, since that is what
#: decides whether you can see the thing at all.
DOUBLES = [
    # (simbad id, display name, constellation, separation ", mags, note)
    ("Albireo", "Albireo", "Cyg", 35.0, "3.1 / 5.1",
     "Gold and blue, the finest colour contrast in the sky. Splits in "
     "binoculars."),
    ("* zet UMa", "Mizar", "UMa", 14.4, "2.2 / 3.9",
     "Naked-eye with Alcor beside it; a telescope splits Mizar itself."),
    ("* gam And", "Almach", "And", 9.6, "2.3 / 5.0",
     "Orange and blue-green, and brighter than Albireo."),
    ("Cor Caroli", "Cor Caroli", "CVn", 19.3, "2.9 / 5.6",
     "An easy wide pair in a barren patch of sky."),
    ("* eps01 Lyr", "The Double Double", "Lyr", 208.0, "5.0 / 5.2",
     "Two pairs. Binoculars split it into two stars; 100 mm and steady air "
     "splits each of those again."),
    ("* alf Gem", "Castor", "Gem", 5.4, "1.9 / 3.0",
     "A tight bright pair that has visibly rotated since Herschel measured "
     "it."),
    ("* eps Boo", "Izar", "Boo", 2.9, "2.6 / 4.8",
     "Struve called it Pulcherrima, the most beautiful. Needs 100 mm and a "
     "still night."),
    ("* bet Sco", "Graffias", "Sco", 13.6, "2.6 / 4.5",
     "A clean white pair low in the summer south."),
    ("* gam Ari", "Mesarthim", "Ari", 7.4, "4.6 / 4.7",
     "Two near-identical white stars; one of the first doubles ever found."),
    ("* gam Del", "Gamma Delphini", "Del", 9.0, "4.3 / 5.1",
     "Gold and green-white at the nose of the Dolphin."),
    ("* alf Her", "Rasalgethi", "Her", 4.6, "3.5 / 5.4",
     "A red giant with a green-looking companion."),
    ("* 61 Cyg", "61 Cygni", "Cyg", 31.6, "5.2 / 6.1",
     "The first star to have its distance measured, in 1838."),
    ("* zet Cnc", "Tegmine", "Cnc", 6.0, "5.3 / 6.2",
     "A triple; the brighter component is itself double."),
    ("* iot Cnc", "Iota Cancri", "Cnc", 30.5, "4.0 / 6.6",
     "Often called the spring Albireo, for the same gold and blue."),
    ("* eta Cas", "Achird", "Cas", 13.4, "3.5 / 7.4",
     "A yellow sun much like ours with a red dwarf alongside."),
    ("* alf Cru", "Acrux", "Cru", 4.0, "1.3 / 1.7",
     "The brightest star of the Southern Cross, and a pair."),
    ("* alf Cen", "Alpha Centauri", "Cen", 8.0, "0.0 / 1.3",
     "The nearest star system, and a superb pair."),
    ("* the Ori", "Trapezium", "Ori", 13.0, "5.1 / 6.7",
     "The four stars lighting the Orion Nebula from inside it."),
    ("* bet Mon", "Beta Monocerotis", "Mon", 7.3, "4.6 / 5.0",
     "Three blue-white stars in a row; Herschel called it one of the finest "
     "sights in the heavens."),
    ("* gam Leo", "Algieba", "Leo", 4.6, "2.4 / 3.6",
     "Two orange giants, tight and bright."),
]


def query(identifier: str) -> tuple[float, float, float | None, float | None]:
    """RA, Dec (J2000 degrees), V magnitude and parallax (mas).

    Parallax is worth having here in a way it is not for deep-sky objects:
    these are nearby stars, Gaia has measured them directly, and the result
    is a real distance rather than the cross-matched nonsense the OpenNGC
    parallax column holds for galaxies.
    """
    adql = (
        "SELECT b.ra, b.dec, b.plx_value, f.V "
        "FROM basic b JOIN ident i ON b.oid = i.oidref "
        "LEFT JOIN allfluxes f ON b.oid = f.oidref "
        f"WHERE i.id = '{identifier}'"
    )
    url = TAP + "?" + urllib.parse.urlencode({
        "request": "doQuery", "lang": "adql", "format": "csv", "query": adql,
    })
    with urllib.request.urlopen(url, timeout=30) as response:
        rows = list(csv.DictReader(io.StringIO(response.read().decode("utf-8"))))
    if not rows:
        raise LookupError(f"SIMBAD has no {identifier!r}")
    row = rows[0]
    v = (row.get("V") or "").strip()
    plx = (row.get("plx_value") or "").strip()
    return (float(row["ra"]), float(row["dec"]),
            float(v) if v else None, float(plx) if plx else None)


def sexagesimal(ra_deg: float, dec_deg: float) -> tuple[str, str]:
    """Degrees to the HH:MM:SS.s / +DD:MM:SS the OpenNGC CSVs use."""
    hours = ra_deg / 15.0
    hh = int(hours)
    mm = int((hours - hh) * 60)
    ss = ((hours - hh) * 60 - mm) * 60

    sign = "+" if dec_deg >= 0 else "-"
    d = abs(dec_deg)
    dd = int(d)
    dm = int((d - dd) * 60)
    ds = ((d - dd) * 60 - dm) * 60
    return f"{hh:02d}:{mm:02d}:{ss:04.1f}", f"{sign}{dd:02d}:{dm:02d}:{ds:02.0f}"


COLUMNS = [
    "Name", "Type", "RA", "Dec", "Const", "MajAx", "MinAx", "PosAng",
    "B-Mag", "V-Mag", "J-Mag", "H-Mag", "K-Mag", "SurfBr", "Hubble", "Pax",
    "Pm-RA", "Pm-Dec", "RadVel", "Redshift", "Cstar U-Mag", "Cstar B-Mag",
    "Cstar V-Mag", "M", "NGC", "IC", "Cstar Names", "Identifiers",
    "Common names", "NED notes", "OpenNGC notes", "Sources",
]


def main() -> int:
    rows = []
    for identifier, name, constellation, separation, mags, _note in DOUBLES:
        try:
            ra, dec, v, plx = query(identifier)
        except Exception as exc:                       # noqa: BLE001
            print(f"  {name:<20} FAILED: {exc}", file=sys.stderr)
            return 1
        ra_s, dec_s = sexagesimal(ra, dec)
        row = {column: "" for column in COLUMNS}
        row.update({
            "Name": f"DBL{name.replace(' ', '')}",
            "Type": "**",
            "RA": ra_s,
            "Dec": dec_s,
            "Const": constellation,
            # The pair's separation is its angular extent, in arcminutes.
            "MajAx": f"{separation / 60.0:.4f}",
            # Fall back to the brighter component: a resolved pair often has
            # no composite V, and a target with no magnitude is scored as an
            # unknown rather than as the bright object it is.
            "V-Mag": f"{v:.2f}" if v is not None
                     else f"{float(mags.split('/')[0].strip()):.2f}",
            "Common names": name,
            "Identifiers": identifier,
            "Sources": "SIMBAD",
        })
        rows.append(row)
        # Light years from parallax, for pasting into engine/reference.py.
        distance = f"{3.26156 / (plx / 1000.0):,.1f} ly" if plx else "--"
        print(f"  {name:<20} {ra_s} {dec_s}  V={row['V-Mag']}  {distance}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nwrote {len(rows)} rows to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
