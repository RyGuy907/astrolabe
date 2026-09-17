"""HTTP layer: response shape, the UTC contract, and error handling.

The API must stay a thin adapter (PLAN.md §4), so these tests check that it
faithfully exposes what the engine computed — not that the astronomy is right,
which the engine tests already cover.

No test here reaches the network: ASTRO_NO_NETWORK is set for the module, so
weather and geocoding take their degraded paths.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from conftest import requires_ephemeris

# fastapi is a declared runtime dependency and httpx a declared `[dev]` one,
# so a missing TestClient means a broken install, not an absent optional
# feature. This used to be an `importorskip`, which silently removed all 42
# tests in this file behind a single skip line while the suite exited 0.
fastapi_testclient = pytest.importorskip(
    "fastapi.testclient",
    reason="fastapi/httpx missing — install with `pip install -e \".[dev]\"`; "
           "the API tests cannot run and this is NOT a passing suite",
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setenv("ASTRO_NO_NETWORK", "1")


@pytest.fixture(scope="module")
def client():
    from api.main import app

    return fastapi_testclient.TestClient(app)


def _is_utc_iso(value: str) -> bool:
    """Parses, and carries an explicit UTC offset."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(None)


# --- meta -------------------------------------------------------------------

def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_openapi_schema_is_generated(client):
    """FastAPI's auto docs are a stated reason for the framework choice."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    for path in ("/api/night", "/api/targets", "/api/planets", "/api/events",
                 "/api/locations"):
        assert path in paths, f"{path} missing from the OpenAPI schema"


# --- night ------------------------------------------------------------------

@requires_ephemeris
def test_night_response_shape(client):
    response = client.get("/api/night?date=2026-09-15&location=home")
    assert response.status_code == 200

    body = response.json()
    window, score = body["window"], body["score"]

    assert window["date"] == "2026-09-15"
    assert window["location"]["key"] == "home"
    assert window["dark_hours"] == pytest.approx(7.84, abs=0.05)
    assert window["astronomical_night_hours"] > window["dark_hours"]
    assert 0.0 <= window["moon_illumination"] <= 1.0
    assert score["verdict"]


@requires_ephemeris
def test_all_night_datetimes_are_utc(client):
    """The API never emits local times — the browser converts."""
    body = client.get("/api/night?date=2026-09-15&location=home").json()
    window = body["window"]

    for field in ("sunset", "sunrise", "civil_dusk", "astronomical_dusk",
                  "astronomical_dawn", "moonrise", "moonset"):
        if window[field] is not None:
            assert _is_utc_iso(window[field]), f"{field} is not UTC: {window[field]}"

    for span in window["astronomical_night"] + window["dark_intervals"]:
        assert _is_utc_iso(span["start"]) and _is_utc_iso(span["end"])

    for slot in body["score"]["slots"]:
        assert _is_utc_iso(slot["time"])


@requires_ephemeris
def test_night_carries_the_timezone_for_the_display_layer(client):
    body = client.get("/api/night?date=2026-09-15&location=home").json()
    assert body["window"]["location"]["timezone"] == "America/Los_Angeles"


@requires_ephemeris
def test_without_weather_the_response_says_it_is_not_gradeable(client):
    """PLAN.md §7's no-silent-degradation rule, carried over HTTP."""
    body = client.get("/api/night?date=2026-09-15&location=home").json()
    score = body["score"]

    assert score["weather_available"] is False
    assert score["is_gradeable"] is False
    assert score["deep_sky_grade"] == "n/a"
    assert score["planetary_grade"] == "n/a"
    assert score["weather_note"]


# --- targets ----------------------------------------------------------------

@requires_ephemeris
def test_targets_response_shape(client):
    response = client.get("/api/targets?date=2026-09-15&location=home&limit=3")
    assert response.status_code == 200

    body = response.json()
    assert body["total_passing"] > 100
    assert "Galaxies" in body["groups"]
    assert all(len(v) <= 3 for v in body["groups"].values())


@requires_ephemeris
def test_targets_still_rank_m31_first(client):
    """The Phase 1 acceptance check, surfaced through HTTP.

    Deliberately against `santa_monica_mtns` (Bortle 4) rather than the
    default site. The API resolves locations from config, and the shipped
    default is now Griffith Observatory at Bortle 8 -- an inner-city sky where
    M31's surface brightness genuinely fails the contrast test. Asserting it
    ranks first there would be asserting the physics is wrong.
    """
    body = client.get(
        "/api/targets?date=2026-09-15&location=santa_monica_mtns"
    ).json()
    assert body["groups"]["Galaxies"][0]["messier"] == 31


