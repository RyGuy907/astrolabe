"""The week ahead: each night scored as its own dashboard would score it."""

from __future__ import annotations

from datetime import timedelta

import pytest

from conftest import REFERENCE_DATE, requires_ephemeris
from engine.ephem import night_window
from engine.outlook import MAX_NIGHTS, week_outlook
from engine.scoring import limiting_factor, score_night
from engine.session import resolve_session
from engine.timeutil import is_aware_utc, local_noon_utc
from engine.weather import Forecast, HourlyConditions, unavailable


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setenv("ASTRO_NO_NETWORK", "1")


def _forecast(location, days, cloudy_from=None):
    """Hourly synthetic weather from noon on the reference date for `days`:
    clear, or overcast from the `cloudy_from`th day on."""
    start = local_noon_utc(REFERENCE_DATE, location.tz)
    hours = []
    for i in range(days * 24):
        cloudy = cloudy_from is not None and i >= cloudy_from * 24
        hours.append(HourlyConditions(
            time_utc=start + timedelta(hours=i),
            cloud_cover=100.0 if cloudy else 0.0, cloud_high=0.0,
            humidity_pct=40.0, temperature_c=10.0 + (i % 24) / 4,
            dew_point_c=0.0, wind_gust_kmh=5.0,
            seeing_quality=0.9, transparency_quality=0.9))
    return Forecast(location=location, hours=hours, available=True,
                    sources=("synthetic",))


@requires_ephemeris
def test_a_week_is_seven_consecutive_nights(home):
    week = week_outlook(home, REFERENCE_DATE, _forecast(home, 8))
    assert [n.date for n in week] == [REFERENCE_DATE + timedelta(days=i) for i in range(7)]


@requires_ephemeris
def test_each_night_matches_what_its_own_dashboard_says(home):
    """The point of scoring through `score_night`: the strip and the dials
    for the same night cannot disagree."""
    forecast = _forecast(home, 8, cloudy_from=3)
    week = week_outlook(home, REFERENCE_DATE, forecast)
    for night in week:
        window = night_window(night.date, home)
        session = resolve_session(window)
        own = score_night(window, forecast, session=session)
        assert night.session == session
        assert night.deep_sky_peak == pytest.approx(own.deep_sky_peak)
        assert night.deep_sky_mean == pytest.approx(own.deep_sky_mean)
        assert night.planetary_mean == pytest.approx(own.planetary_mean)
        assert night.best_window == own.best_window
        assert night.limiting_factor == limiting_factor(own)
        assert night.moon_illumination == window.moon_illumination


@requires_ephemeris
def test_cloud_and_temperature_describe_the_session(home):
    week = week_outlook(home, REFERENCE_DATE, _forecast(home, 8, cloudy_from=3))
    assert week[0].cloud_mean_pct == pytest.approx(0.0)
    assert week[4].cloud_mean_pct == pytest.approx(100.0)
    assert week[4].deep_sky_mean < week[0].deep_sky_mean
    assert week[4].limiting_factor == "cloud"
    assert all(n.temperature_low_c is not None for n in week)


@requires_ephemeris
def test_a_night_past_the_forecast_is_not_graded(home):
    """Three days of weather and a seven-night run: the last nights say they
    have no forecast rather than scoring the Moon and calling it an A."""
    week = week_outlook(home, REFERENCE_DATE, _forecast(home, 3))
    assert week[0].is_gradeable
    assert not week[-1].is_gradeable
    assert week[-1].deep_sky_grade == "n/a"
    assert week[-1].cloud_mean_pct is None
    assert week[-1].weather_note == "outside the forecast's range"


@requires_ephemeris
def test_past_a_7timer_only_forecast_the_note_says_why_it_is_short(home):
    """With Open-Meteo out, 7Timer's three days are all there is; the nights
    past them say so, rather than just "outside the forecast's range"."""
    import dataclasses

    short = dataclasses.replace(_forecast(home, 3), sources=("7Timer",),
                                fallback_reason="Open-Meteo unreachable")
    week = week_outlook(home, REFERENCE_DATE, short)
    assert week[0].is_gradeable
    assert week[-1].weather_note == ("beyond 7Timer's 3-day forecast; "
                                     "Open-Meteo unreachable")


@requires_ephemeris
def test_no_forecast_at_all_still_gives_every_night_with_the_reason(home):
    week = week_outlook(home, REFERENCE_DATE, unavailable(home, "network disabled"))
    assert len(week) == 7
    assert not any(n.is_gradeable for n in week)
    assert {n.weather_note for n in week} == {"network disabled"}
    # Darkness and the Moon need no weather.
    assert all(0.0 <= n.moon_illumination <= 1.0 for n in week)
    assert all(n.dark_hours >= 0.0 for n in week)


@requires_ephemeris
def test_outlook_times_are_aware_utc(home):
    for night in week_outlook(home, REFERENCE_DATE, _forecast(home, 8)):
        for span in (night.session, night.best_window):
            if span is not None:
                assert is_aware_utc(span[0]) and is_aware_utc(span[1])


def test_the_run_length_is_bounded(home):
    with pytest.raises(ValueError):
        week_outlook(home, REFERENCE_DATE, None, nights=0)
    with pytest.raises(ValueError):
        week_outlook(home, REFERENCE_DATE, None, nights=MAX_NIGHTS + 1)
