"""Weather clients: index inversion, horizons, caching, graceful degradation.

No test here touches the network. Live calls are blocked with ASTRO_NO_NETWORK
and every parser is exercised against a synthetic payload shaped like the real
one, so the suite is deterministic and runs offline.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from engine.locations import get_location
from engine.timeutil import is_aware_utc, now_utc
from engine.weather import (
    OPEN_METEO_HORIZON,
    SEVENTIMER_HORIZON,
    Forecast,
    HourlyConditions,
    blend,
    cloudcover_index_to_percent,
    estimate_seeing_quality,
    get_forecast,
    parse_7timer,
    parse_open_meteo,
    seeing_arcsec_to_quality,
    seeing_index_to_quality,
    transparency_index_to_quality,
    unavailable,
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Hard-block the network for every test in this module."""
    monkeypatch.setenv("ASTRO_NO_NETWORK", "1")


@pytest.fixture
def home():
    return get_location("home")


# --- PLAN.md 7: 7Timer index inversion --------------------------------------
# "Lower = better for seeing/transparency/cloud. Easy to get backwards and it
# silently produces plausible-looking garbage."
#
# EXTERNAL SOURCE, verified 2026-08-25 against https://www.7timer.info/doc.php
# (the 7Timer ASTRO product documentation), quoted values:
#
#   cloudcover    1 = "0%-6%"  ...  9 = "94%-100%"
#   seeing        1 = "<0.5\"" ...  8 = ">2.5\""
#                 doc: "the smaller/bluer, the better the seeing condition is"
#   transparency  1 = "<0.3"   ...  8 = ">1"   (mag per air mass)
#                 doc: "the fewer bars/bluer, the better the transparency is"
#
# All three run lower = better. The assertions below encode that direction, so
# an inverted conversion fails loudly rather than producing a plausible score.
#
# Cross-source confirmation, same date: 7Timer's converted cloud cover and
# Open-Meteo's direct percentage agreed to a mean 2.7 percentage points over
# the next 7 matched samples at Agoura Hills. An inverted scale would have
# reported clear skies as overcast, so this is independent evidence of the
# direction on top of the documentation.

def test_seeing_index_1_is_the_best_possible():
    """Index 1 means sub-0.5 arcsec seeing — the best there is."""
    assert seeing_index_to_quality(1) == pytest.approx(1.0)


def test_seeing_index_8_is_the_worst_but_not_worthless():
    """Index 8 means over 2.5 arcsec -- the worst class 7Timer forecasts.

    It is the lowest quality on the scale, but it is not zero. This asserted
    0.0 until the scale was recalibrated: >2.5" is an ordinary night at most
    backyard sites, and scoring it as worthless sent the planetary score to
    nothing on nights when Jupiter's belts are plainly visible.
    """
    worst = seeing_index_to_quality(8)
    assert worst == min(seeing_index_to_quality(i) for i in range(1, 9))
    assert 0.3 < worst < 0.6


def test_good_planetary_seeing_scores_near_full_marks():
    """Class 4, 1 to 1.25", is a good night for a backyard telescope.

    The old linear scale put it at 0.57, which the planetary score then
    raised to the power 1.5 -- a factor of 0.43 on a night worth observing.
    """
    assert seeing_index_to_quality(4) > 0.9


def test_seeing_quality_passes_through_its_arcsecond_anchors():
    assert seeing_arcsec_to_quality(0.8) == pytest.approx(1.0)
    assert seeing_arcsec_to_quality(1.0) == pytest.approx(1.0)
    assert seeing_arcsec_to_quality(2.0) == pytest.approx(0.7)
    assert seeing_arcsec_to_quality(3.0) == pytest.approx(0.4)
    # Terrible, but never zero.
    assert seeing_arcsec_to_quality(10.0) == pytest.approx(0.15)


def test_seeing_quality_decreases_as_the_index_rises():
    qualities = [seeing_index_to_quality(i) for i in range(1, 9)]
    assert qualities == sorted(qualities, reverse=True), (
        "seeing quality must fall as the 7Timer index rises — the inversion "
        "is backwards"
    )


def test_transparency_index_1_is_best():
    assert transparency_index_to_quality(1) == pytest.approx(1.0)
    assert transparency_index_to_quality(8) == pytest.approx(0.0)
    assert transparency_index_to_quality(2) > transparency_index_to_quality(7)


def test_cloudcover_index_1_is_clear_and_9_is_overcast():
    assert cloudcover_index_to_percent(1) == pytest.approx(0.0)
    assert cloudcover_index_to_percent(9) == pytest.approx(100.0)
    assert cloudcover_index_to_percent(5) == pytest.approx(50.0)


def test_indices_are_clamped_not_extrapolated():
    """Out-of-range indices clamp rather than producing negative quality."""
    assert seeing_index_to_quality(0) == pytest.approx(1.0)
    assert seeing_index_to_quality(99) == pytest.approx(seeing_index_to_quality(8))
    assert cloudcover_index_to_percent(-3) == pytest.approx(0.0)


