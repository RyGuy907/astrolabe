"""Dual condition scoring: factors, aggregation, and the no-weather contract."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from conftest import REFERENCE_DATE, requires_ephemeris
from engine.ephem import night_window
from engine.scoring import (
    FactorBreakdown,
    clear_factor,
    dew_factor,
    grade,
    moon_factor,
    score_night,
    score_slot,
    seeing_factor,
    transparency_factor,
    verdict,
    wind_factor,
)
from engine.timeutil import is_aware_utc, now_utc
from engine.weather import Forecast, HourlyConditions, blend, unavailable


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setenv("ASTRO_NO_NETWORK", "1")


def _clear_forecast(location, start, hours=14, **overrides):
    """A synthetic pristine night, with fields overridable per test."""
    defaults = dict(cloud_cover=0.0, cloud_high=0.0, humidity_pct=40.0,
                    temperature_c=15.0, dew_point_c=2.0, wind_gust_kmh=5.0,
                    seeing_quality=1.0, transparency_quality=1.0)
    defaults.update(overrides)
    samples = [
        HourlyConditions(time_utc=start + timedelta(hours=i), **defaults)
        for i in range(hours)
    ]
    return Forecast(location=location, hours=samples, available=True,
                    sources=("synthetic",))


# --- individual factors -----------------------------------------------------

def test_clear_factor_endpoints():
    assert clear_factor(0) == pytest.approx(1.0)
    assert clear_factor(100) == pytest.approx(0.0)
    assert clear_factor(50) == pytest.approx(0.5 ** 1.5)


def test_clear_factor_punishes_cloud_superlinearly():
    """The ^1.5 exponent means half cover costs more than half the score."""
    assert clear_factor(50) < 0.5


def test_unknown_cloud_does_not_silently_zero_the_score():
    assert clear_factor(None) == pytest.approx(1.0)


def test_moon_is_irrelevant_to_planetary_observing():
    """PLAN.md 3.2: the Moon is either irrelevant or is the target."""
    assert moon_factor(1.0, 80.0, deep_sky=False) == pytest.approx(1.0)


def test_a_full_moon_high_up_wrecks_deep_sky():
    assert moon_factor(1.0, 80.0, deep_sky=True) < 0.3


def test_a_moon_below_the_horizon_costs_nothing():
    assert moon_factor(1.0, -5.0, deep_sky=True) == pytest.approx(1.0)
    assert moon_factor(1.0, 0.0, deep_sky=True) == pytest.approx(1.0)


def test_new_moon_costs_nothing_however_high():
    assert moon_factor(0.0, 89.0, deep_sky=True) == pytest.approx(1.0)


def test_moon_penalty_grows_with_altitude_and_illumination():
    low = moon_factor(0.8, 10.0, deep_sky=True)
    high = moon_factor(0.8, 80.0, deep_sky=True)
    assert high < low

    dim = moon_factor(0.3, 50.0, deep_sky=True)
    bright = moon_factor(0.9, 50.0, deep_sky=True)
    assert bright < dim


def test_seeing_dominates_planetary_and_barely_touches_deep_sky():
    """PLAN.md 3.2's central asymmetry."""
    bad_planetary = seeing_factor(0.2, deep_sky=False)
    bad_deep_sky = seeing_factor(0.2, deep_sky=True)

    # Dominant: poor air costs planets the full shortfall. This used to demand
    # under 0.15 from the old `q ** 1.5`; the quality scale now carries the
    # perceptual weighting itself, so the factor is the quality, unexponented.
    assert bad_planetary == pytest.approx(0.2)
    assert bad_deep_sky > 0.85           # mild
    assert bad_planetary < bad_deep_sky / 4


def test_perfect_seeing_is_full_marks_for_both():
    assert seeing_factor(1.0, deep_sky=True) == pytest.approx(1.0)
    assert seeing_factor(1.0, deep_sky=False) == pytest.approx(1.0)


def test_transparency_matters_far_more_for_deep_sky():
    """High cirrus kills galaxies, leaves planets workable."""
    poor_deep_sky = transparency_factor(0.2, None, None, deep_sky=True)
    poor_planetary = transparency_factor(0.2, None, None, deep_sky=False)

    assert poor_deep_sky == pytest.approx(0.2)
    assert 0.8 <= poor_planetary <= 1.0