@requires_ephemeris
def test_a_city_sky_rejects_m31(client):
    """The other half of that: from Bortle 8, M31 should not be recommended.

    This is the light-pollution model doing its job, and it is worth pinning
    explicitly rather than leaving as an unexamined consequence.
    """
    body = client.get("/api/targets?date=2026-09-15&location=home").json()
    galaxies = body["groups"].get("Galaxies", [])
    assert 31 not in [t["messier"] for t in galaxies]


@requires_ephemeris
def test_targets_exclude_m42_in_september(client):
    body = client.get("/api/targets?date=2026-09-15&location=home").json()
    nebulae = [t["messier"] for t in body["groups"].get("Nebulae", [])]
    assert 42 not in nebulae


@requires_ephemeris
def test_targets_group_filter(client):
    body = client.get(
        "/api/targets?date=2026-09-15&location=home&groups=Galaxies").json()
    assert set(body["groups"]) == {"Galaxies"}


@requires_ephemeris
def test_targets_surface_the_horizon_warning(client):
    """The generic-preset caveat must reach the UI, not just the CLI."""
    body = client.get("/api/targets?date=2026-09-15&location=home").json()
    warning = body["horizon_warning"]
    assert warning is not None
    assert "GENERIC" in warning


@requires_ephemeris
def test_target_times_are_utc(client):
    body = client.get("/api/targets?date=2026-09-15&location=home&limit=2").json()
    for targets in body["groups"].values():
        for target in targets:
            assert _is_utc_iso(target["peak_time"])
            assert _is_utc_iso(target["best_window"]["start"])


# --- planets ----------------------------------------------------------------

@requires_ephemeris
def test_planets_response_shape(client):
    response = client.get(
        "/api/planets?date=2026-09-15&location=home&find_next=false")
    assert response.status_code == 200

    planets = response.json()["planets"]
    assert len(planets) == 7
    saturn = next(p for p in planets if p["name"] == "saturn")
    assert saturn["observable"] is True
    assert saturn["ring_tilt_deg"] is not None
    assert saturn["event_date"] == "2026-10-04"


@requires_ephemeris
def test_only_saturn_has_a_ring_tilt(client):
    planets = client.get(
        "/api/planets?date=2026-09-15&location=home&find_next=false").json()["planets"]
    for planet in planets:
        if planet["name"] != "saturn":
            assert planet["ring_tilt_deg"] is None


# --- events -----------------------------------------------------------------

@requires_ephemeris
def test_events_response_shape(client):
    response = client.get("/api/events?from=2026-09-15&days=90&location=home")
    assert response.status_code == 200

    body = response.json()
    names = [s["name"] for s in body["showers"]]
    assert "Geminids" in names and "Orionids" in names
    assert body["conjunctions"]


@requires_ephemeris
def test_events_conjunctions_respect_thresholds(client):
    body = client.get("/api/events?from=2026-09-15&days=90").json()
    for conjunction in body["conjunctions"]:
        limit = 3.0 if conjunction["involves_moon"] else 5.0
        assert conjunction["separation_deg"] <= limit


# --- altitude chart ---------------------------------------------------------

@requires_ephemeris
def test_altitude_series_for_planets_and_objects(client):
    response = client.get(
        "/api/altitude?date=2026-09-15&location=home"
        "&bodies=moon,saturn&objects=M31")
    assert response.status_code == 200

    body = response.json()
    labels = [s["label"] for s in body["series"]]
    assert "moon" in labels and "saturn" in labels
    assert any("M31" in label for label in labels)

    for series in body["series"]:
        assert series["points"]
        assert all(_is_utc_iso(p["time"]) for p in series["points"])
        assert all(-90.0 <= p["altitude_deg"] <= 90.0 for p in series["points"])


@requires_ephemeris
def test_altitude_includes_the_shading_bands(client):
    """The chart needs the night and dark spans to shade the background."""
    body = client.get("/api/altitude?date=2026-09-15&location=home").json()
    assert body["astronomical_night"]
    assert body["dark_intervals"]
    assert body["horizon_at_azimuth"]


