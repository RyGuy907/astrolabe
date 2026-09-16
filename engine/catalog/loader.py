"""OpenNGC ingest: vendored CSV -> SQLite -> DeepSkyObject records.

Data: OpenNGC (github.com/mattiaverga/OpenNGC), CC-BY-SA 4.0. The CSVs in
`data/` are vendored so the engine works with no network.

Two things in PLAN.md 7 are handled here:

* **Catalog dupes.** OpenNGC carries explicit `Dup` rows for objects with both
  an NGC and an IC designation. Those are dropped on ingest, and the surviving
  rows are deduplicated by sky position as a second pass.
* **Surface brightness.** OpenNGC's own `SurfBr` column is B-band. Visual
  observing cares about V, so `surface_brightness` is computed from the V
  magnitude and the object's dimensions; OpenNGC's value is kept alongside as
  `catalog_surface_brightness` for reference. The two differ by ~0.4 mag
  systematically, and mixing them would quietly corrupt the contrast model.
"""

from __future__ import annotations

import csv
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
NGC_CSV = DATA_DIR / "NGC.csv"
ADDENDUM_CSV = DATA_DIR / "addendum.csv"

SCHEMA_VERSION = 1

# OpenNGC type code -> our observing group (PLAN.md 3.3.5).
TYPE_GROUPS = {
    "G": "Galaxies",
    "GPair": "Galaxies",
    "GTrpl": "Galaxies",
    "GGroup": "Galaxies",
    "PN": "Nebulae",
    "HII": "Nebulae",
    "Neb": "Nebulae",
    "EmN": "Nebulae",
    "RfN": "Nebulae",
    "DrkN": "Nebulae",
    "SNR": "Nebulae",
    "Cl+N": "Nebulae",
    "OCl": "Open Clusters",
    "GCl": "Globular Clusters",
    "**": "Double Stars",
    "*Ass": "Asterisms",
}

# Dropped on ingest: duplicates, non-existent entries, single stars, novae and
# the catch-all "Other" bucket. None of them are observing targets.
SKIPPED_TYPES = {"Dup", "NonEx", "Nova", "*", "Other"}

# ...except that a Messier number means somebody judged the object worth
# pointing a telescope at, which outranks OpenNGC's type bucket. Only these two
# types stay fatal for a Messier row: `Dup` because the object is catalogued
# elsewhere under its real designation (this is how M102 is handled — OpenNGC
# equates it with M101 and gives it M101's coordinates), and `NonEx` because
# the object does not exist.
ALWAYS_SKIPPED_TYPES = {"Dup", "NonEx"}

# Group assignment for Messier objects rescued from a skipped type. Kept as an
# explicit table rather than a general rule, because it is one known case and
# guessing a category from the `Other` bucket would be inventing data.
#   M73 / NGC 6994 - four stars whose cluster status is disputed.
MESSIER_TYPE_OVERRIDES = {73: "Asterisms"}

TYPE_LABELS = {
    "G": "galaxy", "GPair": "galaxy pair", "GTrpl": "galaxy triplet",
    "GGroup": "galaxy group", "PN": "planetary nebula", "HII": "HII region",
    "Neb": "nebula", "EmN": "emission nebula", "RfN": "reflection nebula",
    "DrkN": "dark nebula", "SNR": "supernova remnant",
    "Cl+N": "cluster + nebula", "OCl": "open cluster",
    "GCl": "globular cluster", "**": "double star", "*Ass": "asterism",
}


@dataclass(frozen=True)
class DeepSkyObject:
    """One catalog entry, in the units the rest of the engine expects."""

    name: str                       # OpenNGC primary designation, e.g. NGC0224
    obj_type: str                   # OpenNGC type code
    group: str                      # our observing group
    ra_deg: float                   # J2000
    dec_deg: float                  # J2000
    constellation: str
    messier: int | None
    common_names: tuple[str, ...]
    major_axis_arcmin: float | None
    minor_axis_arcmin: float | None
    v_mag: float | None
    b_mag: float | None
    surface_brightness: float | None          # V-band, mag/arcsec^2, computed
    catalog_surface_brightness: float | None  # OpenNGC's B-band value

    @property
    def type_label(self) -> str:
        return TYPE_LABELS.get(self.obj_type, self.obj_type)

    @property
    def display_name(self) -> str:
        """"M31 (Andromeda Galaxy)" where possible, else the catalog name."""
        primary = f"M{self.messier}" if self.messier else _pretty_ngc(self.name)
        if self.common_names:
            return f"{primary} ({self.common_names[0]})"
        return primary

    @property
    def magnitude(self) -> float | None:
        """Best available integrated magnitude, V preferred."""
        return self.v_mag if self.v_mag is not None else self.b_mag

    @property
    def size_arcmin(self) -> float | None:
        """Major axis, arcminutes: the object's apparent size on the sky."""
        return self.major_axis_arcmin

    @property
    def is_extended(self) -> bool:
        """Extended enough that surface brightness governs detection.

        PLAN.md 7: a mag-8 galaxy spread over 10' is harder than a mag-10
        planetary nebula. Anything above about an arcminute is in that regime.
        """
        return (self.major_axis_arcmin or 0) > 1.0


