"""HTTP layer: response shape, the UTC contract, and error handling.

The API must stay a thin adapter (PLAN.md §4), so these tests check that it
faithfully exposes what the engine computed — not that the astronomy is right,
which the engine tests already cover.

No test here reaches the network: ASTRO_NO_NETWORK is set for the module, so
weather and geocoding take their degraded paths.
"""

from __future__ import annotations

import json
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
                 "/api/locations", "/api/sites/resolve"):
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
    """The other half of that: from Bortle 8, M31 is not a recommendation.

    Asserted on the badge rather than on absence from the list. The original
    checked that M31 was not among the Galaxies group at all, which really
    tested whether it fell inside `DEFAULT_GROUP_LIMIT` -- the group is
    truncated to eight, so any change to the ranking could pass or fail this
    without the light-pollution model moving at all. Narrowing the default
    window to an observing session did exactly that.
    """
    body = client.get("/api/targets?date=2026-09-15&location=home").json()
    galaxies = body["groups"].get("Galaxies", [])
    m31 = next((t for t in galaxies if t["messier"] == 31), None)
    # Listed or truncated away, either is fine; what must not happen is it
    # being presented as observable from an inner-city sky.
    assert m31 is None or m31["too_faint"] is True


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


def _site(**fields) -> str:
    """A `site` parameter, as the browser sends one."""
    return json.dumps({"key": "api_site", "name": "API Site",
                       "lat": 36.6, "lon": -118.06, **fields})


def test_resolve_site_from_coordinates(client):
    payload = {"key": "api_test_site", "name": "API Test Site",
               "lat": 36.6, "lon": -118.06, "elevation_m": 1130,
               "bortle": 2, "horizon": "ridge"}
    response = client.post("/api/sites/resolve", json=payload)
    assert response.status_code == 200

    body = response.json()
    assert body["key"] == "api_test_site"
    assert body["timezone"] == "America/Los_Angeles"      # resolved, not supplied
    assert body["horizon_name"] == "ridge"
    assert body["horizon_spec"] == "ridge"
    assert body["source"] == "browser"

    # Checked, not kept: the browser stores its sites, the server nothing.
    listed = {loc["key"] for loc in client.get("/api/locations").json()}
    assert "api_test_site" not in listed


@requires_ephemeris
def test_a_site_sent_with_the_request_is_used(client):
    response = client.get("/api/night", params={"site": _site(name="Usable"),
                                                "date": "2026-09-15"})
    assert response.status_code == 200
    body = response.json()
    assert body["window"]["dark_hours"] > 0
    assert body["window"]["location"]["name"] == "Usable"
    assert body["window"]["location"]["source"] == "browser"


@requires_ephemeris
def test_a_site_outranks_a_location_key(client):
    """The browser's own copy of a site is the one that counts."""
    body = client.get("/api/planets", params={
        "location": "home", "site": _site(lat=-33.9, lon=151.2),
        "date": "2026-09-15", "find_next": "false"}).json()
    assert body["location"]["timezone"] == "Australia/Sydney"


@pytest.mark.parametrize("site", ["not json", json.dumps({"name": "No coordinates"}),
                                  json.dumps({"lat": 95, "lon": 0})])
def test_an_unusable_site_is_422(client, site):
    response = client.get("/api/night", params={"site": site, "date": "2026-09-15"})
    assert response.status_code == 422


@requires_ephemeris
def test_every_per_site_endpoint_takes_a_site(client):
    site = _site()
    for path, extra in [("/api/night", {}), ("/api/planets", {"find_next": "false"}),
                        ("/api/events", {"days": 7}), ("/api/altitude", {"bodies": "moon"}),
                        ("/api/sky/frame", {"at": "2026-09-16T05:00:00Z"}),
                        ("/api/targets", {"limit": 1})]:
        response = client.get(path, params={"site": site, "date": "2026-09-15",
                                            "from": "2026-09-15", **extra})
        assert response.status_code == 200, path