def test_transparency_falls_back_to_high_cloud_and_humidity():
    clear = transparency_factor(None, 0.0, 40.0, deep_sky=True)
    cirrus = transparency_factor(None, 90.0, 40.0, deep_sky=True)
    humid = transparency_factor(None, 0.0, 95.0, deep_sky=True)

    assert clear > cirrus
    assert clear > humid


def test_wind_is_full_marks_when_calm_and_floors_when_gusty():
    assert wind_factor(5.0) == pytest.approx(1.0)
    assert wind_factor(15.0) == pytest.approx(1.0)
    assert wind_factor(50.0) == pytest.approx(0.4)
    assert 0.4 < wind_factor(28.0) < 1.0


def test_wind_factor_is_monotonic():
    values = [wind_factor(g) for g in range(0, 60, 5)]
    assert values == sorted(values, reverse=True)


def test_dew_warning_fires_inside_two_degrees():
    factor, warning = dew_factor(1.0)
    assert factor == pytest.approx(0.85)
    assert warning is True

    factor, warning = dew_factor(8.0)
    assert factor == pytest.approx(1.0)
    assert warning is False


def test_unknown_dew_spread_is_neutral():
    assert dew_factor(None) == (1.0, False)


# --- the multiplicative rule ------------------------------------------------

def test_one_dealbreaker_dominates_the_product():
    """PLAN.md 3.2: multiplicative so a single killer factor wins."""
    everything_else_perfect = FactorBreakdown(
        clear=0.05, transparency=1.0, moon=1.0, seeing=1.0, wind=1.0, dew=1.0,
    )
    assert everything_else_perfect.product < 0.06


def test_weakest_names_the_limiting_factor():
    breakdown = FactorBreakdown(clear=1.0, transparency=0.9, moon=0.35,
                                seeing=0.8, wind=1.0, dew=1.0)
    name, value = breakdown.weakest()
    assert name == "moon"
    assert value == pytest.approx(0.35)


def test_grade_bands():
    assert grade(95) == "A"
    assert grade(85) == "B"
    assert grade(75) == "C"
    assert grade(65) == "D"
    assert grade(20) == "F"


# --- slot scoring -----------------------------------------------------------

def test_slot_score_is_aware_utc():
    when = datetime(2026, 9, 16, 5, 0, tzinfo=timezone.utc)
    slot = score_slot(None, 0.0, -20.0, when)
    assert is_aware_utc(slot.time_utc)


def test_a_perfect_night_scores_near_100_for_both_modes():
    when = datetime(2026, 9, 16, 5, 0, tzinfo=timezone.utc)
    perfect = HourlyConditions(
        time_utc=when, cloud_cover=0.0, cloud_high=0.0, humidity_pct=30.0,
        temperature_c=15.0, dew_point_c=0.0, wind_gust_kmh=3.0,
        seeing_quality=1.0, transparency_quality=1.0,
    )
    slot = score_slot(perfect, 0.0, -30.0, when)
    assert slot.deep_sky > 99
    assert slot.planetary > 99


def test_a_cloudy_night_scores_near_zero_for_both():
    when = datetime(2026, 9, 16, 5, 0, tzinfo=timezone.utc)
    overcast = HourlyConditions(time_utc=when, cloud_cover=100.0)
    slot = score_slot(overcast, 0.0, -30.0, when)
    assert slot.deep_sky < 1
    assert slot.planetary < 1


def test_bright_moon_splits_the_two_scores():
    """The whole reason there are two numbers instead of one."""
    when = datetime(2026, 9, 16, 5, 0, tzinfo=timezone.utc)
    steady = HourlyConditions(
        time_utc=when, cloud_cover=0.0, cloud_high=0.0, humidity_pct=30.0,
        temperature_c=15.0, dew_point_c=0.0, wind_gust_kmh=3.0,
        seeing_quality=1.0, transparency_quality=1.0,
    )
    slot = score_slot(steady, illumination=1.0, moon_altitude_deg=70.0, when=when)

    assert slot.planetary > 95, "a steady bright-moon night is great for planets"
    assert slot.deep_sky < 40, "...and bad for galaxies"


