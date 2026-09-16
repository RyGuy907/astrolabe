"""Engine-level contracts: ephemeris caching, location config, purity."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from conftest import REFERENCE_DATE, requires_ephemeris
from engine.ephem import (
    EphemerisMissingError,
    _complement,
    _intersect,
    altaz_series,
    ephemeris_is_cached,
    ephemeris_path,
    load_ephemeris,
    night_window,
)
from engine.locations import BORTLE_SQM, LocationError, get_location, load_locations


# --- ephemeris cache --------------------------------------------------------

@requires_ephemeris
def test_ephemeris_is_reused_not_refetched():
    """With the cache warm, loading must succeed with downloads disabled.

    This is the "never re-downloaded" guarantee from PLAN.md 7. If the loader
    ever tried to re-fetch, disabling downloads would surface it.
    """
    mtime_before = ephemeris_path().stat().st_mtime
    eph = load_ephemeris(allow_download=False)
    assert eph.kernel is not None
    assert ephemeris_path().stat().st_mtime == mtime_before


@requires_ephemeris
def test_load_ephemeris_is_memoized():
    assert load_ephemeris(allow_download=False) is load_ephemeris(allow_download=False)


def test_missing_ephemeris_with_downloads_disabled_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRO_EPHEM_DIR", str(tmp_path / "empty"))
    load_ephemeris.cache_clear()
    try:
        assert not ephemeris_is_cached()
        with pytest.raises(EphemerisMissingError):
            load_ephemeris(allow_download=False)
    finally:
        load_ephemeris.cache_clear()


# --- locations config -------------------------------------------------------

def test_both_seeded_locations_load():
    locations = load_locations()
    assert set(locations) == {"home", "santa_monica_mtns"}


def test_home_matches_the_configured_values():
    home = get_location("home")
    assert home.name == "Agoura Hills"
    assert (home.lat, home.lon) == (34.1361, -118.7745)
    assert home.elevation_m == 300
    assert home.bortle == 5


def test_timezone_is_resolved_from_coordinates():
    """timezonefinder, not a hardcoded zone."""
    assert get_location("home").tz == "America/Los_Angeles"
    assert get_location("santa_monica_mtns").tz == "America/Los_Angeles"


def test_bortle_maps_to_sqm():
    assert get_location("home").sqm == BORTLE_SQM[5] == 20.4
    assert get_location("santa_monica_mtns").sqm == BORTLE_SQM[4] == 20.9


def test_default_location_is_home():
    assert get_location().key == "home"


def test_unknown_location_raises_with_a_useful_message():
    with pytest.raises(LocationError) as exc:
        get_location("mount_wilson")
    assert "home" in str(exc.value)


# --- night_window behaviour -------------------------------------------------

@requires_ephemeris
def test_night_window_is_deterministic(home):
    """Same inputs, same outputs: no hidden dependence on the current clock."""
    first = night_window(REFERENCE_DATE, home)
    second = night_window(REFERENCE_DATE, home)
    assert first.sunset_utc == second.sunset_utc
    assert first.dark_intervals == second.dark_intervals
    assert first.moon_illumination == second.moon_illumination


@requires_ephemeris
def test_night_spans_midnight_into_the_next_day(reference_night):
    """The night of D ends on D+1, per PLAN.md 3.1."""
    assert reference_night.sunset_utc.date() == date(2026, 9, 16)  # 19:01 PDT
    assert reference_night.sunrise_utc > reference_night.sunset_utc
    assert (reference_night.sunrise_utc - reference_night.sunset_utc) < timedelta(hours=14)


@requires_ephemeris
def test_dark_window_is_contained_in_astronomical_night(reference_night):
    """True dark can only ever be a subset of astronomical night."""
    night_start, night_end = reference_night.astronomical_night[0]
    for start, end in reference_night.dark_intervals:
        assert night_start <= start < end <= night_end


@requires_ephemeris
def test_full_moon_night_has_little_or_no_true_dark(home):
    """A near-full moon that is up all night should collapse the dark window.

    2026-10-26 is close to full; the moon is above the horizon through most of
    astronomical night, so true dark must be far shorter than the night itself.
    """
    window = night_window(date(2026, 10, 26), home)
    assert window.moon_illumination > 0.8
    assert window.dark_hours < window.astronomical_night_hours


def test_interval_algebra_supports_a_split_dark_window():
    """A moon whose whole up-interval nests inside the night splits the dark.

    Tested as interval algebra rather than by hunting for a real night: a
    scan of every night of 2026 at Agoura Hills, and of Nov 2026 - Feb 2027
    at a synthetic 60N site, produced no naturally split window. The geometry
    fights it — a short-arc (southern declination) moon is near new in
    winter and therefore up in daylight, while a moon that transits mid-night
    is near full and has too long an arc to fit inside the night.

    So the multi-interval representation is defensive. It is still the right
    representation: collapsing it to one (start, end) pair would be silently
    wrong whenever the case does arise, and the cost of supporting it is a
    list instead of a tuple.
    """
    from datetime import datetime, timezone

    def at(hour, minute=0, day=1):
        return datetime(2026, 12, day, hour, minute, tzinfo=timezone.utc)

    night = [(at(2), at(14))]                    # astronomical night
    moon_up = [(at(5), at(9))]                   # nests strictly inside
    moon_down = _complement(moon_up, at(2), at(14))

    assert moon_down == [(at(2), at(5)), (at(9), at(14))]

    dark = _intersect(night, moon_down)
    assert len(dark) == 2
    assert dark == [(at(2), at(5)), (at(9), at(14))]
    assert all(s < e for s, e in dark)
    assert all(a[1] <= b[0] for a, b in zip(dark, dark[1:]))


def test_interval_algebra_handles_a_moon_up_all_night():
    """Full moon up from dusk to dawn leaves no true dark at all."""
    from datetime import datetime, timezone

    def at(hour):
        return datetime(2026, 12, 1, hour, tzinfo=timezone.utc)

    night = [(at(2), at(14))]
    moon_down = _complement([(at(1), at(15))], at(2), at(14))
    assert moon_down == []
    assert _intersect(night, moon_down) == []


# --- altaz_series behaviour -------------------------------------------------

@requires_ephemeris
def test_altaz_series_sun_is_below_horizon_during_true_dark(reference_night, home):
    start, end = reference_night.dark_intervals[0]
    series = altaz_series("sun", home, start, end, timedelta(minutes=30))
    assert max(series.alt_deg) < -18.0


@requires_ephemeris
def test_altaz_series_moon_is_below_horizon_during_true_dark(reference_night, home):
    """The other half of the dark-window definition."""
    start, end = reference_night.dark_intervals[0]
    # Step in from the edges: the boundary sample sits exactly at moonset.
    series = altaz_series("moon", home,
                          start + timedelta(minutes=5),
                          end - timedelta(minutes=5),
                          timedelta(minutes=30))
    assert max(series.alt_deg) < 0.0


@requires_ephemeris
def test_altaz_azimuth_is_in_range(reference_night, home):
    start, end = reference_night.dark_intervals[0]
    series = altaz_series("jupiter", home, start, end, timedelta(hours=1))
    assert all(0.0 <= az < 360.0 for az in series.az_deg)
    assert all(-90.0 <= alt <= 90.0 for alt in series.alt_deg)


@requires_ephemeris
def test_altaz_series_rejects_unknown_body(home, reference_night):
    start, end = reference_night.dark_intervals[0]
    with pytest.raises(KeyError):
        altaz_series("m31", home, start, end, timedelta(hours=1))


@requires_ephemeris
def test_altaz_series_rejects_nonpositive_step(home, reference_night):
    start, end = reference_night.dark_intervals[0]
    with pytest.raises(ValueError):
        altaz_series("moon", home, start, end, timedelta(0))