def test_missing_index_is_none_not_zero():
    """None means unknown. Zero would mean 'worst possible', a real value."""
    assert seeing_index_to_quality(None) is None
    assert transparency_index_to_quality(None) is None
    assert cloudcover_index_to_percent(None) is None


# --- parsers ----------------------------------------------------------------

SEVENTIMER_PAYLOAD = {
    "init": "2026082512",
    "dataseries": [
        {"timepoint": 3, "cloudcover": 1, "seeing": 2, "transparency": 3},
        {"timepoint": 6, "cloudcover": 9, "seeing": 7, "transparency": 8},
    ],
}


def test_parse_7timer_converts_indices_and_offsets():
    hours = parse_7timer(SEVENTIMER_PAYLOAD)
    assert len(hours) == 2

    first, second = hours
    assert first.time_utc == datetime(2026, 8, 25, 15, tzinfo=timezone.utc)
    assert second.time_utc == datetime(2026, 8, 25, 18, tzinfo=timezone.utc)

    # First sample is a good night, second is a bad one.
    assert first.cloud_cover == pytest.approx(0.0)
    assert first.seeing_quality > second.seeing_quality
    assert first.transparency_quality > second.transparency_quality
    assert second.cloud_cover == pytest.approx(100.0)


def test_parse_7timer_survives_a_broken_payload():
    assert parse_7timer({}) == []
    assert parse_7timer({"init": "not-a-date", "dataseries": []}) == []
    assert parse_7timer({"init": "2026082512", "dataseries": [{}]}) == []


OPEN_METEO_PAYLOAD = {
    "hourly": {
        "time": ["2026-08-25T20:00", "2026-08-25T21:00"],
        "cloud_cover": [10, 80],
        "cloud_cover_low": [0, 20],
        "cloud_cover_mid": [5, 30],
        "cloud_cover_high": [5, 60],
        "temperature_2m": [18.0, 16.0],
        "dew_point_2m": [9.0, 15.5],
        "relative_humidity_2m": [55, 90],
        "wind_speed_10m": [8, 20],
        "wind_gusts_10m": [12, 45],
        "visibility": [24000, 8000],
        "precipitation_probability": [0, 40],
        "is_day": [0, 0],
    }
}


def test_parse_open_meteo_reads_every_field():
    hours = parse_open_meteo(OPEN_METEO_PAYLOAD)
    assert len(hours) == 2

    first = hours[0]
    assert first.time_utc == datetime(2026, 8, 25, 20, tzinfo=timezone.utc)
    assert first.cloud_cover == 10
    assert first.cloud_high == 5
    assert first.temperature_c == 18.0
    assert first.dew_point_c == 9.0
    assert first.wind_gust_kmh == 12
    assert first.dew_point_spread_c == pytest.approx(9.0)


def test_dew_point_spread_flags_a_fogging_night():
    """Second sample is 16.0 C air over a 15.5 C dew point — 0.5 C spread."""
    second = parse_open_meteo(OPEN_METEO_PAYLOAD)[1]
    assert second.dew_point_spread_c == pytest.approx(0.5)


def test_parse_open_meteo_survives_missing_columns():
    hours = parse_open_meteo({"hourly": {"time": ["2026-08-25T20:00"]}})
    assert len(hours) == 1
    assert hours[0].cloud_cover is None


def test_parse_open_meteo_survives_an_empty_payload():
    assert parse_open_meteo({}) == []


# --- timezone boundary ------------------------------------------------------

def test_parsed_times_are_aware_utc():
    for hour in parse_open_meteo(OPEN_METEO_PAYLOAD) + parse_7timer(SEVENTIMER_PAYLOAD):
        assert is_aware_utc(hour.time_utc)


def test_hourly_conditions_rejects_a_naive_datetime():
    from engine.timeutil import NaiveDatetimeError

    with pytest.raises(NaiveDatetimeError):
        HourlyConditions(time_utc=datetime(2026, 8, 25, 20, 0))


# --- blending and horizons --------------------------------------------------

def _meteo_hours(count: int, start: datetime) -> list[HourlyConditions]:
    return [
        HourlyConditions(
            time_utc=start + timedelta(hours=i),
            cloud_cover=20.0, humidity_pct=50.0, wind_gust_kmh=10.0,
            temperature_c=15.0, dew_point_c=5.0,
        )
        for i in range(count)
    ]


def test_blend_takes_seeing_from_7timer_inside_the_horizon(home):
    reference = now_utc()
    meteo = _meteo_hours(3, reference)
    astro = [HourlyConditions(time_utc=reference, seeing_quality=0.9,
                              transparency_quality=0.8)]

    forecast = blend(home, meteo, astro, reference=reference)
    assert forecast.available
    assert forecast.hours[0].seeing_quality == pytest.approx(0.9)
    assert forecast.hours[0].transparency_quality == pytest.approx(0.8)
    assert forecast.hours[0].seeing_estimated is False
    assert "7Timer" in forecast.sources