# --- night aggregation ------------------------------------------------------

@requires_ephemeris
def test_score_night_produces_a_slot_per_half_hour(home):
    window = night_window(REFERENCE_DATE, home)
    score = score_night(window, None)

    expected = int(window.astronomical_night_hours * 2)
    assert abs(len(score.slots) - expected) <= 1
    assert all(is_aware_utc(s.time_utc) for s in score.slots)


@requires_ephemeris
def test_dark_hours_are_reported_separately_from_the_score(home):
    """PLAN.md 3.2: darkness duration is surfaced, not folded into the score."""
    window = night_window(REFERENCE_DATE, home)
    score = score_night(window, None)
    assert score.dark_hours == pytest.approx(window.dark_hours)


@requires_ephemeris
def test_best_window_is_at_least_an_hour_and_inside_the_night(home):
    window = night_window(REFERENCE_DATE, home)
    forecast = _clear_forecast(home, window.astronomical_night[0][0])
    score = score_night(window, forecast)

    assert score.best_window is not None
    start, end = score.best_window
    assert (end - start) >= timedelta(hours=1)
    assert start >= window.astronomical_night[0][0]


@requires_ephemeris
def test_peak_is_never_below_the_mean(home):
    window = night_window(REFERENCE_DATE, home)
    score = score_night(window, _clear_forecast(home, window.astronomical_night[0][0]))
    assert score.deep_sky_peak >= score.deep_sky_mean
    assert score.planetary_peak >= score.planetary_mean


# --- PLAN.md 7: no silent degradation ---------------------------------------

@requires_ephemeris
def test_without_weather_the_night_is_not_gradeable(home):
    """The key contract: no weather means no grade, not a clear-night grade."""
    window = night_window(REFERENCE_DATE, home)
    score = score_night(window, unavailable(home, "beyond horizon"))

    assert score.weather_available is False
    assert score.is_gradeable is False
    assert score.deep_sky_grade == "n/a"
    assert score.planetary_grade == "n/a"
    assert score.weather_note == "beyond horizon"


@requires_ephemeris
def test_without_weather_the_verdict_says_so(home):
    window = night_window(REFERENCE_DATE, home)
    score = score_night(window, unavailable(home, "network disabled"))
    text = verdict(score, window)

    assert "weather unavailable" in text.lower()
    assert "network disabled" in text


@requires_ephemeris
def test_with_weather_the_night_is_gradeable(home):
    window = night_window(REFERENCE_DATE, home)
    forecast = _clear_forecast(home, window.astronomical_night[0][0])
    score = score_night(window, forecast)

    assert score.is_gradeable is True
    assert score.deep_sky_grade in {"A", "B", "C", "D", "F"}


@requires_ephemeris
def test_score_night_never_raises_without_a_forecast(home):
    """The engine must run with no weather object at all."""
    window = night_window(REFERENCE_DATE, home)
    score = score_night(window, None)
    assert score.slots
    assert score.is_gradeable is False


@requires_ephemeris
def test_seeing_estimated_reflects_this_night_not_the_whole_forecast(home):
    """A 16-day forecast is mostly past the 7Timer horizon; that says nothing
    about tonight. The flag must describe the scored hours only."""
    window = night_window(REFERENCE_DATE, home)
    start = window.astronomical_night[0][0]

    # Real 7Timer data across this night, estimated only far in the future.
    tonight_hours = [
        HourlyConditions(time_utc=start + timedelta(hours=i), cloud_cover=0.0,
                         seeing_quality=0.9, transparency_quality=0.9,
                         seeing_estimated=False)
        for i in range(14)
    ]
    far_hours = [
        HourlyConditions(time_utc=start + timedelta(days=10, hours=i),
                         cloud_cover=0.0, seeing_quality=0.5,
                         transparency_quality=0.5, seeing_estimated=True)
        for i in range(6)
    ]
    forecast = Forecast(location=home, hours=tonight_hours + far_hours,
                        available=True, sources=("synthetic",),
                        seeing_estimated=True)     # true forecast-wide

    score = score_night(window, forecast)
    assert score.seeing_estimated is False, (
        "the flag must describe the scored night, not the 16-day forecast"
    )