def _pretty_ngc(name: str) -> str:
    """NGC0224 -> NGC 224, IC0434 -> IC 434."""
    for prefix in ("NGC", "IC"):
        if name.startswith(prefix):
            rest = name[len(prefix):]
            digits = rest.lstrip("0") or "0"
            return f"{prefix} {digits}"
    return name


def _float(value: str) -> float | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_ra(value: str) -> float | None:
    """"00:42:44.35" (hours) -> degrees."""
    parts = (value or "").strip().split(":")
    if len(parts) != 3:
        return None
    try:
        hours, minutes, seconds = (float(p) for p in parts)
    except ValueError:
        return None
    return (hours + minutes / 60.0 + seconds / 3600.0) * 15.0


def _parse_dec(value: str) -> float | None:
    """"+41:16:08.6" -> degrees."""
    text = (value or "").strip()
    parts = text.split(":")
    if len(parts) != 3:
        return None
    sign = -1.0 if text.startswith("-") else 1.0
    try:
        degrees, minutes, seconds = (abs(float(p)) for p in parts)
    except ValueError:
        return None
    return sign * (degrees + minutes / 60.0 + seconds / 3600.0)


def surface_brightness(mag: float | None, major_arcmin: float | None,
                       minor_arcmin: float | None) -> float | None:
    """Mean surface brightness in mag/arcsec^2 over the object's ellipse.

    SB = m + 2.5 * log10(area), with area the ellipse pi/4 * a * b in square
    arcseconds. Point-like objects have no meaningful surface brightness, so
    anything under 0.1' returns None rather than a huge number.
    """
    if mag is None or not major_arcmin or major_arcmin < 0.1:
        return None
    minor = minor_arcmin or major_arcmin      # assume round if only one axis
    area_arcsec2 = math.pi / 4.0 * (major_arcmin * 60.0) * (minor * 60.0)
    if area_arcsec2 <= 0:
        return None
    return mag + 2.5 * math.log10(area_arcsec2)


def _parse_row(row: dict) -> DeepSkyObject | None:
    obj_type = (row.get("Type") or "").strip()
    messier_raw = (row.get("M") or "").strip().lstrip("0")
    messier = int(messier_raw) if messier_raw.isdigit() else None

    if obj_type in ALWAYS_SKIPPED_TYPES:
        return None
    group = TYPE_GROUPS.get(obj_type)
    if group is None:
        # Not an observing type. Keep it only if it carries a Messier number
        # and we know where to file it.
        group = MESSIER_TYPE_OVERRIDES.get(messier) if messier else None
        if group is None:
            return None

    ra = _parse_ra(row.get("RA", ""))
    dec = _parse_dec(row.get("Dec", ""))
    if ra is None or dec is None:
        return None

    common = tuple(
        n.strip() for n in (row.get("Common names") or "").split(",") if n.strip()
    )
    v_mag, b_mag = _float(row.get("V-Mag", "")), _float(row.get("B-Mag", ""))
    major, minor = _float(row.get("MajAx", "")), _float(row.get("MinAx", ""))

    return DeepSkyObject(
        name=(row.get("Name") or "").strip(),
        obj_type=obj_type,
        group=group,
        ra_deg=ra,
        dec_deg=dec,
        constellation=(row.get("Const") or "").strip(),
        messier=messier,
        common_names=common,
        major_axis_arcmin=major,
        minor_axis_arcmin=minor,
        v_mag=v_mag,
        b_mag=b_mag,
        # Computed from V so it is consistent with visual detectability.
        surface_brightness=surface_brightness(v_mag, major, minor),
        catalog_surface_brightness=_float(row.get("SurfBr", "")),
    )


def _dedupe(objects: list[DeepSkyObject]) -> list[DeepSkyObject]:
    """Drop entries sharing a sky position to within ~0.1 arcmin.

    OpenNGC's `Dup` rows are already gone by this point; this catches the
    remainder, e.g. an addendum entry restating a main-catalog object. The
    entry with a Messier number wins, then the one with a magnitude, then the
    first seen — so the better-described record survives.
    """
    def quality(obj: DeepSkyObject) -> tuple:
        return (obj.messier is not None, obj.magnitude is not None,
                len(obj.common_names))

    best: dict[tuple[int, int], DeepSkyObject] = {}
    for obj in objects:
        key = (round(obj.ra_deg * 600), round(obj.dec_deg * 600))
        existing = best.get(key)
        if existing is None or quality(obj) > quality(existing):
            best[key] = obj
    return sorted(best.values(), key=lambda o: o.name)


