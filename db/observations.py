"""Observation log: sessions and the objects seen in them (PLAN.md §5).

Two design points worth stating, both from PLAN.md §5:

* **Conditions are snapshotted at session time.** A forecast expires; a log
  entry saying "87 — clear, moon 68%" is still meaningful in five years, while
  a foreign key to a forecast that no longer exists is not. The snapshot is
  stored as JSON on the session row.

* **The log feeds back into ranking.** PLAN.md §3.3.4 wants a small novelty
  bonus for objects not yet logged. `logged_object_names()` returns that set;
  `engine.targets` accepts it as an argument rather than reading the database
  itself, which keeps the engine free of I/O it does not own.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from engine.timeutil import UTC, ensure_utc, now_utc

from .store import connect

SCHEMA_VERSION = 2

DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    date                     TEXT NOT NULL,
    location_key             TEXT NOT NULL,
    start_utc                TEXT,
    end_utc                  TEXT,
    scope_key                TEXT,
    conditions_snapshot_json TEXT,
    seeing_actual            INTEGER,
    transparency_actual      INTEGER,
    notes                    TEXT,
    created_utc              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_date ON sessions(date);

CREATE TABLE IF NOT EXISTS observations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    object_id       TEXT,
    object_name     TEXT NOT NULL,
    observed_at_utc TEXT,
    eyepiece        TEXT,
    notes           TEXT,
    rating          INTEGER,
    sketch_path     TEXT
);
CREATE INDEX IF NOT EXISTS idx_observations_session ON observations(session_id);
CREATE INDEX IF NOT EXISTS idx_observations_object ON observations(object_id);
"""


class LogError(ValueError):
    """Raised for invalid log input, e.g. a rating outside 1-5."""


@dataclass(frozen=True)
class Observation:
    object_name: str
    object_id: str | None = None
    observed_at_utc: datetime | None = None
    eyepiece: str | None = None
    notes: str | None = None
    rating: int | None = None
    sketch_path: str | None = None
    id: int | None = None
    session_id: int | None = None

    def __post_init__(self) -> None:
        if self.rating is not None and not 1 <= self.rating <= 5:
            raise LogError(f"rating {self.rating} outside 1-5")
        if self.observed_at_utc is not None:
            object.__setattr__(self, "observed_at_utc",
                               ensure_utc(self.observed_at_utc,
                                          field="observed_at_utc"))


@dataclass(frozen=True)
class Session:
    date: date
    location_key: str
    start_utc: datetime | None = None
    end_utc: datetime | None = None
    scope_key: str | None = None
    conditions: dict | None = None
    seeing_actual: int | None = None
    transparency_actual: int | None = None
    notes: str | None = None
    id: int | None = None
    observations: list[Observation] = field(default_factory=list)

    def __post_init__(self) -> None:
        for name in ("start_utc", "end_utc"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, ensure_utc(value, field=name))
        for name, value in (("seeing_actual", self.seeing_actual),
                            ("transparency_actual", self.transparency_actual)):
            if value is not None and not 1 <= value <= 5:
                raise LogError(f"{name} {value} outside 1-5")


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(DDL)
    connection.execute("INSERT OR REPLACE INTO meta VALUES ('log_schema_version', ?)",
                       (str(SCHEMA_VERSION),))
    connection.commit()


def _open(path: Path | None = None) -> sqlite3.Connection:
    connection = connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    _ensure_schema(connection)
    return connection


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


# --- conditions snapshot ----------------------------------------------------

