"""Observation log endpoints (PLAN.md §5).

Kept in its own module so `main.py` stays readable now that the API covers
five domains. Registered onto the app via `register(app)`.

Same rule as everywhere else in `api/`: no astronomy or persistence logic
lives here. It resolves inputs, calls `db.observations` and `engine`, and
serialises the result.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query

from db import observations
from engine.ephem import night_window
from engine.equipment import load_equipment
from engine.scoring import score_night
from engine.targets import assess_targets, group_targets
from engine.weather import get_forecast

from .schemas import (
    LogCandidateModel,
    LogPrefillResponse,
    LogStatsModel,
    NewObservationRequest,
    NewSessionRequest,
    ObjectHistoryModel,
    ObservationModel,
    SessionModel,
)


def _observation_model(observation) -> ObservationModel:
    return ObservationModel(
        id=observation.id,
        session_id=observation.session_id,
        object_id=observation.object_id,
        object_name=observation.object_name,
        observed_at_utc=observation.observed_at_utc,
        eyepiece=observation.eyepiece,
        notes=observation.notes,
        rating=observation.rating,
        sketch_path=observation.sketch_path,
    )


def _session_model(session) -> SessionModel:
    return SessionModel(
        id=session.id,
        date=session.date,
        location_key=session.location_key,
        start_utc=session.start_utc,
        end_utc=session.end_utc,
        scope_key=session.scope_key,
        conditions=session.conditions,
        seeing_actual=session.seeing_actual,
        transparency_actual=session.transparency_actual,
        notes=session.notes,
        observations=[_observation_model(o) for o in session.observations],
    )


def _snapshot(site, night_date) -> dict:
    """Freeze the night's conditions so the entry outlives the forecast."""
    window = night_window(night_date, site)
    spans = window.astronomical_night
    forecast = get_forecast(site,
                            start=spans[0][0] if spans else None,
                            end=spans[-1][1] if spans else None)
    return observations.snapshot_conditions(window, score_night(window, forecast))


def register(app: FastAPI, resolve_location, resolve_date) -> None:
    """Attach the log routes, reusing main.py's resolvers."""

    @app.get("/api/log/prefill", response_model=LogPrefillResponse, tags=["log"])
    def log_prefill(date_: str | None = Query(None, alias="date"),
                    location: str | None = None,
                    scope: str | None = None,
                    limit: int = Query(8, ge=1, le=50)) -> LogPrefillResponse:
        """The night's recommended targets, ready to tick off.

        PLAN.md §5's key UX move: pre-populate the log from what was actually
        recommended that night instead of making the observer retype it.
        """
        site = resolve_location(location)
        night_date = resolve_date(date_, site)
        kit = load_equipment()
        try:
            selected_scope = kit.scope(scope)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc.args[0]))

        window = night_window(night_date, site)
        already = observations.logged_object_ids()
        assessments = assess_targets(window, kit, selected_scope, logged=already)
        grouped = group_targets(assessments, limit_per_group=limit)

        return LogPrefillResponse(
            date=night_date,
            location_key=site.key,
            scope_key=selected_scope.key,
            candidates=[
                LogCandidateModel(
                    object_id=a.obj.name,
                    object_name=a.obj.display_name,
                    group=a.group,
                    score=a.score,
                    peak_altitude_deg=a.peak_altitude_deg,
                    already_logged=a.obj.name in already,
                )
                for targets in grouped.values()
                for a in targets
            ],
            conditions=_snapshot(site, night_date),
        )

    @app.post("/api/sessions", response_model=SessionModel, status_code=201,
              tags=["log"])
    def create_session(request: NewSessionRequest) -> SessionModel:
        """Start a session, optionally freezing the night's conditions on it."""
        site = resolve_location(request.location)
        night_date = resolve_date(request.date, site)

        kit = load_equipment()
        try:
            selected_scope = kit.scope(request.scope)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc.args[0]))

        conditions = _snapshot(site, night_date) if request.snapshot_conditions else None

        try:
            created = observations.create_session(observations.Session(
                date=night_date,
                location_key=site.key,
                scope_key=selected_scope.key,
                conditions=conditions,
                seeing_actual=request.seeing_actual,
                transparency_actual=request.transparency_actual,
                notes=request.notes,
            ))
        except observations.LogError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return _session_model(created)

    @app.get("/api/sessions", response_model=list[SessionModel], tags=["log"])
    def list_sessions(limit: int = Query(50, ge=1, le=500),
                      location: str | None = None) -> list[SessionModel]:
        return [
            _session_model(s)
            for s in observations.list_sessions(limit=limit, location_key=location)
        ]

    @app.get("/api/sessions/{session_id}", response_model=SessionModel,
             tags=["log"])
    def get_session(session_id: int) -> SessionModel:
        session = observations.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"no session {session_id}")
        return _session_model(session)

    @app.delete("/api/sessions/{session_id}", status_code=204, tags=["log"])
    def remove_session(session_id: int) -> None:
        if not observations.delete_session(session_id):
            raise HTTPException(status_code=404, detail=f"no session {session_id}")

    @app.post("/api/sessions/{session_id}/observations",
              response_model=ObservationModel, status_code=201, tags=["log"])
    def add_observation(session_id: int,
                        request: NewObservationRequest) -> ObservationModel:
        try:
            created = observations.add_observation(
                session_id,
                observations.Observation(
                    object_name=request.object_name,
                    object_id=request.object_id,
                    observed_at_utc=request.observed_at_utc,
                    eyepiece=request.eyepiece,
                    notes=request.notes,
                    rating=request.rating,
                    sketch_path=request.sketch_path,
                ),
            )
        except observations.LogError as exc:
            missing_session = "no session" in str(exc)
            raise HTTPException(status_code=404 if missing_session else 422,
                                detail=str(exc))
        return _observation_model(created)

    @app.delete("/api/observations/{observation_id}", status_code=204,
                tags=["log"])
    def remove_observation(observation_id: int) -> None:
        if not observations.delete_observation(observation_id):
            raise HTTPException(status_code=404,
                                detail=f"no observation {observation_id}")

    @app.get("/api/objects/{object_id}/history",
             response_model=ObjectHistoryModel, tags=["log"])
    def object_history(object_id: str) -> ObjectHistoryModel:
        """Every time this object has been logged, newest first."""
        entries = observations.object_history(object_id)
        return ObjectHistoryModel(object_id=object_id,
                                  times_observed=len(entries), entries=entries)

    @app.get("/api/log/stats", response_model=LogStatsModel, tags=["log"])
    def log_stats() -> LogStatsModel:
        return LogStatsModel(**observations.log_statistics())