def parse_catalog(ngc_csv: Path | None = None,
                  addendum_csv: Path | None = None) -> list[DeepSkyObject]:
    """Read the vendored CSVs into deduplicated DeepSkyObject records."""
    objects: list[DeepSkyObject] = []
    for path in (ngc_csv or NGC_CSV, addendum_csv or ADDENDUM_CSV):
        if not path.exists():
            continue
        with open(path, encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh, delimiter=";"):
                parsed = _parse_row(row)
                if parsed is not None:
                    objects.append(parsed)
    return _dedupe(objects)


# --- SQLite -----------------------------------------------------------------

DDL = """
CREATE TABLE IF NOT EXISTS objects (
    name            TEXT PRIMARY KEY,
    obj_type        TEXT NOT NULL,
    grp             TEXT NOT NULL,
    ra_deg          REAL NOT NULL,
    dec_deg         REAL NOT NULL,
    constellation   TEXT,
    messier         INTEGER,
    common_names    TEXT,
    major_arcmin    REAL,
    minor_arcmin    REAL,
    v_mag           REAL,
    b_mag           REAL,
    surface_bright  REAL,
    catalog_sb      REAL
);
CREATE INDEX IF NOT EXISTS idx_objects_grp ON objects(grp);
CREATE INDEX IF NOT EXISTS idx_objects_dec ON objects(dec_deg);
CREATE INDEX IF NOT EXISTS idx_objects_messier ON objects(messier);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def catalog_db_path() -> Path:
    """SQLite location, alongside the ephemeris cache."""
    from ..ephem import ephemeris_dir

    return ephemeris_dir().parent / "catalog.sqlite3"


def build_database(path: Path | None = None,
                   objects: list[DeepSkyObject] | None = None) -> Path:
    """Parse the CSVs once into SQLite. Idempotent; overwrites any prior build."""
    path = path or catalog_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    objects = objects if objects is not None else parse_catalog()

    connection = sqlite3.connect(path)
    try:
        connection.executescript(DDL)
        connection.execute("DELETE FROM objects")
        connection.executemany(
            "INSERT INTO objects VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (o.name, o.obj_type, o.group, o.ra_deg, o.dec_deg,
                 o.constellation, o.messier, ",".join(o.common_names),
                 o.major_axis_arcmin, o.minor_axis_arcmin, o.v_mag, o.b_mag,
                 o.surface_brightness, o.catalog_surface_brightness)
                for o in objects
            ],
        )
        connection.execute(
            "INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        connection.commit()
    finally:
        connection.close()
    return path


def _row_to_object(row: sqlite3.Row) -> DeepSkyObject:
    return DeepSkyObject(
        name=row["name"],
        obj_type=row["obj_type"],
        group=row["grp"],
        ra_deg=row["ra_deg"],
        dec_deg=row["dec_deg"],
        constellation=row["constellation"],
        messier=row["messier"],
        common_names=tuple(n for n in (row["common_names"] or "").split(",") if n),
        major_axis_arcmin=row["major_arcmin"],
        minor_axis_arcmin=row["minor_arcmin"],
        v_mag=row["v_mag"],
        b_mag=row["b_mag"],
        surface_brightness=row["surface_bright"],
        catalog_surface_brightness=row["catalog_sb"],
    )


def load_catalog(path: Path | None = None, *, rebuild: bool = False
                 ) -> list[DeepSkyObject]:
    """All catalog objects, building the SQLite cache on first use.

    Reading the vendored CSV and the SQLite cache are both local file reads,
    so this stays inside the engine's no-network contract.
    """
    path = path or catalog_db_path()
    if rebuild or not path.exists():
        build_database(path)

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("SELECT * FROM objects").fetchall()
    except sqlite3.DatabaseError:
        connection.close()
        build_database(path)
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT * FROM objects").fetchall()
    finally:
        connection.close()
    return [_row_to_object(r) for r in rows]


def find_object(designation: str, catalog: list[DeepSkyObject] | None = None
                ) -> DeepSkyObject | None:
    """Look up by Messier number, catalog name, or common name."""
    catalog = catalog if catalog is not None else load_catalog()
    query = designation.strip().lower()

    if query.startswith("m") and query[1:].strip().isdigit():
        number = int(query[1:])
        return next((o for o in catalog if o.messier == number), None)

    compact = query.replace(" ", "")
    for obj in catalog:
        if obj.name.lower() == compact:
            return obj
        if _pretty_ngc(obj.name).lower().replace(" ", "") == compact:
            return obj
    for obj in catalog:
        if any(query == n.lower() for n in obj.common_names):
            return obj
    return None