@requires_ephemeris
def test_the_api_stores_nothing(client, monkeypatch, tmp_path):
    """No database appears on a server that is only ever sent sites."""
    import db.store

    path = tmp_path / "planner.sqlite3"
    monkeypatch.setattr(db.store, "database_path", lambda: path)
    client.get("/api/locations")
    client.post("/api/sites/resolve", json={"lat": 36.6, "lon": -118.06})
    client.get("/api/night", params={"site": _site(), "date": "2026-09-15"})
    assert not path.exists()

    for method, url in [("post", "/api/locations"), ("delete", "/api/locations/home"),
                        ("post", "/api/sessions"), ("get", "/api/log/prefill")]:
        assert getattr(client, method)(url).status_code in (404, 405), url


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


def _map_with_tiles(tmp_path, *, with_tiles=True):
    """A 2x2 raster, and beside it the tile folder the build would write."""
    rasterio = pytest.importorskip("rasterio")
    import numpy as np

    raster = tmp_path / "map.tif"
    with rasterio.open(raster, "w", driver="GTiff", width=2, height=2, count=1,
                       dtype="float32", crs="EPSG:4326",
                       transform=rasterio.Affine(1, 0, -112, 0, -1, 41)) as out:
        out.write(np.full((2, 2), 0.5, dtype=np.float32), 1)
        out.update_tags(LABEL="modelled from 2025 satellite data")
    if with_tiles:
        tiles = tmp_path / "map_tiles"
        (tiles / "5" / "6").mkdir(parents=True)
        (tiles / "5" / "6" / "12.png").write_bytes(b"\x89PNG fake tile")
        (tiles / "meta.json").write_text(json.dumps(
            {"min_zoom": 3, "max_zoom": 7, "legend": [[22.0, [0, 0, 0, 0]], [17.0, [255, 255, 255, 245]]]}))
    return raster


@pytest.fixture
def configured_map(monkeypatch, tmp_path):
    import engine.skybrightness as sb

    def configure(**kwargs):
        monkeypatch.setenv("ASTRO_SKYBRIGHTNESS_RASTER", str(_map_with_tiles(tmp_path, **kwargs)))
        sb._open_raster.cache_clear()
    yield configure
    sb._open_raster.cache_clear()


def test_the_map_overlay_is_served_from_its_prebuilt_tiles(client, configured_map):
    configured_map()
    tiles = client.get("/api/skybrightness").json()["tiles"]
    assert tiles["min_zoom"] == 3 and tiles["max_zoom"] == 7
    assert tiles["legend"][0] == [22.0, [0, 0, 0, 0]]

    drawn = client.get("/api/skybrightness/tiles/5/6/12.png", params={"v": tiles["version"]})
    assert drawn.status_code == 200
    assert drawn.headers["content-type"] == "image/png"
    assert drawn.content == b"\x89PNG fake tile"


def test_a_rebuilt_map_can_never_be_hidden_by_cached_tiles(client, configured_map):
    """The site map asks for tiles by version. At the current version a tile
    can be kept for good -- a rebuild is new URLs -- and anything else must
    be checked again, or a browser goes on showing the old map."""
    configured_map()
    version = client.get("/api/skybrightness").json()["tiles"]["version"]
    assert version

    current = client.get("/api/skybrightness/tiles/5/6/12.png", params={"v": version})
    assert "immutable" in current.headers["cache-control"]
    for params in ({}, {"v": "an-old-build"}):
        stale = client.get("/api/skybrightness/tiles/5/6/12.png", params=params)
        assert stale.headers["cache-control"] == "no-cache"


def test_a_tile_left_out_for_a_pristine_sky_is_clear_not_missing(client, configured_map):
    """The build skips tiles with nothing to draw. Asking for one is the most
    ordinary request on the map, so it gets a clear image, not an error."""
    configured_map()
    blank = client.get("/api/skybrightness/tiles/5/7/12.png")
    assert blank.status_code == 200
    assert blank.content.startswith(b"\x89PNG")