@requires_ephemeris
def test_altitude_rejects_an_unknown_body(client):
    response = client.get("/api/altitude?date=2026-09-15&bodies=vulcan")
    assert response.status_code == 422


@requires_ephemeris
def test_altitude_rejects_an_unknown_object(client):
    response = client.get("/api/altitude?date=2026-09-15&objects=NGC999999")
    assert response.status_code == 404



# --- locations --------------------------------------------------------------

def test_locations_list(client):
    body = client.get("/api/locations").json()
    keys = {loc["key"] for loc in body}
    assert {"home", "santa_monica_mtns"} <= keys

    home = next(loc for loc in body if loc["key"] == "home")
    assert home["horizon_name"] == "hilly"
    assert home["horizon_is_generic"] is True


def test_unknown_location_is_404_and_lists_the_known_keys(client):
    response = client.get("/api/night?location=atlantis")
    assert response.status_code == 404
    assert "home" in response.json()["detail"]


def test_bad_date_is_422(client):
    response = client.get("/api/night?date=not-a-date")
    assert response.status_code == 422


def test_create_location_from_coordinates(client):
    payload = {"key": "api_test_site", "name": "API Test Site",
               "lat": 36.6, "lon": -118.06, "elevation_m": 1130,
               "bortle": 2, "horizon": "ridge"}
    response = client.post("/api/locations", json=payload)
    assert response.status_code == 201

    body = response.json()
    assert body["key"] == "api_test_site"
    assert body["timezone"] == "America/Los_Angeles"      # resolved, not supplied
    assert body["horizon_name"] == "ridge"
    assert body["source"] == "stored"

    assert client.delete("/api/locations/api_test_site").status_code == 204


def test_created_location_is_immediately_usable(client):
    client.post("/api/locations", json={"key": "api_usable", "name": "Usable",
                                        "lat": 36.6, "lon": -118.06})
    try:
        response = client.get("/api/night?location=api_usable&date=2026-09-15")
        assert response.status_code == 200
        assert response.json()["window"]["dark_hours"] > 0
    finally:
        client.delete("/api/locations/api_usable")


def test_skybrightness_coverage_reports_whether_an_atlas_is_configured(client):
    """The map picker opens on the atlas's coverage, so it has to ask.

    Shape only: whether a raster is present depends on the machine, and the
    point of the endpoint is that both answers are valid.
    """
    body = client.get("/api/skybrightness").json()
    assert isinstance(body["configured"], bool)

    if body["configured"]:
        west, south, east, north = body["bounds"]
        assert -180 <= west < east <= 180
        assert -90 <= south < north <= 90
    else:
        assert body["bounds"] is None


def test_create_location_with_a_directional_horizon(client):
    """The bearing has to survive the API and the store, not just the engine."""
    payload = {"key": "api_facing_site", "name": "Facing Site",
               "lat": 36.6, "lon": -118.06, "bortle": 3,
               "horizon": "ridge", "horizon_facing": 250}
    response = client.post("/api/locations", json=payload)
    assert response.status_code == 201

    body = response.json()
    assert body["horizon_name"] == "ridge"
    assert body["horizon_facing"] == 250
    assert body["horizon_is_generic"] is True

    try:
        # and it is still there after a reload from SQLite
        listed = next(loc for loc in client.get("/api/locations").json()
                      if loc["key"] == "api_facing_site")
        assert listed["horizon_facing"] == 250
        assert listed["horizon_name"] == "ridge"
    finally:
        client.delete("/api/locations/api_facing_site")


def test_horizon_binds_says_whether_a_profile_can_change_anything(client):
    """A profile below the altitude floor is inert. The API says so rather
    than leaving the UI to rediscover it."""
    body = client.get("/api/locations").json()
    home = next(loc for loc in body if loc["key"] == "home")
    # home uses `hilly`, which tops out at 12 deg under a 25 deg floor
    assert home["horizon_binds"] is False

    client.post("/api/locations", json={"key": "api_binding_site",
                                        "name": "Binding", "lat": 36.6,
                                        "lon": -118.06, "horizon": "ridge"})
    try:
        listed = next(loc for loc in client.get("/api/locations").json()
                      if loc["key"] == "api_binding_site")
        assert listed["horizon_binds"] is True
    finally:
        client.delete("/api/locations/api_binding_site")