# --- cloudy vs clear end to end ---------------------------------------------

@requires_ephemeris
def test_an_overcast_night_scores_far_below_a_clear_one(home):
    window = night_window(REFERENCE_DATE, home)
    start = window.astronomical_night[0][0]

    clear = score_night(window, _clear_forecast(home, start))
    overcast = score_night(window, _clear_forecast(home, start, cloud_cover=100.0))

    assert overcast.deep_sky_peak < clear.deep_sky_peak
    assert overcast.deep_sky_peak < 1
    assert overcast.deep_sky_grade == "F"


@requires_ephemeris
def test_a_dewy_night_raises_the_warning(home):
    window = night_window(REFERENCE_DATE, home)
    start = window.astronomical_night[0][0]
    score = score_night(window, _clear_forecast(home, start,
                                                temperature_c=12.0,
                                                dew_point_c=11.5))
    assert score.dew_warning is True


@requires_ephemeris
def test_verdict_names_the_limiting_factor(home):
    window = night_window(REFERENCE_DATE, home)
    start = window.astronomical_night[0][0]
    windy = score_night(window, _clear_forecast(home, start, wind_gust_kmh=55.0))
    assert "wind" in verdict(windy, window).lower()


# --- other latitudes --------------------------------------------------------

def _site(name, lat, lon, elevation=0, bortle=4):
    from engine.horizon import FLAT
    from engine.locations import Location, _resolve_tz

    return Location(key=name, name=name, lat=lat, lon=lon,
                    elevation_m=elevation, bortle=bortle,
                    tz=_resolve_tz(lat, lon), horizon=FLAT)


@requires_ephemeris
def test_polar_summer_is_not_gradeable():
    """Tromso in June: the sun never reaches -18, so there is no night.

    Reporting "0, grade F" would read as a bad night rather than no night —
    and the weather can be perfectly available, so the weather check alone
    does not catch this.
    """
    tromso = _site("tromso", 69.65, 18.96)
    window = night_window(date(2026, 6, 21), tromso)
    assert not window.astronomical_night

    score = score_night(window, _clear_forecast(tromso, now_utc()))
    assert score.slots == []
    assert score.is_gradeable is False
    assert score.deep_sky_grade == "n/a"
    assert "No astronomical night" in verdict(score, window)


@requires_ephemeris
def test_polar_winter_still_scores():
    """Longyearbyen in December: no sunrise, but plenty of night."""
    svalbard = _site("svalbard", 78.22, 15.65)
    window = night_window(date(2026, 12, 21), svalbard)

    assert window.sunrise_utc is None and window.sunset_utc is None
    assert window.astronomical_night_hours > 12

    score = score_night(window, _clear_forecast(
        svalbard, window.astronomical_night[0][0]))
    assert score.slots
    assert score.is_gradeable is True


@requires_ephemeris
@pytest.mark.parametrize(
    "name, lat, lon",
    [("sydney", -33.87, 151.21),
     ("quito", -0.18, -78.47),
     ("ushuaia", -54.80, -68.30)],
)
def test_southern_and_equatorial_sites_score_normally(name, lat, lon):
    site = _site(name, lat, lon)
    window = night_window(date(2026, 6, 21), site)
    score = score_night(window, _clear_forecast(
        site, window.astronomical_night[0][0]))

    assert score.slots
    assert score.is_gradeable is True
    assert 0.0 <= score.deep_sky_peak <= 100.0


# ---------------------------------------------------------------------------
# Describing the night, not its best half hour
#
# Everything that *described* a night used to be read off the single
# highest-scoring slot: the verdict, the limiting factor, the factor bars. The
# peak slot is by construction the least cloudy one, so on a night that is
# clear for an hour and socked in for five, the breakdown reported a clear
# factor near 1.00 next to an hourly row of 100% cloud, and the verdict blamed
# whatever came second. Cloud could never be named on exactly the nights where
# cloud was the whole story.
#
# The grade stays peak-based -- a short clear window is still worth going out
# for -- so these pin the description, not the number.
# ---------------------------------------------------------------------------