def test_zooms_the_map_was_not_drawn_at_are_404(client, configured_map):
    configured_map()
    assert client.get("/api/skybrightness/tiles/9/1/1.png").status_code == 404
    assert client.get("/api/skybrightness/tiles/2/1/1.png").status_code == 404


def test_a_map_without_tiles_says_so(client, configured_map):
    configured_map(with_tiles=False)
    assert client.get("/api/skybrightness").json()["tiles"] is None
    assert client.get("/api/skybrightness/tiles/5/6/12.png").status_code == 404


def test_a_reading_carries_the_decimal_class(client, configured_map):
    configured_map()
    body = client.get("/api/skybrightness/at", params={"lat": 40.5, "lon": -111.5}).json()
    assert body["in_coverage"] is True
    assert round(body["bortle_decimal"]) == body["bortle"]


def test_resolve_a_directional_horizon(client):
    """The bearing has to survive the API and the browser's copy, not just
    the engine."""
    payload = {"key": "api_facing_site", "name": "Facing Site",
               "lat": 36.6, "lon": -118.06, "bortle": 3,
               "horizon": "ridge", "horizon_facing": 250}
    body = client.post("/api/sites/resolve", json=payload).json()
    assert body["horizon_name"] == "ridge"
    assert body["horizon_facing"] == 250
    assert body["horizon_is_generic"] is True

    # What the browser stores is `horizon_spec`, sent back as `horizon`.
    again = client.post("/api/sites/resolve",
                        json={**payload, "horizon": body["horizon_spec"],
                              "horizon_facing": None}).json()
    assert again["horizon_facing"] == 250
    assert again["horizon_name"] == "ridge"


def test_a_measured_horizon_round_trips_through_an_edit(client):
    """The editor must be able to send a measured horizon back unchanged.

    It used to get only the profile's name and peak, so saving any edit to a
    measured site -- a rename -- replaced the survey with a flat ring at its
    highest angle. The points now come back, and resending them is lossless.
    """
    measured = {"0": 12.5, "90": 4.0, "180": 20.0, "270": 0.0}
    base = {"key": "api_measured_site", "name": "Measured",
            "lat": 36.6, "lon": -118.06, "bortle": 3}
    listed = client.post("/api/sites/resolve",
                         json={**base, "horizon": json.dumps(measured)}).json()
    assert listed["horizon_points"] == measured
    assert listed["horizon_is_generic"] is False

    # An edit that renames the site and resends what it was given.
    edited = client.post("/api/sites/resolve",
                         json={**base, "name": "Renamed",
                               "horizon": json.dumps(listed["horizon_points"])}).json()
    assert edited["name"] == "Renamed"
    assert edited["horizon_points"] == measured
    assert edited["horizon_max_deg"] == 20.0

    # And the spec the browser stores is the same profile again.
    spec = client.post("/api/sites/resolve",
                       json={**base, "horizon": listed["horizon_spec"]}).json()
    assert spec["horizon_points"] == measured


@pytest.mark.parametrize("horizon", ["0", "10", "ridge"])
def test_only_a_measured_horizon_reports_points(client, horizon):
    """A clear horizon, a uniform angle or a preset has nothing to resend --
    and a clear one reporting {0: 0} would read to the editor as a survey."""
    listed = client.post("/api/sites/resolve", json={
        "key": "api_unmeasured", "name": "Unmeasured", "lat": 36.6,
        "lon": -118.06, "bortle": 3, "horizon": horizon}).json()
    assert listed["horizon_points"] is None


