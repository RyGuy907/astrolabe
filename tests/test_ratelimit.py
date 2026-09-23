"""Budgets and backoff for outbound calls, and the API's per-visitor limit.

What must hold: a flood aimed at the app never becomes a flood aimed at
Open-Meteo or 7Timer; a service that says "slow down" is left alone; a
failure is never remembered as an answer; and whoever is refused is told why.
"""

from __future__ import annotations

import email.message
import io
import json
import urllib.error

import pytest

from engine import geocode, ratelimit, weather
from engine.ratelimit import SlidingWindow, Upstream


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock()


# --- Upstream ---------------------------------------------------------------

def test_the_minute_budget_refuses_then_refills(clock):
    upstream = Upstream("Svc", per_minute=3, per_day=100, clock=clock)
    assert [upstream.acquire() for _ in range(4)] == [True, True, True, False]
    assert "this minute" in upstream.unavailable_reason()
    clock.now += 60
    assert upstream.acquire()


def test_the_day_budget_outlasts_the_minute(clock):
    upstream = Upstream("Svc", per_minute=10, per_day=12, clock=clock)
    for _ in range(12):
        assert upstream.acquire()
        clock.now += 30
    assert not upstream.acquire()
    assert "today's" in upstream.unavailable_reason()
    clock.now += 86400
    assert upstream.acquire()


def test_a_call_costs_what_it_weighs(clock):
    upstream = Upstream("Svc", per_minute=5, per_day=100, clock=clock)
    assert upstream.acquire(cost=2) and upstream.acquire(cost=2)
    assert not upstream.acquire(cost=2)
    assert upstream.acquire(cost=1)


def test_a_429_rests_the_service_as_long_as_it_asks(clock):
    upstream = Upstream("Svc", per_minute=60, per_day=1000, clock=clock)
    upstream.failed(status=429, retry_after="120")
    assert not upstream.acquire()
    assert "HTTP 429" in upstream.unavailable_reason()
    clock.now += 119
    assert not upstream.acquire()
    clock.now += 2
    assert upstream.acquire()


@pytest.mark.parametrize("retry_after, expected", [
    (None, ratelimit.DEFAULT_PAUSE_S),
    ("soon", ratelimit.DEFAULT_PAUSE_S),        # unparseable: the default
    ("5", ratelimit.MIN_PAUSE_S),               # too short to be polite
    ("999999", ratelimit.MAX_PAUSE_S),          # not a day's outage over one header
])
def test_the_pause_is_bounded(clock, retry_after, expected):
    upstream = Upstream("Svc", per_minute=60, per_day=1000, clock=clock)
    upstream.failed(status=503, retry_after=retry_after)
    clock.now += expected - 1
    assert not upstream.acquire()
    clock.now += 2
    assert upstream.acquire()


def test_three_failures_in_a_row_rest_it_and_a_success_forgives(clock):
    upstream = Upstream("Svc", per_minute=60, per_day=1000, clock=clock)
    upstream.failed()
    upstream.failed()
    upstream.succeeded()
    upstream.failed()
    upstream.failed()
    assert upstream.acquire()                   # two since the success
    upstream.failed()
    assert not upstream.acquire()
    assert "3 times in a row" in upstream.unavailable_reason()
    clock.now += ratelimit.FAILURE_PAUSE_S + 1
    assert upstream.acquire()


def test_a_bad_request_is_not_held_against_the_service(clock):
    upstream = Upstream("Svc", per_minute=60, per_day=1000, clock=clock)
    for _ in range(5):
        upstream.failed(status=400)
    assert upstream.acquire()
    assert upstream.unavailable_reason() is None


# --- SlidingWindow ----------------------------------------------------------

def test_the_window_limits_each_key_alone(clock):
    window = SlidingWindow(limit=2, window=60, clock=clock)
    assert window.hit("a") is None and window.hit("a") is None
    assert window.hit("a") == pytest.approx(60)
    assert window.hit("b") is None
    clock.now += 30
    assert window.hit("a") == pytest.approx(30)
    clock.now += 31
    assert window.hit("a") is None


def test_a_flood_of_addresses_cannot_grow_the_table(clock):
    window = SlidingWindow(limit=5, window=60, max_keys=100, clock=clock)
    for i in range(1000):
        window.hit(f"10.0.{i // 256}.{i % 256}")
    assert len(window._hits) <= 100


# --- the networked modules --------------------------------------------------