def snapshot_conditions(night_window, night_score) -> dict:
    """Freeze what the night looked like, so the log survives the forecast.

    Deliberately records `weather_available` and `is_gradeable` alongside the
    numbers: a snapshot that shows "deep sky 100" without recording that there
    was no weather data would be actively misleading years later.
    """
    return {
        "snapshot_utc": now_utc().isoformat(),
        "dark_hours": round(night_window.dark_hours, 3),
        "astronomical_night_hours": round(night_window.astronomical_night_hours, 3),
        "moon_illumination": round(night_window.moon_illumination, 4),
        "moon_up_at_dusk": night_window.moon_up_at_dusk,
        "deep_sky_peak": round(night_score.deep_sky_peak, 1),
        "planetary_peak": round(night_score.planetary_peak, 1),
        "deep_sky_grade": night_score.deep_sky_grade,
        "planetary_grade": night_score.planetary_grade,
        "is_gradeable": night_score.is_gradeable,
        "weather_available": night_score.weather_available,
        "weather_note": night_score.weather_note,
        "seeing_estimated": night_score.seeing_estimated,
        "limiting_factor": (
            night_score.slots and
            max(night_score.slots, key=lambda s: s.deep_sky)
            .deep_sky_factors.weakest()[0]
        ) or None,
    }


# --- sessions ---------------------------------------------------------------

def create_session(session: Session, path: Path | None = None) -> Session:
    connection = _open(path)
    try:
        cursor = connection.execute(
            "INSERT INTO sessions (date, location_key, start_utc, end_utc, "
            "scope_key, conditions_snapshot_json, seeing_actual, "
            "transparency_actual, notes, created_utc) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (session.date.isoformat(), session.location_key,
             session.start_utc.isoformat() if session.start_utc else None,
             session.end_utc.isoformat() if session.end_utc else None,
             session.scope_key,
             json.dumps(session.conditions) if session.conditions else None,
             session.seeing_actual, session.transparency_actual, session.notes,
             now_utc().isoformat()),
        )
        connection.commit()
        session_id = int(cursor.lastrowid)
    finally:
        connection.close()

    for observation in session.observations:
        add_observation(session_id, observation, path)
    return get_session(session_id, path)


def _row_to_session(row: sqlite3.Row, observations: list[Observation]) -> Session:
    return Session(
        id=row["id"],
        date=date.fromisoformat(row["date"]),
        location_key=row["location_key"],
        start_utc=_parse_utc(row["start_utc"]),
        end_utc=_parse_utc(row["end_utc"]),
        scope_key=row["scope_key"],
        conditions=(json.loads(row["conditions_snapshot_json"])
                    if row["conditions_snapshot_json"] else None),
        seeing_actual=row["seeing_actual"],
        transparency_actual=row["transparency_actual"],
        notes=row["notes"],
        observations=observations,
    )


def _row_to_observation(row: sqlite3.Row) -> Observation:
    return Observation(
        id=row["id"], session_id=row["session_id"],
        object_id=row["object_id"], object_name=row["object_name"],
        observed_at_utc=_parse_utc(row["observed_at_utc"]),
        eyepiece=row["eyepiece"], notes=row["notes"],
        rating=row["rating"], sketch_path=row["sketch_path"],
    )