def test_horizon_binds_says_whether_a_profile_can_change_anything(client):
    """A profile below the altitude floor is inert. The API says so rather
    than leaving the UI to rediscover it."""
    body = client.get("/api/locations").json()
    home = next(loc for loc in body if loc["key"] == "home")
    # home uses `hilly`, which tops out at 12 deg under a 25 deg floor
    assert home["horizon_binds"] is False

    listed = client.post("/api/sites/resolve", json={
        "key": "api_binding_site", "name": "Binding", "lat": 36.6,
        "lon": -118.06, "horizon": "ridge"}).json()
    assert listed["horizon_binds"] is True


def test_an_out_of_range_bearing_is_rejected(client):
    response = client.post("/api/sites/resolve",
                           json={"key": "api_bad_bearing", "name": "Bad",
                                 "lat": 36.6, "lon": -118.06,
                                 "horizon": "ridge", "horizon_facing": 400})
    assert response.status_code == 422


def test_resolve_without_coordinates_or_query_is_422(client):
    assert client.post("/api/sites/resolve", json={"name": "Nowhere"}).status_code == 422


def test_geocoding_offline_returns_empty_not_an_error(client):
    """Graceful degradation, same contract as weather."""
    response = client.get("/api/geocode?q=Lone Pine")
    assert response.status_code == 200
    assert response.json() == []