def _http_error(status: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://example.invalid", status, "no", headers, None)


def _serve(monkeypatch, answers):
    """Fake the network: each call takes the next answer -- a payload, or an
    exception to raise. Returns the list of URLs asked for."""
    asked: list[str] = []
    queue = list(answers)

    def fake_urlopen(request, timeout):
        asked.append(request.full_url)
        answer = queue.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return io.BytesIO(json.dumps(answer).encode("utf-8"))

    monkeypatch.delenv("ASTRO_NO_NETWORK", raising=False)
    monkeypatch.setattr(weather.urllib.request, "urlopen", fake_urlopen)
    return asked


def test_after_a_429_open_meteo_is_not_asked_again(monkeypatch, home):
    asked = _serve(monkeypatch, [_http_error(429, "600")])
    forecast = weather.get_forecast(home, use_cache=False)
    assert len(asked) == 1
    assert not forecast.available

    again = weather.get_forecast(home, use_cache=False)
    assert len(asked) == 1                      # rested: no second request
    assert "asked for fewer requests" in again.unavailable_reason


def test_the_backoff_is_shared_by_every_open_meteo_endpoint(monkeypatch, home):
    asked = _serve(monkeypatch, [_http_error(429)])
    weather.get_forecast(home, use_cache=False)
    assert geocode.search("Moab") == []
    assert geocode.elevation_at(38.57, -109.55) is None
    assert len(asked) == 1


def test_a_spent_budget_says_so_rather_than_unreachable(monkeypatch, home):
    _serve(monkeypatch, [])
    monkeypatch.setattr(ratelimit.OPEN_METEO, "per_day", 0)
    forecast = weather.get_forecast(home, use_cache=False)
    assert not forecast.available
    assert "budget" in forecast.unavailable_reason and "unreachable" not in forecast.unavailable_reason


def test_a_failed_elevation_is_not_remembered(monkeypatch):
    asked = _serve(monkeypatch, [urllib.error.URLError("down"), {"elevation": [1392.0]}])
    assert geocode.elevation_at(40.0, -111.0) is None
    assert geocode.elevation_at(40.0, -111.0) == 1392.0
    assert geocode.elevation_at(40.0, -111.0) == 1392.0
    assert len(asked) == 2                      # the success is cached, the failure was not


def test_a_place_search_is_cached_whatever_its_spacing_or_case(monkeypatch):
    moab = {"results": [{"name": "Moab", "latitude": 38.57, "longitude": -109.55,
                         "elevation": 1227, "country": "United States", "admin1": "Utah"}]}
    asked = _serve(monkeypatch, [moab])
    assert geocode.search("Moab")[0].name == "Moab"
    assert geocode.search("  moab ")[0].name == "Moab"
    assert geocode.search("MOAB")[0].lat == 38.57
    assert len(asked) == 1


def test_a_failed_search_is_not_remembered(monkeypatch):
    asked = _serve(monkeypatch, [urllib.error.URLError("down"), {"results": []}])
    assert geocode.search("Moab") == []
    assert geocode.search("Moab") == []
    assert len(asked) == 2


# --- the API's per-visitor limit --------------------------------------------

fastapi_testclient = pytest.importorskip("fastapi.testclient")


@pytest.fixture
def client(monkeypatch):
    from api.main import app

    monkeypatch.setenv("ASTRO_NO_NETWORK", "1")     # answers come back at once
    return fastapi_testclient.TestClient(app)


def test_one_visitor_is_told_to_wait(client):
    from api import main

    limit = main._REACHING_OUT.limit
    for _ in range(limit):
        assert client.get("/api/elevation", params={"lat": 40, "lon": -111}).status_code == 200
    refused = client.get("/api/elevation", params={"lat": 40, "lon": -111})
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) >= 1
    assert "Too many requests" in refused.json()["detail"]
    # Only what reaches out is held back so soon; the rest has its own limit.
    assert client.get("/api/skybrightness/at", params={"lat": 40, "lon": -111}).status_code == 200


def test_the_health_check_is_never_limited(client):
    from api import main

    for _ in range(main._EVERY_REQUEST.limit + 5):
        assert client.get("/api/health").status_code == 200


def test_an_ipv6_household_counts_as_one(monkeypatch):
    from starlette.requests import Request

    from api.main import _visitor

    def visitor(host):
        return _visitor(Request({"type": "http", "client": (host, 1234), "headers": []}))

    assert visitor("2001:db8:1:2::1") == visitor("2001:db8:1:2:ffff::9")
    assert visitor("2001:db8:1:2::1") != visitor("2001:db8:1:3::1")
    assert visitor("203.0.113.7") == "203.0.113.7"
