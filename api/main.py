"""FastAPI app — a thin adapter over `engine`, per PLAN.md §4.

No astronomy is computed here. Every endpoint resolves a location, calls the
same engine functions the CLI calls, and converts the result to a response
model. If a calculation is happening in this file, it is in the wrong place.

Timezone rule: responses carry ISO-8601 **UTC** datetimes plus the site's IANA
zone. Conversion to local time happens in the browser, which is the display
layer. The API never emits local times.
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from datetime import date, datetime, timedelta

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from db import observations, store
from engine import geocode
from engine.catalog.loader import find_object, load_catalog
from engine.ephem import Interval, altaz_series, night_window
from engine.equipment import load_equipment
from engine.events import (
    MOON_CONJUNCTION_DEG,
    PLANET_CONJUNCTION_DEG,
    assess_shower,
    conjunctions,
    load_meteor_showers,
    lunar_eclipses,
    showers_active_between,
)
from engine.constellations import (assess_constellations, centroid, centroids,
                                   constellation_name)
from engine.horizon import build as build_horizon
from engine.locations import Location, LocationError, atlas_sqm_for
from engine.planets import ALL_PLANETS, report_all, report_moon
from engine.scoring import score_night, verdict
from engine.session import dark_overlap_hours, resolve_session
from engine.reference import MOON_FACTS, deep_sky_facts, planet_facts
from engine.showpieces import showpiece_ids
from engine.targets import (
    DEFAULT_GROUP_LIMIT,
    DEFAULT_MIN_ALTITUDE_DEG,
    _observing_window,
    assess_targets,
    group_catalog,
    group_targets,
    uses_true_dark,
)
from engine.timeutil import local_noon_utc, now_utc, resolve_night_date
from engine.weather import get_forecast

from .schemas import (
    AltitudePointModel,
    AltitudeResponse,
    AltitudeSeriesModel,
    ConjunctionModel,
    EclipseModel,
    EventsResponse,
    FactorsModel,
    GeocodeCandidate,
    GroupInfo,
    IntervalModel,
    LocationModel,
    NewLocationRequest,
    SkyBrightnessCoverage,
    SkyBrightnessReading,
    HorizonMarksResponse,
    SkyMarkModel,
    NightResponse,
    NightWindowModel,
    ChartPointModel,
    FinderChartModel,
    MoonFactsModel,
    MoonModel,
    PlanetFactsModel,
    PlanetModel,
    PlanetsResponse,
    ScoreModel,
    ObservingWindowModel,
    ShowerModel,
    SlotModel,
    TargetModel,
    TargetsResponse,
)

def _warm_caches() -> None:
    """Load the slow, static things once, before anyone asks for them.

    The catalogue parse, the timezone finder and the ephemeris each cost
    about a second the first time and nothing after, and the coming months'
    conjunctions a few seconds. Without this, whoever
    made the first request after a restart paid all of it -- the horizon
    measurer's first open took 4 s where later ones took under 1 s. Nothing
    is computed about any night; these are caches of vendored data.
    """
    from engine.ephem import load_ephemeris
    from engine.events import conjunctions
    from engine.locations import _timezone_finder

    def next_conjunctions():
        # The events tab looks 90 days ahead of whichever night is open.
        # Computed month by month and cached, so warming four months covers
        # tonight and a week or so of stepping forward.
        conjunctions(now_utc().date(), 120)

    for warm in (load_catalog, _timezone_finder, load_ephemeris,
                 next_conjunctions):
        try:
            warm()
        except Exception:  # noqa: BLE001 -- a warm-up must never stop startup
            pass


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # A daemon thread, so the server takes requests immediately and a slow
    # warm-up only ever makes an early request no slower than it was.
    threading.Thread(target=_warm_caches, name="warm-caches", daemon=True).start()
    yield


app = FastAPI(
    title="Astrolabe",
    version="0.1.0",
    description="Is tonight worth going out, and what should I point at?",
    lifespan=_lifespan,
)

# The unfiltered target list is ~10 MB of JSON; it compresses to a fraction of
# that and the payload is almost entirely repeated field names.
#
# Level 5, not the default 9: on that payload 9 took 617 ms to save 94 KB over
# level 5's 131 ms. This is served from localhost, where half a second of
# compression costs more than any saving in transfer.
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)

# The Vite dev server runs on a different origin during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- helpers ----------------------------------------------------------------

def _resolve_location(key: str | None) -> Location:
    """YAML sites plus runtime additions; 404 with the known keys on a miss."""
    locations = store.all_locations()

    # No sites at all is an ordinary starting state, not a malformed request:
    # nothing ships preconfigured, because every answer here depends on where
    # the observer is standing.
    if not locations:
        raise HTTPException(
            status_code=404,
            detail="no observing sites yet. Add one with the \"Sites...\" "
                   "button, which can place a site from a map, a place name, "
                   "or raw coordinates.",
        )

    if not key:
        from engine.locations import default_location_key

        # The configured default covers config sites; anything added through
        # the UI lives in the store, which that function cannot see.
        key = default_location_key() or sorted(locations)[0]

    if key not in locations:
        raise HTTPException(
            status_code=404,
            detail=f"unknown location {key!r}; known: {', '.join(sorted(locations))}",
        )
    return locations[key]


def _resolve_date(raw: str | None, location: Location) -> date:
    """Parse an ISO date, or resolve "tonight" per PLAN.md §3.1."""
    if not raw:
        return resolve_night_date(now_utc(), location.tz)
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=422,
                            detail=f"{raw!r} is not a valid date; use YYYY-MM-DD")


def _location_model(location: Location, source: str = "config") -> LocationModel:
    stored = store.stored_locations()
    return LocationModel(
        key=location.key,
        name=location.name,
        lat=location.lat,
        lon=location.lon,
        elevation_m=location.elevation_m,
        bortle=location.bortle,
        sqm=location.sqm,
        sky_source=location.sky_source,
        effective_bortle=location.effective_bortle,
        timezone=location.tz,
        horizon_name=location.horizon.name,
        horizon_is_generic=location.horizon.is_generic,
        horizon_max_deg=location.horizon.max_obstruction_deg,
        # Only an explicit azimuth map -- what measuring produces -- has
        # points worth sending back. A clear horizon is non-generic too, but
        # it is one number, and the editor would mistake it for a survey.
        horizon_points=({str(az): alt for az, alt in location.horizon.points}
                        if location.horizon.name == "custom" else None),
        horizon_facing=location.horizon.facing,
        horizon_binds=(location.horizon.max_obstruction_deg
                       > DEFAULT_MIN_ALTITUDE_DEG),
        source="stored" if location.key in stored else source,
    )


def _interval(span: Interval) -> IntervalModel:
    start, end = span
    return IntervalModel(start=start, end=end,
                         hours=(end - start).total_seconds() / 3600.0)


def _horizon_warning(location: Location, min_altitude: float) -> str | None:
    """The same honesty note the CLI prints, for the UI to surface."""
    horizon = location.horizon
    if horizon.is_flat:
        return None
    facing = "" if horizon.facing is None else f", facing {horizon.facing:.0f} deg"
    parts = [f"Horizon profile '{horizon.name}'{facing} "
             f"(max {horizon.max_obstruction_deg:.0f} deg)."]
    if horizon.is_generic:
        parts.append("This is a GENERIC preset, not a survey of this site.")
    if horizon.max_obstruction_deg <= min_altitude:
        parts.append(f"It sits below the {min_altitude:.0f} deg floor, so it is "
                     "not affecting these results.")
    return " ".join(parts)


# --- night ------------------------------------------------------------------

def _session_instant(raw: str | None, field: str) -> datetime | None:
    """Parse one end of a session window from an ISO-8601 query parameter.

    Timezone-required, like every other datetime crossing this boundary: a
    bare local time would be read as UTC and shift the session by hours.
    """
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=422,
                            detail=f"{field}: {raw!r} is not an ISO-8601 datetime")
    if parsed.tzinfo is None:
        raise HTTPException(
            status_code=422,
            detail=f"{field} must carry a timezone; this API is UTC-only",
        )
    return parsed


def _session_model(window, session, *, asked: bool) -> ObservingWindowModel | None:
    """The session actually used, echoed back so the UI can show and edit it."""
    if session is None:
        return None
    start, end = session
    return ObservingWindowModel(
        start=start, end=end,
        hours=(end - start).total_seconds() / 3600.0,
        dark_hours=dark_overlap_hours(window, session),
        is_default=not asked,
    )


@app.get("/api/night", response_model=NightResponse, tags=["night"])
def get_night(date_: str | None = Query(None, alias="date"),
              location: str | None = None,
              session_start: str | None = Query(
                  None, description="ISO-8601 UTC start of the observing "
                                    "session. Defaults to astronomical dusk."),
              session_end: str | None = Query(
                  None, description="ISO-8601 UTC end. Defaults to 01:00 local."),
              ) -> NightResponse:
    """Night window, moon, condition scores and hourly conditions.

    Scores cover the observing session, not the whole night. See
    `engine/session.py` for why: averaging conditions over hours nobody will
    be outside for describes the night rather than the plan.
    """
    site = _resolve_location(location)
    night_date = _resolve_date(date_, site)
    window = night_window(night_date, site)
    session = resolve_session(window,
                              _session_instant(session_start, "session_start"),
                              _session_instant(session_end, "session_end"))

    forecast = get_forecast(
        site,
        start=session[0] if session else None,
        end=session[1] if session else None,
    )
    score = score_night(window, forecast, session=session)

    peak = max(score.slots, key=lambda s: s.deep_sky) if score.slots else None
    # Over the night, not at the peak. The peak slot is the least cloudy one
    # by construction, so reading the limiting factor off it meant cloud could
    # never be blamed on a night that clouds over halfway through -- which is
    # the commonest way a night goes wrong.
    limiting = None
    if score.slots:
        name, value = score.mean_factors_deep_sky.weakest()
        limiting = name if value < 0.99 else None

    def _factors(breakdown) -> FactorsModel:
        return FactorsModel(clear=breakdown.clear,
                            transparency=breakdown.transparency,
                            moon=breakdown.moon, seeing=breakdown.seeing,
                            wind=breakdown.wind, dew=breakdown.dew)

    slots = []
    for slot in score.slots:
        conditions = forecast.at(slot.time_utc) if forecast.available else None
        slots.append(SlotModel(
            time=slot.time_utc,
            deep_sky=slot.deep_sky,
            planetary=slot.planetary,
            moon_altitude_deg=slot.moon_altitude_deg,
            cloud_cover=conditions.cloud_cover if conditions else None,
            wind_gust_kmh=conditions.wind_gust_kmh if conditions else None,
            temperature_c=conditions.temperature_c if conditions else None,
            humidity_pct=conditions.humidity_pct if conditions else None,
            dew_point_spread_c=(conditions.dew_point_spread_c
                                if conditions else None),
        ))

    return NightResponse(
        session=_session_model(window, session,
                               asked=bool(session_start or session_end)),
        window=NightWindowModel(
            date=window.date,
            location=_location_model(site),
            sunset=window.sunset_utc,
            sunrise=window.sunrise_utc,
            civil_dusk=window.civil_dusk_utc,
            nautical_dusk=window.nautical_dusk_utc,
            astronomical_dusk=window.astronomical_dusk_utc,
            astronomical_dawn=window.astronomical_dawn_utc,
            nautical_dawn=window.nautical_dawn_utc,
            civil_dawn=window.civil_dawn_utc,
            moonrise=window.moonrise_utc,
            moonset=window.moonset_utc,
            moon_illumination=window.moon_illumination,
            moon_waxing=window.moon_waxing,
            moon_up_at_dusk=window.moon_up_at_dusk,
            astronomical_night=[_interval(s) for s in window.astronomical_night],
            dark_intervals=[_interval(s) for s in window.dark_intervals],
            astronomical_night_hours=window.astronomical_night_hours,
            dark_hours=window.dark_hours,
        ),
        score=ScoreModel(
            deep_sky_peak=score.deep_sky_peak,
            deep_sky_mean=score.deep_sky_mean,
            planetary_peak=score.planetary_peak,
            planetary_mean=score.planetary_mean,
            deep_sky_grade=score.deep_sky_grade,
            planetary_grade=score.planetary_grade,
            is_gradeable=score.is_gradeable,
            best_window=_interval(score.best_window) if score.best_window else None,
            best_window_score=score.best_window_score,
            dark_hours=score.dark_hours,
            weather_available=score.weather_available,
            weather_note=score.weather_note,
            weather_sources=list(forecast.sources),
            seeing_estimated=score.seeing_estimated,
            dew_warning=score.dew_warning,
            verdict=verdict(score, window),
            peak_factors_deep_sky=_factors(peak.deep_sky_factors) if peak else None,
            peak_factors_planetary=(_factors(peak.planetary_factors)
                                    if peak else None),
            mean_factors_deep_sky=(_factors(score.mean_factors_deep_sky)
                                   if score.slots else None),
            mean_factors_planetary=(_factors(score.mean_factors_planetary)
                                    if score.slots else None),
            limiting_factor=limiting,
            slots=slots,
        ),
    )


# --- targets ----------------------------------------------------------------

@app.get("/api/targets", response_model=TargetsResponse, tags=["targets"])
def get_targets(date_: str | None = Query(None, alias="date"),
                location: str | None = None,
                scope: str | None = None,
                groups: str | None = Query(None,
                                           description="Comma-separated group names."),
                limit: int = Query(DEFAULT_GROUP_LIMIT, ge=1, le=1000),
                group_by: str = Query("type", pattern="^(type|constellation)$"),
                sort: str = Query("score", pattern="^(score|brightness)$"),
                include_all: bool = Query(
                    False,
                    description="List every catalog object, not just the "
                                "observable ones. Each is badged with its "
                                "visibility tonight.",
                ),
                min_altitude: float = Query(
                    DEFAULT_MIN_ALTITUDE_DEG, ge=0.0, le=89.0,
                    description="Extra altitude floor on top of the site's "
                                "horizon. Send 0 to let the horizon alone "
                                "decide, which is what the web UI does.",
                ),
                session_start: str | None = Query(None),
                session_end: str | None = Query(None),
                ) -> TargetsResponse:
    """Ranked deep-sky targets, grouped by object type."""
    site = _resolve_location(location)
    night_date = _resolve_date(date_, site)
    kit = load_equipment()
    try:
        selected_scope = kit.scope(scope)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0]))

    window = night_window(night_date, site)
    session = resolve_session(window,
                              _session_instant(session_start, "session_start"),
                              _session_instant(session_end, "session_end"))
    span = session or _observing_window(window)
    assessments = assess_targets(window, kit, selected_scope,
                                 min_altitude_deg=min_altitude,
                                 session=session,
                                 logged=observations.logged_object_ids(),
                                 include_too_faint=True)
    if include_all:
        entries = group_catalog(load_catalog(), assessments, by=group_by)
        grouped = {k: [e.assessment or e.obj for e in v] for k, v in entries.items()}
        entry_index = {k: {(_entry_key(e)): e for e in v} for k, v in entries.items()}
    else:
        grouped = group_targets(assessments, limit_per_group=limit,
                                by=group_by, sort=sort)
        entry_index = {}

    if groups:
        wanted = {g.strip().lower() for g in groups.split(",") if g.strip()}
        grouped = {k: v for k, v in grouped.items() if k.lower() in wanted}

    # Resolved once per request against the same catalogue the rows come
    # from, so a Messier number maps to whatever identifier this release
    # uses. Cheap: a set membership test per row.
    showpieces = showpiece_ids(load_catalog())

    def _target(item) -> TargetModel:
        """Accepts an assessment or a bare catalog object."""
        assessment = item if hasattr(item, "obj") else None
        obj = assessment.obj if assessment else item

        facts = deep_sky_facts(obj.name, obj.messier)

        if assessment is None:
            return TargetModel(
                name=obj.name, display_name=obj.display_name, group=obj.group,
                object_type=obj.type_label, messier=obj.messier,
                constellation=obj.constellation,
                ra_deg=obj.ra_deg, dec_deg=obj.dec_deg,
                magnitude=obj.magnitude, size_arcmin=obj.size_arcmin,
                surface_brightness=obj.surface_brightness,
                visible_tonight=False, visible_late=False,
                showpiece=obj.name in showpieces,
                distance_ly=facts.distance_ly if facts else None,
                aliases=list(obj.common_names),
                separation_arcsec=(facts.separation_arcsec
                                   if facts else None),
                component_mags=facts.component_mags if facts else None,
                diameter_ly=facts.diameter_ly if facts else None,
                discovered_by=facts.discovered_by if facts else None,
                discovered_year=facts.discovered_year if facts else None,
                about=facts.note if facts else None,
                notes=[],
            )
        return TargetModel(
            name=obj.name, display_name=obj.display_name, group=obj.group,
            object_type=obj.type_label, messier=obj.messier,
            constellation=obj.constellation,
            ra_deg=obj.ra_deg, dec_deg=obj.dec_deg, magnitude=obj.magnitude,
            size_arcmin=obj.size_arcmin,
            surface_brightness=obj.surface_brightness,
            score=assessment.score,
            peak_altitude_deg=assessment.peak_altitude_deg,
            peak_time=assessment.peak_time_utc,
            hours_above_floor=assessment.hours_above_floor,
            best_window=_interval(assessment.best_window),
            moon_separation_deg=assessment.moon_separation_deg,
            contrast_margin=assessment.contrast_margin,
            visible_tonight=True,
            visible_late=assessment.visible_late,
            too_faint=assessment.too_faint,
            showpiece=obj.name in showpieces,
            distance_ly=facts.distance_ly if facts else None,
            aliases=list(obj.common_names),
            separation_arcsec=facts.separation_arcsec if facts else None,
            component_mags=facts.component_mags if facts else None,
            diameter_ly=facts.diameter_ly if facts else None,
            discovered_by=facts.discovered_by if facts else None,
            discovered_year=facts.discovered_year if facts else None,
            about=facts.note if facts else None,
            notes=list(assessment.notes),
        )

    return TargetsResponse(
        session=_session_model(window, session,
                               asked=bool(session_start or session_end)),
        date=night_date,
        location=_location_model(site),
        scope=selected_scope.name,
        min_altitude_deg=min_altitude,
        total_passing=sum(1 for a in assessments if not a.too_faint),
        total_too_faint=sum(1 for a in assessments if a.too_faint),
        window=_interval(span) if span else None,
        using_true_dark=uses_true_dark(window),
        horizon_warning=_horizon_warning(site, min_altitude),
        group_by=group_by,
        sort=sort,
        include_all=include_all,
        group_info=_group_info(grouped, group_by, site, window, min_altitude,
                               session),
        groups={k: [_target(a) for a in v] for k, v in grouped.items()},
    )


def _entry_key(entry) -> str:
    return entry.obj.name


def _group_info(grouped: dict, group_by: str, site=None, window=None,
                min_altitude: float = DEFAULT_MIN_ALTITUDE_DEG,
                session=None) -> dict[str, GroupInfo]:
    """Header metadata per bucket: centroid and tonight's visibility.

    Sampled over the same span as the targets inside it, for the same reason:
    a constellation's window is a fact about the sky, and clipping it to the
    session made every group appear to set exactly when the observer went to
    bed -- and made "visible late" unreachable, since nothing could start
    after a boundary the sampling stopped at.
    """
    if group_by != "constellation":
        return {key: GroupInfo(key=key, label=key) for key in grouped}

    table = centroids(load_catalog())
    positions = {key: table.get(key.strip().lower()) for key in grouped}

    visibility: dict[str, str] = {}
    if site is not None and window is not None:
        span = session or _observing_window(window)
        if span is not None:
            from datetime import timedelta

            from engine.targets import sampling_bounds
            from engine.timeutil import local_midnight_utc

            outer = sampling_bounds(window, span) if session else span
            # The boundary past which a group counts as late: the hour the
            # observer packs up, or local midnight when they gave no hours.
            late_after = span[1] if session else local_midnight_utc(
                window.date + timedelta(days=1), site.tz)
            visibility = assess_constellations(
                [p for p in positions.values() if p], site,
                outer[0], outer[1], late_after, min_altitude_deg=min_altitude,
            )

    return {
        key: GroupInfo(
            key=key,
            label=constellation_name(key),
            is_constellation=True,
            ra_deg=position.ra_deg if position else None,
            dec_deg=position.dec_deg if position else None,
            spread_deg=position.spread_deg if position else None,
            is_wide=bool(position and position.is_wide),
            visibility=(visibility[key].status if key in visibility else "none"),
            window_start=(visibility[key].start_utc if key in visibility else None),
            window_end=(visibility[key].end_utc if key in visibility else None),
            hours_up=(visibility[key].hours_up if key in visibility else 0.0),
            peak_altitude_deg=(visibility[key].peak_altitude_deg
                               if key in visibility else None),
            has_gap=(visibility[key].has_gap if key in visibility else False),
        )
        for key, position in positions.items()
    }


# --- planets ----------------------------------------------------------------

@app.get("/api/planets", response_model=PlanetsResponse, tags=["planets"])
def get_planets(date_: str | None = Query(None, alias="date"),
                location: str | None = None,
                min_altitude: float = Query(DEFAULT_MIN_ALTITUDE_DEG,
                                            ge=0.0, le=89.0),
                find_next: bool = Query(
                    True,
                    description="Search forward for the next visible date. "
                                "Slow: set false for a fast response.",
                )) -> PlanetsResponse:
    """Planet apparitions: altitude, size, magnitude and apparition trend.

    Deliberately *not* narrowed to the observing session. `observing_span`
    assesses the inner planets from sunset, because Mercury and Venus almost
    never clear an altitude floor with the Sun more than 18 deg down -- a
    session that starts at astronomical dusk would report the brightest
    planet in the sky as unobservable. This tab answers "what planets are
    around tonight", which is a different question from "what can I point at
    between nine and one".
    """
    site = _resolve_location(location)
    night_date = _resolve_date(date_, site)
    window = night_window(night_date, site)

    reports = report_all(site, window, min_altitude_deg=min_altitude,
                         find_next=find_next)
    moon = report_moon(site, window, min_altitude_deg=min_altitude)
    return PlanetsResponse(
        date=night_date,
        location=_location_model(site),
        min_altitude_deg=min_altitude,
        planets=[
            PlanetModel(
                name=r.name, observable=r.observable,
                peak_altitude_deg=r.peak_altitude_deg,
                peak_time=r.peak_time_utc,
                hours_above_floor=r.hours_above_floor,
                magnitude=r.magnitude,
                apparent_diameter_arcsec=r.apparent_diameter_arcsec,
                illuminated_fraction=r.illuminated_fraction,
                distance_au=r.distance_au, elongation_deg=r.elongation_deg,
                trend=r.trend, event_name=r.event_name,
                event_date=r.event_date, days_to_event=r.days_to_event,
                ring_tilt_deg=r.ring_tilt_deg,
                next_visible_date=r.next_visible_date,
                best_altitude_deg=(r.visibility.best_altitude_deg
                                   if r.visibility and r.visibility.floor_unreachable
                                   else None),
                facts=_planet_facts_model(r.name),
                notes=list(r.notes),
            )
            for r in reports
        ],
        moon=MoonModel(
            observable=moon.observable,
            peak_altitude_deg=moon.peak_altitude_deg,
            peak_time=moon.peak_time_utc,
            hours_above_floor=moon.hours_above_floor,
            magnitude=moon.magnitude,
            apparent_diameter_arcsec=moon.apparent_diameter_arcsec,
            illuminated_fraction=moon.illuminated_fraction,
            waxing=moon.waxing,
            distance_km=moon.distance_km,
            elongation_deg=moon.elongation_deg,
            phase_angle_deg=moon.phase_angle_deg,
            age_days=moon.age_days,
            next_phase_name=moon.next_phase_name,
            next_phase_time=moon.next_phase_utc,
            moonrise=moon.moonrise_utc,
            moonset=moon.moonset_utc,
            facts=MoonFactsModel(
                diameter_km=MOON_FACTS.diameter_km,
                sidereal_month_days=MOON_FACTS.sidereal_month_days,
                synodic_month_days=MOON_FACTS.synodic_month_days,
                mean_distance_km=MOON_FACTS.mean_distance_km,
                visible_surface_fraction=MOON_FACTS.visible_surface_fraction,
                about=MOON_FACTS.note,
            ),
            notes=list(moon.notes),
        ),
    )


# --- events -----------------------------------------------------------------

@app.get("/api/events", response_model=EventsResponse, tags=["events"])
def get_events(from_: str | None = Query(None, alias="from"),
               days: int = Query(90, ge=1, le=730),
               location: str | None = None) -> EventsResponse:
    """Meteor showers, lunar eclipses and conjunctions in a forward window."""
    site = _resolve_location(location)
    start = _resolve_date(from_, site)

    showers = []
    for shower, peak in showers_active_between(start, days, load_meteor_showers()):
        forecast = assess_shower(shower, site, night_window(peak, site))
        showers.append(ShowerModel(
            name=shower.name, code=shower.code, peak_date=peak,
            zhr=shower.zhr, velocity_km_s=shower.velocity_km_s,
            parent=shower.parent,
            best_time=forecast.best_time_utc,
            best_radiant_altitude_deg=forecast.best_radiant_altitude_deg,
            estimated_rate_per_hour=forecast.best_rate,
            moon_illumination=forecast.moon_illumination,
            notes=list(forecast.notes),
        ))

    return EventsResponse(
        from_date=start, days=days, location=_location_model(site),
        showers=showers,
        lunar_eclipses=[
            EclipseModel(time=e.time_utc, kind=e.kind,
                         moon_altitude_deg=e.moon_altitude_deg,
                         visible_from_location=e.visible)
            for e in lunar_eclipses(start, days, site)
        ],
        conjunctions=[
            ConjunctionModel(time=c.time_utc, body_a=c.body_a, body_b=c.body_b,
                             separation_deg=c.separation_deg,
                             involves_moon=c.involves_moon)
            for c in conjunctions(start, days)
        ],
    )


# --- altitude chart ---------------------------------------------------------

def _planet_facts_model(name: str) -> PlanetFactsModel | None:
    """Constants for a planet, straight from `engine/reference.py`."""
    facts = planet_facts(name)
    if facts is None:
        return None
    return PlanetFactsModel(
        equatorial_diameter_km=facts.equatorial_diameter_km,
        rotation_hours=facts.rotation_hours,
        year_earth_years=facts.year_earth_years,
        moons=facts.moons,
        discovered_by=facts.discovered_by,
        discovered_year=facts.discovered_year,
        about=facts.note,
    )


@app.get("/api/altitude", response_model=AltitudeResponse, tags=["night"])
def get_altitude(date_: str | None = Query(None, alias="date"),
                 location: str | None = None,
                 bodies: str = Query(
                     "moon,saturn,jupiter",
                     description="Comma-separated planets, plus sun/moon.",
                 ),
                 objects: str | None = Query(
                     None,
                     description="Comma-separated catalog designations, e.g. M31,M13.",
                 ),
                 constellations: str | None = Query(
                     None,
                     description="Comma-separated IAU abbreviations, e.g. Cyg,Peg. "
                                 "Plotted at the constellation's centroid.",
                 ),
                 step_minutes: int = Query(15, ge=5, le=60),
                 min_altitude: float = Query(DEFAULT_MIN_ALTITUDE_DEG,
                                             ge=0.0, le=89.0)) -> AltitudeResponse:
    """Altitude-vs-time curves — PLAN.md §4's highest-value visual."""
    site = _resolve_location(location)
    night_date = _resolve_date(date_, site)
    window = night_window(night_date, site)

    # Normally sunset to sunrise. Inside the polar circles there may be
    # neither, and the chart still has something to show: fall back to
    # astronomical night, then to the whole local day. Erroring here left
    # arctic sites with no chart at all for half the year.
    if window.sunset_utc and window.sunrise_utc:
        start, end = window.sunset_utc, window.sunrise_utc
    elif window.astronomical_night:
        start = window.astronomical_night[0][0]
        end = window.astronomical_night[-1][1]
    else:
        start = local_noon_utc(night_date, site.tz)
        end = local_noon_utc(night_date + timedelta(days=1), site.tz)
    step = timedelta(minutes=step_minutes)
    series: list[AltitudeSeriesModel] = []

    for body in (b.strip().lower() for b in bodies.split(",") if b.strip()):
        if body not in set(ALL_PLANETS) | {"sun", "moon"}:
            raise HTTPException(status_code=422, detail=f"unknown body {body!r}")
        sampled = altaz_series(body, site, start, end, step)
        series.append(AltitudeSeriesModel(
            label=body, kind="moon" if body == "moon" else
                            ("sun" if body == "sun" else "planet"),
            points=[
                AltitudePointModel(time=t, altitude_deg=a, azimuth_deg=z)
                for t, a, z in zip(sampled.times_utc, sampled.alt_deg,
                                   sampled.az_deg)
            ],
        ))

    if objects:
        from skyfield.api import Star

        from engine.ephem import _observer, load_ephemeris

        catalog = load_catalog()
        eph = load_ephemeris()
        observer = _observer(eph, site)

        stamps, cursor = [], start
        while cursor <= end:
            stamps.append(cursor)
            cursor += step
        times = eph.timescale.from_datetimes(stamps)

        for designation in (o.strip() for o in objects.split(",") if o.strip()):
            obj = find_object(designation, catalog)
            if obj is None:
                raise HTTPException(status_code=404,
                                    detail=f"unknown object {designation!r}")
            star = Star(ra_hours=obj.ra_deg / 15.0, dec_degrees=obj.dec_deg)
            alt, az, _ = observer.at(times).observe(star).apparent().altaz()
            series.append(AltitudeSeriesModel(
                label=obj.display_name, kind="deep-sky",
                points=[
                    AltitudePointModel(time=t, altitude_deg=float(a),
                                       azimuth_deg=float(z))
                    for t, a, z in zip(stamps, alt.degrees, az.degrees)
                ],
            ))

    if constellations:
        from skyfield.api import Star

        from engine.ephem import _observer, load_ephemeris

        catalog = load_catalog()
        eph = load_ephemeris()
        observer = _observer(eph, site)

        stamps, cursor = [], start
        while cursor <= end:
            stamps.append(cursor)
            cursor += step
        times = eph.timescale.from_datetimes(stamps)

        for abbreviation in (c.strip() for c in constellations.split(",") if c.strip()):
            position = centroid(abbreviation, catalog)
            if position is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"unknown constellation {abbreviation!r}",
                )
            star = Star(ra_hours=position.ra_deg / 15.0,
                        dec_degrees=position.dec_deg)
            alt, az, _ = observer.at(times).observe(star).apparent().altaz()
            series.append(AltitudeSeriesModel(
                label=position.name, kind="constellation",
                points=[
                    AltitudePointModel(time=t, altitude_deg=float(a),
                                       azimuth_deg=float(z))
                    for t, a, z in zip(stamps, alt.degrees, az.degrees)
                ],
            ))

    return AltitudeResponse(
        date=night_date, location=_location_model(site),
        start=start, end=end,
        astronomical_night=[_interval(s) for s in window.astronomical_night],
        dark_intervals=[_interval(s) for s in window.dark_intervals],
        min_altitude_deg=min_altitude,
        horizon_at_azimuth={
            str(azimuth): site.horizon.min_altitude_at(azimuth)
            for azimuth in range(0, 360, 10)
        },
        series=series,
    )


