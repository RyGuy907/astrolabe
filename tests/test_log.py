"""Observation log: persistence, conditions snapshot, and the novelty bonus.

Every test runs against a temporary database (`tmp_path`), never the user's
real log. A test suite that wrote into someone's observing history would be
worse than no test suite.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from conftest import REFERENCE_DATE, requires_ephemeris
from db import observations
from db.observations import LogError, Observation, Session
from engine.catalog.loader import find_object, load_catalog
from engine.ephem import night_window
from engine.equipment import load_equipment
from engine.scoring import score_night
from engine.targets import NOVELTY_BONUS, assess_targets
from engine.timeutil import is_aware_utc
from engine.weather import unavailable


@pytest.fixture
def db(tmp_path):
    """An isolated log database for one test."""
    return tmp_path / "log.sqlite3"


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


@pytest.fixture(scope="module")
def kit():
    return load_equipment()


# --- sessions ---------------------------------------------------------------

def test_create_and_read_back_a_session(db):
    created = observations.create_session(
        Session(date=date(2026, 9, 15), location_key="home", scope_key="ad8",
                notes="clear and cold", seeing_actual=3, transparency_actual=4),
        path=db,
    )
    assert created.id is not None

    fetched = observations.get_session(created.id, path=db)
    assert fetched.date == date(2026, 9, 15)
    assert fetched.location_key == "home"
    assert fetched.notes == "clear and cold"
    assert fetched.seeing_actual == 3
    assert fetched.transparency_actual == 4


def test_missing_session_is_none(db):
    assert observations.get_session(999, path=db) is None


def test_sessions_come_back_newest_first(db):
    for day in (10, 20, 15):
        observations.create_session(
            Session(date=date(2026, 9, day), location_key="home"), path=db)

    dates = [s.date for s in observations.list_sessions(path=db)]
    assert dates == sorted(dates, reverse=True)


def test_sessions_filter_by_location(db):
    observations.create_session(Session(date=REFERENCE_DATE,
                                        location_key="home"), path=db)
    observations.create_session(Session(date=REFERENCE_DATE,
                                        location_key="santa_monica_mtns"), path=db)

    assert len(observations.list_sessions(path=db)) == 2
    assert len(observations.list_sessions(location_key="home", path=db)) == 1


def test_sessions_filter_by_night_however_old(db):
    """The log reattaches to a night's session by looking it up by date and
    site. Found among the ten most recent, an older night's was missed and a
    duplicate made."""
    for day in range(1, 21):
        observations.create_session(
            Session(date=date(2026, 8, day), location_key="home"), path=db)
    old = observations.list_sessions(limit=1, location_key="home",
                                     on_date=date(2026, 8, 2), path=db)
    assert [s.date for s in old] == [date(2026, 8, 2)]
    assert observations.list_sessions(on_date=date(2026, 7, 1), path=db) == []


def test_deleting_a_session_removes_its_observations(db):
    session = observations.create_session(
        Session(date=REFERENCE_DATE, location_key="home"), path=db)
    observations.add_observation(session.id, Observation(object_name="M31",
                                                         object_id="NGC0224"),
                                 path=db)

    assert observations.log_statistics(path=db)["observations"] == 1
    assert observations.delete_session(session.id, path=db) is True

    stats = observations.log_statistics(path=db)
    assert stats["sessions"] == 0
    assert stats["observations"] == 0


def test_deleting_a_missing_session_returns_false(db):
    assert observations.delete_session(4242, path=db) is False


def test_seeing_outside_the_scale_is_rejected():
    with pytest.raises(LogError):
        Session(date=REFERENCE_DATE, location_key="home", seeing_actual=9)


# --- observations -----------------------------------------------------------

def test_add_and_read_observations(db):
    session = observations.create_session(
        Session(date=REFERENCE_DATE, location_key="home"), path=db)

    observations.add_observation(
        session.id,
        Observation(object_id="NGC0224", object_name="M31 (Andromeda Galaxy)",
                    eyepiece="30mm", rating=5, notes="dust lanes"),
        path=db,
    )
    observations.add_observation(
        session.id, Observation(object_name="something unidentified"), path=db)

    fetched = observations.get_session(session.id, path=db)
    assert len(fetched.observations) == 2
    assert fetched.observations[0].rating == 5
    assert fetched.observations[0].notes == "dust lanes"
    # Free-text entries are kept without a catalog id.
    assert fetched.observations[1].object_id is None


def test_observation_against_a_missing_session_raises(db):
    with pytest.raises(LogError):
        observations.add_observation(999, Observation(object_name="M31"), path=db)


def test_rating_outside_one_to_five_is_rejected():
    with pytest.raises(LogError):
        Observation(object_name="M31", rating=0)
    with pytest.raises(LogError):
        Observation(object_name="M31", rating=6)


def test_deleting_one_observation_leaves_the_session(db):
    session = observations.create_session(
        Session(date=REFERENCE_DATE, location_key="home"), path=db)
    first = observations.add_observation(session.id,
                                         Observation(object_name="M31"), path=db)
    observations.add_observation(session.id, Observation(object_name="M13"),
                                 path=db)

    assert observations.delete_observation(first.id, path=db) is True
    assert len(observations.get_session(session.id, path=db).observations) == 1


# --- timezone boundary ------------------------------------------------------

def test_observation_times_round_trip_as_aware_utc(db):
    session = observations.create_session(
        Session(date=REFERENCE_DATE, location_key="home"), path=db)
    when = datetime(2026, 9, 16, 4, 30, tzinfo=timezone.utc)

    observations.add_observation(
        session.id, Observation(object_name="M31", observed_at_utc=when), path=db)

    stored = observations.get_session(session.id, path=db).observations[0]
    assert is_aware_utc(stored.observed_at_utc)
    assert stored.observed_at_utc == when


def test_a_naive_observation_time_is_rejected():
    from engine.timeutil import NaiveDatetimeError

    with pytest.raises(NaiveDatetimeError):
        Observation(object_name="M31", observed_at_utc=datetime(2026, 9, 16, 4, 30))


def test_local_times_are_converted_not_stored_raw(db):
    """An aware non-UTC time is normalised, not kept in its original zone."""
    from zoneinfo import ZoneInfo

    session = observations.create_session(
        Session(date=REFERENCE_DATE, location_key="home"), path=db)
    local = datetime(2026, 9, 15, 21, 30, tzinfo=ZoneInfo("America/Los_Angeles"))

    observations.add_observation(
        session.id, Observation(object_name="M31", observed_at_utc=local), path=db)

    stored = observations.get_session(session.id, path=db).observations[0]
    assert is_aware_utc(stored.observed_at_utc)
    assert stored.observed_at_utc == local.astimezone(timezone.utc)


# --- conditions snapshot ----------------------------------------------------

@requires_ephemeris
def test_snapshot_records_the_night(home):
    window = night_window(REFERENCE_DATE, home)
    score = score_night(window, unavailable(home, "beyond horizon"))
    snapshot = observations.snapshot_conditions(window, score)

    assert snapshot["dark_hours"] == pytest.approx(window.dark_hours, abs=0.01)
    assert snapshot["moon_illumination"] == pytest.approx(
        window.moon_illumination, abs=0.001)


@requires_ephemeris
def test_snapshot_records_that_there_was_no_weather(home):
    """PLAN.md §7's no-silent-degradation rule, carried into the archive.

    A snapshot showing "deep sky 100" without recording that there was no
    forecast would be misleading years later, when nobody can check.
    """
    window = night_window(REFERENCE_DATE, home)
    score = score_night(window, unavailable(home, "beyond horizon"))
    snapshot = observations.snapshot_conditions(window, score)

    assert snapshot["weather_available"] is False
    assert snapshot["is_gradeable"] is False
    assert snapshot["deep_sky_grade"] == "n/a"
    assert snapshot["weather_note"] == "beyond horizon"


@requires_ephemeris
def test_snapshot_survives_on_the_session(db, home):
    window = night_window(REFERENCE_DATE, home)
    score = score_night(window, unavailable(home, "offline"))

    session = observations.create_session(
        Session(date=REFERENCE_DATE, location_key="home",
                conditions=observations.snapshot_conditions(window, score)),
        path=db,
    )
    stored = observations.get_session(session.id, path=db)

    assert stored.conditions is not None
    assert stored.conditions["is_gradeable"] is False
    assert stored.conditions["dark_hours"] == pytest.approx(window.dark_hours,
                                                            abs=0.01)


# --- history and statistics -------------------------------------------------

def test_object_history_is_newest_first(db):
    for day in (10, 20, 15):
        session = observations.create_session(
            Session(date=date(2026, 9, day), location_key="home"), path=db)
        observations.add_observation(
            session.id, Observation(object_id="NGC0224", object_name="M31"),
            path=db)

    history = observations.object_history("NGC0224", path=db)
    assert len(history) == 3
    dates = [entry["session_date"] for entry in history]
    assert dates == sorted(dates, reverse=True)


def test_history_of_an_unobserved_object_is_empty(db):
    assert observations.object_history("NGC9999", path=db) == []


def test_statistics_count_distinct_objects(db):
    session = observations.create_session(
        Session(date=REFERENCE_DATE, location_key="home"), path=db)
    for object_id in ("NGC0224", "NGC0224", "NGC6205"):
        observations.add_observation(
            session.id, Observation(object_id=object_id, object_name=object_id),
            path=db)

    stats = observations.log_statistics(path=db)
    assert stats["observations"] == 3
    assert stats["distinct_objects"] == 2


def test_logged_object_ids_ignores_free_text(db):
    session = observations.create_session(
        Session(date=REFERENCE_DATE, location_key="home"), path=db)
    observations.add_observation(
        session.id, Observation(object_id="NGC0224", object_name="M31"), path=db)
    observations.add_observation(
        session.id, Observation(object_name="a fuzzy thing"), path=db)

    assert observations.logged_object_ids(path=db) == {"NGC0224"}


# --- PLAN.md 3.3.4 novelty bonus --------------------------------------------

@requires_ephemeris
def test_unlogged_objects_get_the_novelty_bonus(home, kit, catalog):
    window = night_window(REFERENCE_DATE, home)
    m31 = find_object("M31", catalog)

    seen = assess_targets(window, kit, catalog=catalog, logged={m31.name})
    unseen = assess_targets(window, kit, catalog=catalog, logged=set())

    seen_m31 = next(a for a in seen if a.obj.messier == 31)
    unseen_m31 = next(a for a in unseen if a.obj.messier == 31)

    assert unseen_m31.score - seen_m31.score == pytest.approx(NOVELTY_BONUS, abs=0.01)
    assert seen_m31.previously_logged is True
    assert unseen_m31.previously_logged is False


@requires_ephemeris
def test_no_log_context_means_no_novelty_at_all(home, kit, catalog):
    """`None` (no log) and an empty log are different.

    Collapsing them would silently add the bonus to every object whenever the
    caller simply had no log to pass.
    """
    window = night_window(REFERENCE_DATE, home)

    without = assess_targets(window, kit, catalog=catalog)
    empty_log = assess_targets(window, kit, catalog=catalog, logged=set())

    a = next(x for x in without if x.obj.messier == 31)
    b = next(x for x in empty_log if x.obj.messier == 31)

    assert b.score - a.score == pytest.approx(NOVELTY_BONUS, abs=0.01)
    # With no log context we do not claim to know whether it was seen.
    assert a.previously_logged is False
    assert "not yet logged" not in a.notes


@requires_ephemeris
def test_the_novelty_bonus_is_small(home, kit, catalog):
    """It should nudge, not reorder. A never-seen faint smudge must not
    outrank a well-placed showpiece."""
    assert NOVELTY_BONUS <= 5.0

    window = night_window(REFERENCE_DATE, home)
    everything = {o.name for o in catalog}
    all_seen = assess_targets(window, kit, catalog=catalog, logged=everything)
    none_seen = assess_targets(window, kit, catalog=catalog, logged=set())

    # The top galaxy is the same object either way.
    top_seen = next(a for a in all_seen if a.group == "Galaxies")
    top_unseen = next(a for a in none_seen if a.group == "Galaxies")
    assert top_seen.obj.name == top_unseen.obj.name


@requires_ephemeris
def test_scores_stay_within_range_with_the_bonus(home, kit, catalog):
    window = night_window(REFERENCE_DATE, home)
    results = assess_targets(window, kit, catalog=catalog, logged=set())
    assert all(0.0 <= a.score <= 100.0 for a in results)


@requires_ephemeris
def test_unlogged_objects_are_annotated(home, kit, catalog):
    window = night_window(REFERENCE_DATE, home)
    results = assess_targets(window, kit, catalog=catalog, logged=set())
    assert any("not yet logged" in a.notes for a in results)
