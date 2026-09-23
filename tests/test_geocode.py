"""Ground height for a site picked off the map.

No test here touches the network: the live call is replaced with a synthetic
response shaped like Open-Meteo's, and the offline path is exercised with
ASTRO_NO_NETWORK, as in test_weather.py.
"""

from __future__ import annotations

import io
import json

import pytest

from engine import geocode


@pytest.fixture(autouse=True)
def fresh_cache():
    geocode._elevation.cache_clear()
    yield
    geocode._elevation.cache_clear()


def _answer(monkeypatch, payload, calls=None):
    def fake_urlopen(request, timeout):
        if calls is not None:
            calls.append(request.full_url)
        return io.BytesIO(json.dumps(payload).encode("utf-8"))
    monkeypatch.delenv("ASTRO_NO_NETWORK", raising=False)
    monkeypatch.setattr(geocode.urllib.request, "urlopen", fake_urlopen)


def test_the_height_is_read_from_the_terrain_service(monkeypatch):
    calls = []
    _answer(monkeypatch, {"elevation": [1392.0]}, calls)
    assert geocode.elevation_at(40.2338, -111.6585) == 1392.0
    assert "latitude=40.2338" in calls[0] and "longitude=-111.6585" in calls[0]


def test_the_same_point_is_asked_about_once(monkeypatch):
    """Dragging a pin back and forth revisits the same few points."""
    calls = []
    _answer(monkeypatch, {"elevation": [1392.0]}, calls)
    geocode.elevation_at(40.23381, -111.65852)
    geocode.elevation_at(40.23379, -111.65849)   # the same point, to ~10 m
    assert len(calls) == 1


def test_offline_is_none_not_zero(monkeypatch):
    """0 m would be saved as though it were known -- the bug this replaces."""
    monkeypatch.setenv("ASTRO_NO_NETWORK", "1")
    assert geocode.elevation_at(40.0, -111.0) is None


@pytest.mark.parametrize("payload", [{}, {"elevation": []}, {"elevation": ["high"]},
                                     {"elevation": [float("nan")]}])
def test_an_answer_that_does_not_parse_is_none(monkeypatch, payload):
    _answer(monkeypatch, payload)
    assert geocode.elevation_at(40.0, -111.0) is None


def test_a_failed_request_is_none(monkeypatch):
    def refuse(request, timeout):
        raise OSError("unreachable")
    monkeypatch.delenv("ASTRO_NO_NETWORK", raising=False)
    monkeypatch.setattr(geocode.urllib.request, "urlopen", refuse)
    assert geocode.elevation_at(40.0, -111.0) is None


def test_the_api_passes_it_on(monkeypatch):
    testclient = pytest.importorskip("fastapi.testclient")
    from api.main import app

    _answer(monkeypatch, {"elevation": [2433.0]})
    client = testclient.TestClient(app)
    assert client.get("/api/elevation", params={"lat": 37.63, "lon": -112.17}).json() == \
        {"elevation_m": 2433.0}
    monkeypatch.setenv("ASTRO_NO_NETWORK", "1")
    assert client.get("/api/elevation", params={"lat": 1.0, "lon": 1.0}).json() == \
        {"elevation_m": None}
