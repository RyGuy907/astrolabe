"""`planner log ...` — the observation log from the command line.

The field workflow this is built for: run `planner log start` before you go
out, `planner log add` as you go, and let the conditions snapshot be taken
automatically so the entry still means something once the forecast is gone.
"""

from __future__ import annotations

from datetime import date, datetime

import typer

from db import observations
from engine.catalog.loader import find_object, load_catalog
from engine.ephem import night_window
from engine.equipment import load_equipment
from engine.locations import LocationError, get_location
from engine.scoring import score_night
from engine.targets import assess_targets, group_targets
from engine.timeutil import format_local, now_utc, resolve_night_date
from engine.weather import get_forecast

log_app = typer.Typer(add_completion=False, help="Observation log.")

DATE_FORMAT = "%Y-%m-%d"


def _resolve(location_key: str | None, date_str: str | None):
    try:
        loc = get_location(location_key)
    except LocationError as exc:
        typer.secho(exc.args[0], fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    if date_str:
        try:
            night_date = datetime.strptime(date_str, DATE_FORMAT).date()
        except ValueError:
            typer.secho(f"{date_str!r} is not a valid date; expected YYYY-MM-DD",
                        fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2)
    else:
        night_date = resolve_night_date(now_utc(), loc.tz)
    return loc, night_date


def _snapshot(loc, night_date) -> dict:
    window = night_window(night_date, loc)
    spans = window.astronomical_night
    forecast = get_forecast(loc,
                            start=spans[0][0] if spans else None,
                            end=spans[-1][1] if spans else None)
    return observations.snapshot_conditions(window, score_night(window, forecast))


@log_app.command("start")
def start(
    date_str: str = typer.Option(None, "--date", "-d", metavar="YYYY-MM-DD"),
    location_key: str = typer.Option(None, "--location", "-l"),
    scope_key: str = typer.Option(None, "--scope", "-s"),
    notes: str = typer.Option(None, "--notes"),
    seeing: int = typer.Option(None, "--seeing", min=1, max=5,
                               help="Actual seeing, 1 (poor) to 5 (excellent)."),
    transparency: int = typer.Option(None, "--transparency", min=1, max=5),
) -> None:
    """Start a session and freeze the night's conditions onto it."""
    loc, night_date = _resolve(location_key, date_str)
    kit = load_equipment()
    scope = kit.scope(scope_key)

    session = observations.create_session(observations.Session(
        date=night_date, location_key=loc.key, scope_key=scope.key,
        conditions=_snapshot(loc, night_date),
        seeing_actual=seeing, transparency_actual=transparency, notes=notes,
    ))

    typer.echo("")
    typer.secho(f"Session {session.id} started - {night_date} at {loc.name}",
                fg=typer.colors.GREEN)
    conditions = session.conditions or {}
    if conditions.get("is_gradeable"):
        typer.echo(f"  conditions frozen: deep-sky {conditions['deep_sky_peak']:.0f} "
                   f"({conditions['deep_sky_grade']}), "
                   f"{conditions['dark_hours']:.1f} h true dark, "
                   f"moon {conditions['moon_illumination'] * 100:.0f}%")
    else:
        typer.echo(f"  conditions frozen: {conditions.get('dark_hours', 0):.1f} h "
                   f"true dark, moon "
                   f"{conditions.get('moon_illumination', 0) * 100:.0f}% "
                   "(no weather, so no grade)")
    typer.echo(f"\n  planner log add {session.id} M31 --rating 4\n")


@log_app.command("suggest")
def suggest(
    date_str: str = typer.Option(None, "--date", "-d", metavar="YYYY-MM-DD"),
    location_key: str = typer.Option(None, "--location", "-l"),
    limit: int = typer.Option(5, "--limit", "-n"),
) -> None:
    """List tonight's recommended targets, ready to log.

    PLAN.md 5's key move: the log is pre-populated from what was actually
    recommended, rather than typed from memory.
    """
    loc, night_date = _resolve(location_key, date_str)
    kit = load_equipment()
    already = observations.logged_object_ids()

    window = night_window(night_date, loc)
    grouped = group_targets(
        assess_targets(window, kit, logged=already), limit_per_group=limit)

    typer.echo("")
    typer.echo(f"Suggested targets for {night_date} at {loc.name}")
    typer.echo(f"  {'score':>5}  {'object':<34} {'id':<12} {'peak':>6}  seen")
    for group, targets in grouped.items():
        typer.echo(f"\n  {group}")
        for a in targets:
            seen = "yes" if a.obj.name in already else ""
            typer.echo(f"  {a.score:5.1f}  {a.obj.display_name[:34]:<34} "
                       f"{a.obj.name:<12} {a.peak_altitude_deg:5.0f}d  {seen}")
    typer.echo("")


@log_app.command("add")
def add(
    session_id: int = typer.Argument(..., help="Session id from `log start`."),
    designation: str = typer.Argument(..., help="e.g. M31, NGC 7000, or free text."),
    rating: int = typer.Option(None, "--rating", "-r", min=1, max=5),
    eyepiece: str = typer.Option(None, "--eyepiece", "-e"),
    notes: str = typer.Option(None, "--notes"),
) -> None:
    """Log an object. Resolves catalog designations; free text is kept as-is."""
    obj = find_object(designation, load_catalog())
    if obj is None:
        typer.secho(f"  {designation!r} is not in the catalog - logging as free text.",
                    fg=typer.colors.YELLOW)

    try:
        observation = observations.add_observation(
            session_id,
            observations.Observation(
                object_id=obj.name if obj else None,
                object_name=obj.display_name if obj else designation,
                observed_at_utc=now_utc(),
                eyepiece=eyepiece, notes=notes, rating=rating,
            ),
        )
    except observations.LogError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    stars = "*" * observation.rating if observation.rating else ""
    typer.secho(f"  logged {observation.object_name} {stars}", fg=typer.colors.GREEN)


@log_app.command("list")
def list_sessions(
    limit: int = typer.Option(20, "--limit", "-n"),
    location_key: str = typer.Option(None, "--location", "-l"),
) -> None:
    """Recent sessions, newest first."""
    sessions = observations.list_sessions(limit=limit, location_key=location_key)
    if not sessions:
        typer.echo("\n  No sessions logged yet. Start one with `planner log start`.\n")
        return

    typer.echo("")
    typer.echo(f"  {'id':>4}  {'date':<12} {'location':<20} {'objects':>7}  conditions")
    for session in sessions:
        conditions = session.conditions or {}
        summary = "-"
        if conditions:
            grade = (conditions.get("deep_sky_grade")
                     if conditions.get("is_gradeable") else "n/a")
            summary = (f"deep-sky {grade}, "
                       f"{conditions.get('dark_hours', 0):.1f} h dark, "
                       f"moon {conditions.get('moon_illumination', 0) * 100:.0f}%")
        typer.echo(f"  {session.id:>4}  {session.date.isoformat():<12} "
                   f"{session.location_key:<20} {len(session.observations):>7}  {summary}")
    typer.echo("")


@log_app.command("show")
def show(session_id: int = typer.Argument(...)) -> None:
    """Everything recorded for one session."""
    session = observations.get_session(session_id)
    if session is None:
        typer.secho(f"no session {session_id}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    try:
        tz = get_location(session.location_key).tz
    except LocationError:
        tz = "UTC"

    typer.echo("")
    typer.echo(f"Session {session.id} - {session.date} at {session.location_key}")
    if session.scope_key:
        typer.echo(f"  scope {session.scope_key}")
    if session.seeing_actual or session.transparency_actual:
        typer.echo(f"  seeing {session.seeing_actual or '-'}/5   "
                   f"transparency {session.transparency_actual or '-'}/5")
    if session.notes:
        typer.echo(f"  notes: {session.notes}")

    conditions = session.conditions or {}
    if conditions:
        typer.echo("")
        typer.echo("  Conditions, as recorded that night")
        if conditions.get("is_gradeable"):
            typer.echo(f"    deep-sky {conditions['deep_sky_peak']:.0f} "
                       f"({conditions['deep_sky_grade']})   "
                       f"planetary {conditions['planetary_peak']:.0f} "
                       f"({conditions['planetary_grade']})")
        else:
            typer.echo("    no weather that night, so no grade was recorded")
        typer.echo(f"    {conditions.get('dark_hours', 0):.2f} h true dark, "
                   f"moon {conditions.get('moon_illumination', 0) * 100:.0f}%")

    typer.echo("")
    if not session.observations:
        typer.echo("  Nothing logged.")
    else:
        typer.echo(f"  Observations ({len(session.observations)})")
        for observation in session.observations:
            stars = "*" * observation.rating if observation.rating else ""
            when = (format_local(observation.observed_at_utc, tz)
                    if observation.observed_at_utc else "-")
            typer.echo(f"    {when:>6}  {observation.object_name:<34} "
                       f"{observation.eyepiece or '-':<22} {stars}")
            if observation.notes:
                typer.echo(f"            {observation.notes}")
    typer.echo("")


@log_app.command("history")
def history(designation: str = typer.Argument(...)) -> None:
    """Every time an object has been logged."""
    obj = find_object(designation, load_catalog())
    object_id = obj.name if obj else designation
    entries = observations.object_history(object_id)

    typer.echo("")
    label = obj.display_name if obj else designation
    if not entries:
        typer.echo(f"  {label} has never been logged.\n")
        return

    typer.echo(f"  {label} - observed {len(entries)} time(s)")
    for entry in entries:
        stars = "*" * entry["rating"] if entry["rating"] else ""
        typer.echo(f"    {entry['session_date']}  {entry['location_key']:<20} "
                   f"{entry['eyepiece'] or '-':<22} {stars}")
        if entry["notes"]:
            typer.echo(f"      {entry['notes']}")
    typer.echo("")


@log_app.command("stats")
def stats() -> None:
    """Totals across the whole log."""
    data = observations.log_statistics()
    typer.echo("")
    typer.echo(f"  sessions          {data['sessions']}")
    typer.echo(f"  observations      {data['observations']}")
    typer.echo(f"  distinct objects  {data['distinct_objects']}")
    if data["first_session"]:
        typer.echo(f"  span              {data['first_session']} to {data['last_session']}")
    typer.echo("")


@log_app.command("delete")
def delete(session_id: int = typer.Argument(...),
           yes: bool = typer.Option(False, "--yes",
                                    help="Skip the confirmation prompt.")) -> None:
    """Delete a session and everything logged in it."""
    session = observations.get_session(session_id)
    if session is None:
        typer.secho(f"no session {session_id}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    if not yes:
        typer.echo(f"Session {session_id}: {session.date} at "
                   f"{session.location_key}, {len(session.observations)} "
                   "observation(s).")
        typer.confirm("Delete it permanently?", abort=True)

    observations.delete_session(session_id)
    typer.secho(f"  deleted session {session_id}", fg=typer.colors.GREEN)