def test_an_out_of_range_bearing_is_rejected(client):
    response = client.post("/api/locations",
                           json={"key": "api_bad_bearing", "name": "Bad",
                                 "lat": 36.6, "lon": -118.06,
                                 "horizon": "ridge", "horizon_facing": 400})
    assert response.status_code == 422


def test_create_location_without_coordinates_or_query_is_422(client):
    assert client.post("/api/locations", json={"name": "Nowhere"}).status_code == 422


def test_geocoding_offline_returns_empty_not_an_error(client):
    """Graceful degradation, same contract as weather."""
    response = client.get("/api/geocode?q=Lone Pine")
    assert response.status_code == 200
    assert response.json() == []


def test_create_by_query_while_offline_is_404_not_a_crash(client):
    response = client.post("/api/locations", json={"query": "Lone Pine"})
    assert response.status_code == 404
    assert "unreachable" in response.json()["detail"]


def test_yaml_locations_cannot_be_deleted(client):
    """Config is the source of truth; the API must not silently shadow it."""
    response = client.delete("/api/locations/home")
    assert response.status_code == 409
    assert "locations.yaml" in response.json()["detail"]


def test_deleting_an_unknown_location_is_404(client):
    assert client.delete("/api/locations/never_existed").status_code == 404


# --- observation log (PLAN.md 5) --------------------------------------------

@pytest.fixture
def clean_log():
    """Remove any sessions this test file creates, whatever the outcome."""
    from db import observations

    before = {s.id for s in observations.list_sessions(limit=500)}
    yield
    for session in observations.list_sessions(limit=500):
        if session.id not in before:
            observations.delete_session(session.id)


@requires_ephemeris
def test_log_prefill_offers_the_nights_targets(client):
    """PLAN.md 5's key UX move, over HTTP."""
    # Bortle 4 site, for the same reason as test_targets_still_rank_m31_first.
    response = client.get(
        "/api/log/prefill?date=2026-09-15&location=santa_monica_mtns&limit=4"
    )
    assert response.status_code == 200

    body = response.json()
    assert body["candidates"], "prefill should offer the recommended targets"
    assert body["conditions"] is not None

    names = [c["object_name"] for c in body["candidates"]]
    assert any("M31" in name for name in names)
    for candidate in body["candidates"]:
        assert candidate["object_id"]
        assert 0.0 <= candidate["score"] <= 100.0


@requires_ephemeris
def test_prefill_conditions_record_missing_weather(client):
    body = client.get("/api/log/prefill?date=2026-09-15&location=home").json()
    conditions = body["conditions"]
    assert conditions["weather_available"] is False
    assert conditions["is_gradeable"] is False


@requires_ephemeris
def test_full_log_round_trip(client, clean_log):
    created = client.post("/api/sessions", json={
        "date": "2026-09-15", "location": "home", "notes": "api round trip",
    })
    assert created.status_code == 201
    session = created.json()
    session_id = session["id"]
    assert session["conditions"] is not None

    added = client.post(f"/api/sessions/{session_id}/observations", json={
        "object_id": "NGC0224", "object_name": "M31 (Andromeda Galaxy)",
        "rating": 5,
    })
    assert added.status_code == 201

    fetched = client.get(f"/api/sessions/{session_id}").json()
    assert len(fetched["observations"]) == 1
    assert fetched["observations"][0]["rating"] == 5

    history = client.get("/api/objects/NGC0224/history").json()
    assert history["times_observed"] >= 1

    assert client.delete(f"/api/sessions/{session_id}").status_code == 204
    assert client.get(f"/api/sessions/{session_id}").status_code == 404


@requires_ephemeris
def test_logging_marks_the_object_as_seen_in_later_prefills(client, clean_log):
    """The loop closing: what you logged shows as already seen next time."""
    session_id = client.post("/api/sessions", json={
        "date": "2026-09-15", "location": "home",
    }).json()["id"]

    before = client.get("/api/log/prefill?date=2026-09-15&location=home").json()
    target = before["candidates"][0]
    assert target["already_logged"] is False

    client.post(f"/api/sessions/{session_id}/observations", json={
        "object_id": target["object_id"], "object_name": target["object_name"],
    })

    after = client.get("/api/log/prefill?date=2026-09-15&location=home").json()
    match = next(c for c in after["candidates"]
                 if c["object_id"] == target["object_id"])
    assert match["already_logged"] is True