def test_resolve_by_query_while_offline_is_404_not_a_crash(client):
    response = client.post("/api/sites/resolve", json={"query": "Lone Pine"})
    assert response.status_code == 404
    assert "unreachable" in response.json()["detail"]


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
    response = client.post("/api/sites/resolve", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["horizon_is_generic"] is False
    assert body["horizon_max_deg"] == pytest.approx(24.0, abs=0.1)


@requires_ephemeris
def test_targets_carry_the_showpiece_flag(client):
    """The checkbox in the UI filters on this and nothing else, so the flag
    has to reach the wire rather than being computed in the browser."""
    body = client.get(
        "/api/targets?location=home&date=2026-09-15&include_all=true&limit=1000"
    ).json()
    rows = [row for group in body["groups"].values() for row in group]
    showpieces = [row for row in rows if row["showpiece"]]

    assert len(rows) > 5000, "expected the unfiltered catalogue"
    assert 100 <= len(showpieces) <= 250

    by_messier = {row["messier"]: row for row in rows if row["messier"]}
    assert by_messier[13]["showpiece"] is True          # the Hercules cluster
    assert by_messier[57]["showpiece"] is True          # the Ring Nebula
    assert by_messier[40]["showpiece"] is False         # a double star


@requires_ephemeris
def test_a_uniform_obstruction_angle_survives_the_api(client):
    """The form now sends a bare angle rather than a preset name. It has to
    arrive as a generic ring -- if it came back `is_generic=False` it would
    outrank a measured horizon in every warning the UI prints."""
    payload = {"key": "api_angle_site", "name": "Angle Site",
               "lat": 39.09, "lon": -110.9, "bortle": 3, "horizon": "30"}
    response = client.post("/api/sites/resolve", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["horizon_is_generic"] is True
    assert body["horizon_max_deg"] == pytest.approx(30.0)
    # 30 deg clears the 25 deg altitude floor, so it actually constrains.
    assert body["horizon_binds"] is True

    # Sent back as the browser stores it, it is the same ring.
    reloaded = client.get("/api/planets", params={
        "site": _site(horizon=body["horizon_spec"]), "date": "2026-09-15",
        "find_next": "false"}).json()["location"]
    assert reloaded["horizon_max_deg"] == pytest.approx(30.0)
    assert reloaded["horizon_is_generic"] is True


def test_an_impossible_obstruction_angle_is_refused(client):
    """A 422 rather than a site whose horizon excludes the whole sky."""
    payload = {"key": "api_bad_angle", "name": "Bad Angle",
               "lat": 39.09, "lon": -110.9, "bortle": 3, "horizon": "120"}
    assert client.post("/api/sites/resolve", json=payload).status_code == 422


@requires_ephemeris
def test_the_night_average_factors_reach_the_wire(client):
    """The UI shows these beside the peak-slot breakdown, because the peak
    slot's clear factor describes the least cloudy half hour by construction
    and was being read as a statement about the night."""
    body = client.get("/api/night?date=2026-09-15&location=home").json()
    score = body["score"]
    assert score["mean_factors_deep_sky"] is not None
    for key in ("clear", "transparency", "moon", "seeing", "wind", "dew"):
        assert 0.0 <= score["mean_factors_deep_sky"][key] <= 1.0
    # Never better than the best slot, by definition of a mean.
    assert (score["mean_factors_deep_sky"]["clear"]
            <= score["peak_factors_deep_sky"]["clear"] + 1e-9)


@requires_ephemeris
def test_m31_carries_the_core_note_rather_than_a_faint_badge(client):
    """From the suburban test site M31 must be listed and must say why the
    mean surface brightness is not the number to go by."""
    body = client.get(
        "/api/targets?date=2026-09-15&location=santa_monica_mtns"
        "&include_all=true&limit=1000"
    ).json()
    rows = [row for group in body["groups"].values() for row in group]
    m31 = next(row for row in rows if row["messier"] == 31)
    assert m31["too_faint"] is False


# ---------------------------------------------------------------------------
# The observing session, over HTTP
# ---------------------------------------------------------------------------

@requires_ephemeris
def test_night_defaults_to_a_session_and_says_what_it_used(client):
    """The UI has to be able to show and pre-fill the hours without knowing
    the server's default, so the response carries them."""
    body = client.get("/api/night?date=2026-09-15&location=home").json()
    session = body["session"]
    assert session is not None
    assert session["is_default"] is True
    assert session["hours"] > 0
    assert 0 <= session["dark_hours"] <= session["hours"] + 1e-9
    # Scored over the session, so fewer slots than the whole night.
    assert len(body["score"]["slots"]) > 0


@requires_ephemeris
def test_a_chosen_session_changes_the_conditions_summary(client):
    """The point of the whole feature: the numbers describe the hours you
    will be outside, not the hours you will be asleep."""
    default = client.get("/api/night?date=2026-09-15&location=home").json()
    wide = client.get(
        "/api/night?date=2026-09-15&location=home"
        "&session_start=2026-09-16T03:00:00Z&session_end=2026-09-16T11:00:00Z"
    ).json()

    assert wide["session"]["is_default"] is False
    assert wide["session"]["hours"] > default["session"]["hours"]
    assert len(wide["score"]["slots"]) > len(default["score"]["slots"])


@requires_ephemeris
def test_a_chosen_session_changes_the_target_list(client):
    """A four-hour session cannot offer everything an eight-hour one does."""
    short = client.get(
        "/api/targets?date=2026-09-15&location=home&min_altitude=0"
        "&session_start=2026-09-16T04:00:00Z&session_end=2026-09-16T06:00:00Z"
    ).json()
    long = client.get(
        "/api/targets?date=2026-09-15&location=home&min_altitude=0"
        "&session_start=2026-09-16T03:00:00Z&session_end=2026-09-16T11:00:00Z"
    ).json()
    assert long["total_passing"] > short["total_passing"]
    assert short["session"]["hours"] < long["session"]["hours"]


def test_a_naive_session_boundary_is_refused(client):
    """The UTC invariant at the boundary: a bare local time read as UTC would
    silently shift the session by hours."""
    response = client.get(
        "/api/night?date=2026-09-15&location=home"
        "&session_start=2026-09-16T04:00:00"
    )
    assert response.status_code == 422


def test_an_unparseable_session_boundary_is_refused(client):
    assert client.get(
        "/api/night?date=2026-09-15&location=home&session_start=tuesday"
    ).status_code == 422


@requires_ephemeris
def test_the_site_horizon_alone_can_be_the_floor(client):
    """`min_altitude=0` is what the web UI sends: the site's own obstruction
    angle decides, rather than a universal 25 deg overriding what the
    observer measured."""
    site = _site(key="api_floor_site", name="Floor Site", lat=34.0, lon=-118.5,
                 bortle=4, horizon="0")
    open_sky = client.get("/api/targets", params={
        "date": "2026-09-15", "site": site, "min_altitude": 0,
        "limit": 1000}).json()
    with_floor = client.get("/api/targets", params={
        "date": "2026-09-15", "site": site, "min_altitude": 25,
        "limit": 1000}).json()
    # A clear horizon reaches lower than 25 deg, so it must admit more.
    assert open_sky["total_passing"] > with_floor["total_passing"]


@requires_ephemeris
def test_constellation_headers_reach_past_the_session_too(client):
    """The group headers had the same bug as the rows inside them.

    Sampling only the session made every constellation appear to set exactly
    when the observer went to bed, and made "visible late" unreachable for a
    group, since nothing could start after a boundary the sampling stopped
    at. In September from a northern site Orion is the canonical late group.
    """
    body = client.get(
        "/api/targets?date=2026-09-15&location=home&min_altitude=0"
        "&group_by=constellation&include_all=true&limit=1000"
    ).json()
    info = body.get("group_info") or {}
    constellations = {k: v for k, v in info.items() if v.get("is_constellation")}
    assert constellations, "no constellation headers to check"

    late = [v for v in constellations.values() if v["visibility"] == "late"]
    assert late, "nothing flagged late: headers are still clipped to the session"

    session_end = body["session"]["end"]
    assert any(v["window_end"] > session_end
               for v in constellations.values() if v.get("window_end")), (
        "no constellation window runs past the session end"
    )
    # And a late group must genuinely start after it.
    for group in late:
        assert group["window_start"] >= session_end


@requires_ephemeris
def test_targets_carry_their_reference_facts(client):
    """The detail panel reads these straight off the row, so they have to
    reach the wire rather than being looked up in the browser."""
    body = client.get(
        "/api/targets?date=2026-09-15&location=home&include_all=true&limit=1000"
    ).json()
    rows = {row["messier"]: row for row in
            (r for group in body["groups"].values() for r in group)
            if row["messier"]}

    m13 = rows[13]
    assert m13["distance_ly"] == pytest.approx(22200)
    # A quoted value, not distance times apparent size: that came out at 107
    # for M13 because the catalogued 16.5 arcminutes is the bright core.
    assert m13["diameter_ly"] == pytest.approx(145)
    assert m13["discovered_by"] == "Edmond Halley"
    assert m13["discovered_year"] == 1714
    assert m13["about"]

    # An object with no curated entry must carry nulls, not blanks or zeros:
    # the UI drops the row entirely on null and would print "0 ly" otherwise.
    anonymous = next(row for group in body["groups"].values() for row in group
                     if row["messier"] is None and not row["showpiece"])
    assert anonymous["distance_ly"] is None
    assert anonymous["diameter_ly"] is None
    assert anonymous["discovered_by"] is None
    assert anonymous["about"] is None


@requires_ephemeris
def test_planets_carry_their_reference_facts(client):
    """And the numbers the disc is drawn from -- phase, apparent size and ring
    tilt -- which is what makes the drawing tonight's rather than a stock
    picture of a different decade."""
    body = client.get(
        "/api/planets?date=2026-09-15&location=home&find_next=false"
    ).json()
    planets = {p["name"]: p for p in body["planets"]}

    saturn = planets["saturn"]
    assert saturn["facts"]["moons"] > 50
    assert saturn["facts"]["discovered_by"] is None      # naked-eye
    assert saturn["ring_tilt_deg"] is not None

    uranus = planets["uranus"]
    assert uranus["facts"]["discovered_year"] == 1781
    assert uranus["facts"]["rotation_hours"] < 0          # retrograde

    for planet in body["planets"]:
        assert planet["facts"] is not None, planet["name"]
        assert planet["distance_au"] is not None


@requires_ephemeris
def test_targets_carry_every_name_they_go_by(client):
    """Only the first common name is on display, so search matches nothing
    else unless the rest reach the browser. M17 is shown as the Checkmark
    Nebula and is also the Swan, the Lobster and the Omega."""
    body = client.get(
        "/api/targets?date=2026-09-15&location=home&include_all=true&limit=1000"
    ).json()
    rows = {row["messier"]: row for row in
            (r for group in body["groups"].values() for r in group)
            if row["messier"]}

    m17 = rows[17]
    assert "Checkmark" in m17["display_name"]
    lowered = [alias.lower() for alias in m17["aliases"]]
    assert "swan nebula" in lowered
    assert "omega nebula" in lowered


@requires_ephemeris
def test_named_double_stars_reach_the_wire(client):
    """They come from a vendored CSV of their own, not OpenNGC, so this is
    the check that the third catalogue file is actually being read."""
    body = client.get(
        "/api/targets?date=2026-09-15&location=home&include_all=true&limit=1000"
    ).json()
    rows = {row["display_name"]: row for group in body["groups"].values()
            for row in group}

    albireo = rows.get("Albireo")
    assert albireo is not None, "Albireo is not in the target list"
    assert albireo["object_type"] == "double star"
    assert albireo["separation_arcsec"] == pytest.approx(35.0)
    assert albireo["component_mags"] == "3.1 / 5.1"
    assert albireo["showpiece"] is True
    # Parallax-derived and trustworthy for a nearby star, unlike the
    # catalogue's parallax column for galaxies.
    assert 300 <= albireo["distance_ly"] <= 420


def test_sky_catalog_is_served_once_and_cached(client):
    resp = client.get("/api/sky/catalog")
    assert resp.status_code == 200
    assert "max-age" in resp.headers["cache-control"]
    body = resp.json()
    assert len(body["ra"]) == len(body["dec"]) == len(body["mag"]) > 30_000
    # The showpieces the chart can snap to, each with a position.
    m13 = next(o for o in body["objects"] if o["label"] == "M13")
    assert m13["ra"] == pytest.approx(250.42, abs=0.05) and m13["group"]


@requires_ephemeris
def test_sky_frame_returns_a_rotation_and_the_bodies(client):
    resp = client.get("/api/sky/frame?location=home&at=2026-09-16T04:00:00Z")
    assert resp.status_code == 200
    body = resp.json()
    assert _is_utc_iso(body["at"])
    assert len(body["matrix"]) == 3 and all(len(row) == 3 for row in body["matrix"])
    assert {b["name"] for b in body["bodies"]} >= {"moon", "saturn", "jupiter"}


@pytest.mark.parametrize("at", ["2026-09-16T04:00:00", "yesterday"])
def test_sky_frame_rejects_a_time_it_cannot_place(client, at):
    assert client.get(f"/api/sky/frame?location=home&at={at}").status_code == 422


def test_star_card_for_a_named_star(client):
    catalog = client.get("/api/sky/catalog").json()
    betelgeuse = next(int(i) for i, n in catalog["labels"].items() if n == "Betelgeuse")
    card = client.get(f"/api/sky/star/{betelgeuse}").json()
    assert card["kind"] == "Red supergiant"
    assert 400 <= card["distance_ly"] <= 700
    assert card["age_basis"] == "published"
    assert client.get("/api/sky/star/99999999").status_code == 404


@requires_ephemeris
@pytest.mark.parametrize("day", ["1700-01-01", "2150-06-01", "2300-01-01"])
def test_a_date_past_the_ephemeris_is_refused_plainly(client, day):
    """Out of DE440s's range every computation fails deep in jplephem; the API
    says why instead of answering 500."""
    for path in ("/api/night", "/api/targets", "/api/planets"):
        resp = client.get(f"{path}?location=home&date={day}")
        assert resp.status_code == 422, (path, resp.status_code)
        assert "ephemeris" in resp.json()["detail"]