# --- locations --------------------------------------------------------------

@app.get("/api/locations", response_model=list[LocationModel], tags=["locations"])
def list_locations() -> list[LocationModel]:
    """Configured sites plus any added at runtime."""
    return [_location_model(loc) for loc in
            sorted(store.all_locations().values(), key=lambda x: x.key)]


@app.get("/api/geocode", response_model=list[GeocodeCandidate], tags=["locations"])
def geocode_search(q: str = Query(..., min_length=1)) -> list[GeocodeCandidate]:
    """Place name -> candidate coordinates. Empty list if offline."""
    return [
        GeocodeCandidate(label=r.label, name=r.name, lat=r.lat, lon=r.lon,
                         elevation_m=r.elevation_m,
                         suggested_key=r.suggested_key())
        for r in geocode.search(q)
    ]


@app.post("/api/locations", response_model=LocationModel, status_code=201,
          tags=["locations"])
def create_location(request: NewLocationRequest) -> LocationModel:
    """Add a site, either by explicit coordinates or by geocoding a name."""
    lat, lon = request.lat, request.lon
    name = request.name
    elevation = request.elevation_m

    if lat is None or lon is None:
        if not request.query:
            raise HTTPException(
                status_code=422,
                detail="provide either lat and lon, or a query to geocode",
            )
        candidates = geocode.search(request.query, count=1)
        if not candidates:
            raise HTTPException(
                status_code=404,
                detail=f"no match for {request.query!r} "
                       "(the geocoder may also be unreachable)",
            )
        best = candidates[0]
        lat, lon = best.lat, best.lon
        name = name or best.label
        elevation = elevation if elevation is not None else best.elevation_m

    key = request.key or (name or "location").lower().replace(" ", "_")
    key = "".join(c if c.isalnum() or c == "_" else "_" for c in key).strip("_")
    if not key:
        raise HTTPException(status_code=422, detail="could not derive a key")

    from engine.locations import _resolve_tz

    try:
        location = Location(
            key=key, name=name or key, lat=lat, lon=lon,
            elevation_m=elevation or 0.0, bortle=request.bortle,
            tz=_resolve_tz(lat, lon),
            horizon=build_horizon(request.horizon, request.horizon_facing),
            atlas_sqm=atlas_sqm_for(lat, lon, request.bortle),
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    store.save_location(location)
    return _location_model(location, source="stored")


@app.delete("/api/locations/{key}", status_code=204, tags=["locations"])
def remove_location(key: str) -> None:
    """Delete a runtime-added site. YAML-configured sites cannot be deleted."""
    from engine.locations import load_locations

    if key in load_locations():
        raise HTTPException(
            status_code=409,
            detail=f"{key!r} is defined in config/locations.yaml; "
                   "edit that file to remove it",
        )
    if not store.delete_location(key):
        raise HTTPException(status_code=404, detail=f"unknown location {key!r}")


@app.get("/api/skybrightness", response_model=SkyBrightnessCoverage,
         tags=["locations"])
def skybrightness_coverage() -> SkyBrightnessCoverage:
    """Where the configured light-pollution atlas has data, if there is one."""
    from engine.skybrightness import coverage_bounds, is_configured

    bounds = coverage_bounds()
    return SkyBrightnessCoverage(
        configured=is_configured(),
        bounds=list(bounds) if bounds else None,
    )


@app.get("/api/skybrightness/at", response_model=SkyBrightnessReading,
         tags=["locations"])
def skybrightness_at(lat: float = Query(..., ge=-90.0, le=90.0),
                     lon: float = Query(..., ge=-180.0, le=180.0),
                     ) -> SkyBrightnessReading:
    """What the atlas says about one point, for the add-site form.

    Lets the form fill the Bortle class in rather than asking the observer to
    recall one. Outside coverage the answer is null, which the form treats as
    "you will have to tell me" rather than quietly assuming a suburban sky.
    """
    from engine.skybrightness import bortle_from_sqm, sqm_at

    sqm = sqm_at(lat, lon)
    return SkyBrightnessReading(
        sqm=sqm,
        bortle=None if sqm is None else bortle_from_sqm(sqm),
        in_coverage=sqm is not None,
    )


@app.get("/api/horizon/marks", response_model=HorizonMarksResponse,
         tags=["locations"])
def horizon_marks(lat: float = Query(..., ge=-90.0, le=90.0),
                  lon: float = Query(..., ge=-180.0, le=180.0),
                  azimuth: float = Query(..., ge=0.0, lt=360.0),
                  at: str | None = Query(
                      None, description="ISO-8601 UTC instant; defaults to now."),
                  ) -> HorizonMarksResponse:
    """Constellations toward a bearing, lowest first, for measuring a horizon.

    No astronomy happens here -- `engine.constellations.marks_toward` does the
    work, as PLAN.md §4 requires.
    """
    from engine.constellations import marks_toward

    if at:
        try:
            when = datetime.fromisoformat(at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=422,
                                detail=f"{at!r} is not an ISO-8601 datetime")
        if when.tzinfo is None:
            raise HTTPException(
                status_code=422,
                detail="`at` must carry a timezone; this API is UTC-only",
            )
    else:
        when = now_utc()

    from engine.locations import _resolve_tz

    site = Location(key="probe", name="probe", lat=lat, lon=lon,
                    tz=_resolve_tz(lat, lon))
    marks = marks_toward(azimuth, site, when, load_catalog())
    return HorizonMarksResponse(
        azimuth_deg=azimuth,
        at=when,
        marks=[SkyMarkModel(abbreviation=m.abbreviation, name=m.name,
                            altitude_deg=m.altitude_deg,
                            azimuth_deg=m.azimuth_deg)
               for m in marks],
    )


@app.get("/api/finder", response_model=FinderChartModel, tags=["targets"])
def get_finder(location: str | None = None,
               target: str | None = Query(
                   None, description="Catalogue id or name, e.g. NGC6205 or M13."),
               body: str | None = Query(
                   None, description="moon or a planet, instead of a target."),
               at: str = Query(..., description="ISO-8601 UTC instant to draw the sky at."),
               radius: float = Query(10.0, ge=1.0, le=45.0,
                                     description="Half-width of the field, degrees."),
               orientation: str = Query("sky", pattern="^(sky|north)$"),
               ) -> FinderChartModel:
    """A finder chart around a target or body, for star hopping.

    The engine lays it out -- `engine.starchart` -- in chart units; this only
    resolves what was asked for and passes the result through.
    """
    from engine.starchart import body_position, finder_chart

    site = _resolve_location(location)
    try:
        when = datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{at!r} is not an ISO-8601 datetime")
    if when.tzinfo is None:
        raise HTTPException(status_code=422,
                            detail="`at` must carry a timezone; this API is UTC-only")

    catalog = load_catalog()
    if body:
        name = body.strip().lower()
        if name not in ("moon", *ALL_PLANETS):
            raise HTTPException(status_code=404, detail=f"No body called {body!r}")
        ra, dec = body_position(name, when)
        subject, exclude = name.capitalize(), name
    elif target:
        obj = find_object(target, catalog)
        if obj is None:
            raise HTTPException(status_code=404, detail=f"No catalogue object {target!r}")
        ra, dec = obj.ra_deg, obj.dec_deg
        subject, exclude = obj.display_name, None
    else:
        raise HTTPException(status_code=422, detail="Give a `target` or a `body`")

    # The showpieces are the neighbours worth marking: the ones a hop might
    # pass, or that could be mistaken for the target. The engine keeps
    # those that fall in the frame.
    ids = showpiece_ids(catalog)
    nearby = [(o.ra_deg, o.dec_deg,
               f"M{o.messier}" if o.messier else o.display_name, o.group)
              for o in catalog if o.name in ids and (target is None or o.name != obj.name)]

    chart = finder_chart(ra, dec, site, when, radius_deg=radius,
                         orientation=orientation, nearby_positions=nearby,
                         exclude_body=exclude)
    point = lambda p: ChartPointModel(x=p.x, y=p.y, label=p.label, mag=p.mag, kind=p.kind)
    return FinderChartModel(
        target=subject,
        center_ra_deg=chart.center_ra_deg, center_dec_deg=chart.center_dec_deg,
        at=chart.at_utc, orientation=chart.orientation, radius_deg=chart.radius_deg,
        limiting_mag=chart.limiting_mag,
        center_alt_deg=chart.center_alt_deg, center_az_deg=chart.center_az_deg,
        stars=[point(p) for p in chart.stars],
        lines=chart.lines,
        objects=[point(p) for p in chart.objects],
        horizon=chart.horizon,
        directions=[point(p) for p in chart.directions],
    )


@app.get("/api/health", tags=["meta"])
def health() -> dict:
    from engine.ephem import ephemeris_is_cached

    return {
        "status": "ok",
        "ephemeris_cached": ephemeris_is_cached(),
        "catalog_objects": len(load_catalog()),
    }


# Log routes live in their own module now that the API covers five domains.
# They reuse the resolvers above so location and date handling stays identical.
from .log_routes import register as _register_log_routes  # noqa: E402

_register_log_routes(app, _resolve_location, _resolve_date)