def test_blend_estimates_seeing_past_the_7timer_horizon(home):
    """PLAN.md 2: fall back to a proxy and label it estimated."""
    reference = now_utc()
    far = reference + SEVENTIMER_HORIZON + timedelta(hours=6)
    meteo = _meteo_hours(1, far)

    forecast = blend(home, meteo, [], reference=reference)
    assert forecast.available
    assert forecast.hours[0].seeing_estimated is True
    assert forecast.seeing_estimated is True


def test_blend_keeps_open_meteo_cloud_over_7timer(home):
    """PLAN.md 2: cloud comes from Open-Meteo, which has better resolution."""
    reference = now_utc()
    meteo = _meteo_hours(1, reference)                     # 20% cloud
    astro = [HourlyConditions(time_utc=reference, cloud_cover=100.0,
                              seeing_quality=0.5, transparency_quality=0.5)]

    forecast = blend(home, meteo, astro, reference=reference)
    assert forecast.hours[0].cloud_cover == pytest.approx(20.0)


def test_blend_with_no_open_meteo_is_unavailable(home):
    forecast = blend(home, [], [], reference=now_utc())
    assert forecast.available is False
    assert "Open-Meteo" in forecast.unavailable_reason


def test_estimated_seeing_falls_with_humidity_and_wind():
    calm_dry = estimate_seeing_quality(40.0, 5.0)
    humid = estimate_seeing_quality(95.0, 5.0)
    gusty = estimate_seeing_quality(40.0, 50.0)

    assert calm_dry > humid
    assert calm_dry > gusty
    assert all(0.0 <= q <= 1.0 for q in (calm_dry, humid, gusty))


def test_estimated_seeing_is_none_without_inputs():
    assert estimate_seeing_quality(None, None) is None


# --- PLAN.md 7: forecast horizon mismatch -----------------------------------

def test_a_date_past_the_open_meteo_horizon_is_explicitly_unavailable(home):
    """Must be an explicit no-forecast state, not a degraded score."""
    far = now_utc() + OPEN_METEO_HORIZON + timedelta(days=5)
    forecast = get_forecast(home, start=far, end=far + timedelta(hours=8))

    assert forecast.available is False
    assert "horizon" in forecast.unavailable_reason
    assert forecast.hours == []


def test_a_past_date_is_explicitly_unavailable(home):
    old = now_utc() - timedelta(days=30)
    forecast = get_forecast(home, start=old, end=old + timedelta(hours=8))
    assert forecast.available is False
    assert "past" in forecast.unavailable_reason


# --- graceful degradation ---------------------------------------------------

def test_no_network_yields_unavailable_not_an_exception(home):
    """PLAN.md 1: degrade to "unknown" rather than crash."""
    forecast = get_forecast(home)
    assert isinstance(forecast, Forecast)
    assert forecast.available is False
    assert "network" in forecast.unavailable_reason.lower()


def test_unavailable_forecast_is_still_a_usable_object(home):
    forecast = unavailable(home, "testing")
    assert forecast.hours == []
    assert forecast.at(now_utc()) is None
    assert forecast.covering(now_utc(), now_utc() + timedelta(hours=1)) == []
    assert forecast.covers(now_utc(), now_utc() + timedelta(hours=1)) is False


# --- coverage semantics -----------------------------------------------------

def test_covers_accepts_a_window_shorter_than_the_sample_interval(home):
    """A 6-minute true dark window contains no hourly sample but is covered.

    This is the bug that made `planner tonight` report "forecast does not
    cover that window" for tonight under a bright moon.
    """
    start = now_utc().replace(minute=0, second=0, microsecond=0)
    forecast = blend(home, _meteo_hours(12, start), [], reference=start)

    tiny_start = start + timedelta(hours=3, minutes=12)
    tiny_end = tiny_start + timedelta(minutes=6)

    assert forecast.covering(tiny_start, tiny_end) == []      # no sample inside
    assert forecast.covers(tiny_start, tiny_end) is True      # but still covered


def test_covers_rejects_a_window_beyond_the_forecast(home):
    start = now_utc().replace(minute=0, second=0, microsecond=0)
    forecast = blend(home, _meteo_hours(6, start), [], reference=start)

    beyond = start + timedelta(days=3)
    assert forecast.covers(beyond, beyond + timedelta(hours=2)) is False


def test_at_returns_the_nearest_sample(home):
    start = now_utc().replace(minute=0, second=0, microsecond=0)
    forecast = blend(home, _meteo_hours(6, start), [], reference=start)

    nearest = forecast.at(start + timedelta(hours=2, minutes=20))
    assert nearest.time_utc == start + timedelta(hours=2)