def _half_clouded_forecast(location, start, hours=12):
    """Clear for the first quarter of the night, overcast for the rest."""
    def hour(index: int) -> HourlyConditions:
        return HourlyConditions(
            time_utc=start + timedelta(hours=index),
            cloud_cover=0.0 if index < hours // 4 else 100.0,
            cloud_high=0.0, humidity_pct=40.0, temperature_c=15.0,
            dew_point_c=2.0, wind_gust_kmh=5.0,
            seeing_quality=1.0, transparency_quality=1.0,
        )
    return Forecast(location=location, hours=[hour(i) for i in range(hours)],
                    available=True, sources=("synthetic",))


def test_mean_of_averages_each_factor_independently():
    """Not the mean of the products. Averaging slot scores would smear a
    dealbreaker across the night, which is what the multiplication exists to
    prevent; this is for describing conditions, not for scoring them."""
    a = FactorBreakdown(1.0, 1.0, 1.0, 1.0, 1.0, 1.0)
    b = FactorBreakdown(0.0, 0.5, 1.0, 1.0, 1.0, 1.0)
    mean = FactorBreakdown.mean_of([a, b])
    assert mean.clear == pytest.approx(0.5)
    assert mean.transparency == pytest.approx(0.75)
    assert mean.moon == pytest.approx(1.0)


def test_mean_of_nothing_is_neutral():
    """A night with no slots must not divide by zero on the way to saying so."""
    assert FactorBreakdown.mean_of([]).clear == pytest.approx(1.0)


@requires_ephemeris
def test_the_night_average_sees_cloud_the_peak_slot_cannot(home):
    """The bug, stated directly.

    Three quarters of this night is overcast. The best slot is in the clear
    quarter, so its clear factor is 1.00 -- true of that slot and of nothing
    else. The night average has to disagree.
    """
    window = night_window(REFERENCE_DATE, home)
    start = window.astronomical_night[0][0]
    score = score_night(window, _half_clouded_forecast(home, start))

    peak = max(score.slots, key=lambda s: s.deep_sky)
    assert peak.deep_sky_factors.clear == pytest.approx(1.0)
    assert score.mean_factors_deep_sky.clear < 0.5


@requires_ephemeris
def test_the_verdict_blames_cloud_on_a_night_that_clouds_over(home):
    """Previously impossible: the peak slot is never the cloudy one."""
    window = night_window(REFERENCE_DATE, home)
    start = window.astronomical_night[0][0]
    score = score_night(window, _half_clouded_forecast(home, start))
    assert "cloud" in verdict(score, window).lower()


@requires_ephemeris
def test_the_verdict_says_how_short_the_good_part_is(home):
    """"Workable" describing one clear hour inside six of cloud is true and
    useless. When the best window is a small slice of the dark time, the
    length is the fact about the night."""
    window = night_window(REFERENCE_DATE, home)
    start = window.astronomical_night[0][0]
    score = score_night(window, _half_clouded_forecast(home, start))
    assert " h of " in verdict(score, window)


@requires_ephemeris
def test_a_uniformly_good_night_gets_no_window_qualifier(home):
    """The qualifier has to stay out of the way when it says nothing. A clear
    night's best window covers most of the dark time by definition."""
    window = night_window(REFERENCE_DATE, home)
    start = window.astronomical_night[0][0]
    score = score_night(window, _clear_forecast(home, start))
    assert " h of " not in verdict(score, window)


@requires_ephemeris
def test_an_even_night_reports_the_whole_night_as_the_best_window(home):
    """Found by the test above.

    A strict `>` kept the first window at the best mean, so a flawless
    eight-hour night reported a one-hour best window -- implying the other
    seven were worse, and dragging the verdict's "for 1.0 h of 7.8" qualifier
    in with it. When conditions are even, the best window is the night.
    """
    window = night_window(REFERENCE_DATE, home)
    start = window.astronomical_night[0][0]
    score = score_night(window, _clear_forecast(home, start, hours=16))

    assert score.best_window is not None
    best_start, best_end = score.best_window
    hours = (best_end - best_start).total_seconds() / 3600.0
    # The moon rises and sets, so conditions are not perfectly flat; the point
    # is that the window is a real span rather than the minimum two slots.
    assert hours > 2.0
