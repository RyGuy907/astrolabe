"""SQLite persistence for locations added at runtime (PLAN.md §5).

The YAML files stay the source of truth for the sites you curate by hand.
Anything added through the API lands here and is merged on top, so a runtime
addition never rewrites a file you maintain.

Schema versioning is a single `meta` row rather than a migrations framework —
this is a single-file personal database, and a migrations library would be
more machinery than the problem deserves.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from engine.horizon import parse_horizon, serialise as serialise_horizon
from engine.locations import Location, _resolve_tz

SCHEMA_VERSION = 1

DDL = """
CREATE TABLE IF NOT EXISTS locations (
    key           TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    lat           REAL NOT NULL,
    lon           REAL NOT NULL,
    elevation_m   REAL NOT NULL DEFAULT 0,
    bortle        INTEGER,
    tz            TEXT,
    horizon       TEXT,
    created_utc   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def database_path() -> Path:
    """Alongside the other caches, so one directory holds all local state."""
    from engine.ephem import ephemeris_dir

    return ephemeris_dir().parent / "planner.sqlite3"


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(DDL)
    connection.execute(
        "INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    connection.commit()
    return connection


def _row_to_location(row: sqlite3.Row) -> Location:
    return Location(
        key=row["key"],
        name=row["name"],
        lat=row["lat"],
        lon=row["lon"],
        elevation_m=row["elevation_m"],
        bortle=row["bortle"],
        tz=row["tz"] or _resolve_tz(row["lat"], row["lon"]),
        horizon=parse_horizon(row["horizon"]),
    )


def stored_locations(path: Path | None = None) -> dict[str, Location]:
    """Locations added at runtime. Empty dict if the database is unusable."""
    try:
        connection = connect(path)
    except sqlite3.DatabaseError:
        return {}
    try:
        rows = connection.execute("SELECT * FROM locations").fetchall()
    finally:
        connection.close()
    return {row["key"]: _row_to_location(row) for row in rows}


def save_location(location: Location, path: Path | None = None) -> Location:
    """Insert or replace a runtime location."""
    connection = connect(path)
    try:
        connection.execute(
            "INSERT OR REPLACE INTO locations "
            "(key, name, lat, lon, elevation_m, bortle, tz, horizon, created_utc) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (location.key, location.name, location.lat, location.lon,
             location.elevation_m, location.bortle, location.tz,
             # Whatever the profile is — preset, rotated preset, or a measured
             # az->alt map — it round-trips through this one column. Writing
             # `horizon.name` here used to store NULL for a measured profile,
             # which reloaded as flat-and-not-generic: a fabricated horizon
             # wearing the "measured" label.
             serialise_horizon(location.horizon),
             datetime.now(timezone.utc).isoformat()),
        )
        connection.commit()
    finally:
        connection.close()
    return location


def delete_location(key: str, path: Path | None = None) -> bool:
    connection = connect(path)
    try:
        cursor = connection.execute("DELETE FROM locations WHERE key = ?", (key,))
        connection.commit()
        return cursor.rowcount > 0
    finally:
        connection.close()


def all_locations(config_path: Path | None = None,
                  db_path: Path | None = None) -> dict[str, Location]:
    """YAML sites plus runtime additions, the latter taking precedence."""
    from engine.locations import load_locations

    merged = dict(load_locations(config_path))
    merged.update(stored_locations(db_path))
    return merged