def test_observation_on_a_missing_session_is_404(client):
    response = client.post("/api/sessions/99999/observations",
                           json={"object_name": "M31"})
    assert response.status_code == 404


def test_an_out_of_range_rating_is_rejected(client, clean_log):
    session_id = client.post("/api/sessions", json={
        "date": "2026-09-15", "location": "home", "snapshot_conditions": False,
    }).json()["id"]

    response = client.post(f"/api/sessions/{session_id}/observations",
                           json={"object_name": "M31", "rating": 9})
    assert response.status_code == 422


def test_deleting_a_missing_session_is_404(client):
    assert client.delete("/api/sessions/99999").status_code == 404


def test_log_stats_endpoint(client):
    body = client.get("/api/log/stats").json()
    assert set(body) == {"sessions", "observations", "distinct_objects",
                         "first_session", "last_session"}


@requires_ephemeris
def test_session_datetimes_are_utc(client, clean_log):
    session_id = client.post("/api/sessions", json={
        "date": "2026-09-15", "location": "home", "snapshot_conditions": False,
    }).json()["id"]
    client.post(f"/api/sessions/{session_id}/observations", json={
        "object_name": "M31", "observed_at_utc": "2026-09-16T04:30:00+00:00",
    })

    observation = client.get(f"/api/sessions/{session_id}").json()["observations"][0]
    assert _is_utc_iso(observation["observed_at_utc"])


# ---------------------------------------------------------------------------
# /api/horizon/marks — the data behind "name the lowest thing you can see"
# ---------------------------------------------------------------------------

@requires_ephemeris
def test_horizon_marks_returns_options_lowest_first(client):
    """The form offers these in order and the observer picks the lowest one
    they can see, so the order is load-bearing, not cosmetic."""
    response = client.get(
        "/api/horizon/marks?lat=39.09&lon=-110.9&azimuth=180"
        "&at=2026-09-16T04:00:00Z"
    )
    assert response.status_code == 200
    body = response.json()

    assert body["azimuth_deg"] == 180
    assert body["at"].startswith("2026-09-16T04:00:00")
    altitudes = [m["altitude_deg"] for m in body["marks"]]
    assert altitudes == sorted(altitudes)
    assert all(m["abbreviation"] and m["name"] for m in body["marks"])


@requires_ephemeris
def test_horizon_marks_differ_by_bearing(client):
    """Four identical lists would mean the bearing was being ignored, and
    every direction would record the same obstruction height."""
    def look(azimuth: int) -> set[str]:
        body = client.get(
            f"/api/horizon/marks?lat=39.09&lon=-110.9&azimuth={azimuth}"
            "&at=2026-09-16T04:00:00Z"
        ).json()
        return {m["abbreviation"] for m in body["marks"]}

    assert look(0) != look(180)
    assert look(90) != look(270)


def test_horizon_marks_rejects_a_naive_instant(client):
    """PLAN.md's UTC invariant at the HTTP boundary: a bare local time would
    be read as UTC and put the sky hours out."""
    response = client.get(
        "/api/horizon/marks?lat=39.09&lon=-110.9&azimuth=0"
        "&at=2026-09-16T04:00:00"
    )
    assert response.status_code == 422


def test_horizon_marks_rejects_an_azimuth_off_the_compass(client):
    """Guarded by the query model rather than silently wrapping, so a UI bug
    surfaces as an error instead of a horizon measured in the wrong place."""
    assert client.get(
        "/api/horizon/marks?lat=39.09&lon=-110.9&azimuth=360"
    ).status_code == 422


@requires_ephemeris
def test_a_measured_horizon_is_not_flagged_generic(client):
    """The whole reason for measuring one. A preset is an assumption and the
    UI says so everywhere; an explicit azimuth->altitude map is not, and must
    not inherit the warning."""
    payload = {"key": "api_measured_site", "name": "Measured Site",
               "lat": 39.09, "lon": -110.9, "bortle": 2,
               "horizon": '{"0": 24.0, "90": 0.0, "180": 22.5, "270": 18.1}'}
    response = client.post("/api/locations", json=payload)
    assert response.status_code == 201
    try:
        body = response.json()
        assert body["horizon_is_generic"] is False
        assert body["horizon_max_deg"] == pytest.approx(24.0, abs=0.1)
    finally:
        client.delete("/api/locations/api_measured_site")