def get_session(session_id: int, path: Path | None = None) -> Session | None:
    connection = _open(path)
    try:
        row = connection.execute("SELECT * FROM sessions WHERE id = ?",
                                 (session_id,)).fetchone()
        if row is None:
            return None
        observation_rows = connection.execute(
            "SELECT * FROM observations WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
    finally:
        connection.close()
    return _row_to_session(row, [_row_to_observation(r) for r in observation_rows])


def list_sessions(limit: int = 50, location_key: str | None = None,
                  path: Path | None = None, *, on_date: date | None = None) -> list[Session]:
    """Newest first, optionally for one site and/or one night."""
    connection = _open(path)
    try:
        where, params = [], []
        if location_key:
            where.append("location_key = ?")
            params.append(location_key)
        if on_date:
            where.append("date = ?")
            params.append(on_date.isoformat())
        clause = f"WHERE {' AND '.join(where)} " if where else ""
        rows = connection.execute(
            f"SELECT * FROM sessions {clause}ORDER BY date DESC, id DESC LIMIT ?",
            (*params, limit),
        ).fetchall()

        sessions = []
        for row in rows:
            observation_rows = connection.execute(
                "SELECT * FROM observations WHERE session_id = ? ORDER BY id",
                (row["id"],),
            ).fetchall()
            sessions.append(
                _row_to_session(row, [_row_to_observation(r) for r in observation_rows])
            )
    finally:
        connection.close()
    return sessions


def delete_session(session_id: int, path: Path | None = None) -> bool:
    connection = _open(path)
    try:
        connection.execute("DELETE FROM observations WHERE session_id = ?",
                           (session_id,))
        cursor = connection.execute("DELETE FROM sessions WHERE id = ?",
                                    (session_id,))
        connection.commit()
        return cursor.rowcount > 0
    finally:
        connection.close()


# --- observations -----------------------------------------------------------

def add_observation(session_id: int, observation: Observation,
                    path: Path | None = None) -> Observation:
    connection = _open(path)
    try:
        exists = connection.execute("SELECT 1 FROM sessions WHERE id = ?",
                                    (session_id,)).fetchone()
        if not exists:
            raise LogError(f"no session {session_id}")
        cursor = connection.execute(
            "INSERT INTO observations (session_id, object_id, object_name, "
            "observed_at_utc, eyepiece, notes, rating, sketch_path) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (session_id, observation.object_id, observation.object_name,
             (observation.observed_at_utc.isoformat()
              if observation.observed_at_utc else None),
             observation.eyepiece, observation.notes, observation.rating,
             observation.sketch_path),
        )
        connection.commit()
        observation_id = int(cursor.lastrowid)
        row = connection.execute("SELECT * FROM observations WHERE id = ?",
                                 (observation_id,)).fetchone()
    finally:
        connection.close()
    return _row_to_observation(row)


def delete_observation(observation_id: int, path: Path | None = None) -> bool:
    connection = _open(path)
    try:
        cursor = connection.execute("DELETE FROM observations WHERE id = ?",
                                    (observation_id,))
        connection.commit()
        return cursor.rowcount > 0
    finally:
        connection.close()


# --- history and the novelty bonus ------------------------------------------

def logged_object_ids(path: Path | None = None) -> set[str]:
    """Catalog ids of every object ever logged.

    Fed to `engine.targets.assess_targets` as the `logged` argument so it can
    apply PLAN.md §3.3.4's novelty bonus. The engine takes the set as data
    rather than reading this database itself.
    """
    connection = _open(path)
    try:
        rows = connection.execute(
            "SELECT DISTINCT object_id FROM observations "
            "WHERE object_id IS NOT NULL"
        ).fetchall()
    finally:
        connection.close()
    return {row["object_id"] for row in rows}


def object_history(object_id: str, path: Path | None = None) -> list[dict]:
    """Every time this object was logged, newest first."""
    connection = _open(path)
    try:
        rows = connection.execute(
            "SELECT o.*, s.date AS session_date, s.location_key "
            "FROM observations o JOIN sessions s ON o.session_id = s.id "
            "WHERE o.object_id = ? ORDER BY s.date DESC, o.id DESC",
            (object_id,),
        ).fetchall()
    finally:
        connection.close()
    return [
        {
            "observation_id": row["id"],
            "session_id": row["session_id"],
            "session_date": row["session_date"],
            "location_key": row["location_key"],
            "object_name": row["object_name"],
            "observed_at_utc": row["observed_at_utc"],
            "eyepiece": row["eyepiece"],
            "rating": row["rating"],
            "notes": row["notes"],
        }
        for row in rows
    ]


def log_statistics(path: Path | None = None) -> dict:
    connection = _open(path)
    try:
        sessions = connection.execute("SELECT COUNT(*) c FROM sessions").fetchone()["c"]
        observations = connection.execute(
            "SELECT COUNT(*) c FROM observations").fetchone()["c"]
        distinct = connection.execute(
            "SELECT COUNT(DISTINCT object_id) c FROM observations "
            "WHERE object_id IS NOT NULL").fetchone()["c"]
        first = connection.execute(
            "SELECT MIN(date) d FROM sessions").fetchone()["d"]
        last = connection.execute(
            "SELECT MAX(date) d FROM sessions").fetchone()["d"]
    finally:
        connection.close()
    return {
        "sessions": sessions,
        "observations": observations,
        "distinct_objects": distinct,
        "first_session": first,
        "last_session": last,
    }
