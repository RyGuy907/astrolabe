"""Command-line front end. The display layer, and the only place UTC becomes local.

Everything below the CLI speaks tz-aware UTC. Every local rendering here goes
through `engine.timeutil.to_local` (via format_local), so the conversion
boundary stays greppable.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import typer

from engine.ephem import Interval, NightWindow, ephemeris_is_cached, night_window
from engine.equipment import load_equipment
from engine.events import (
    MOON_CONJUNCTION_DEG,
    PLANET_CONJUNCTION_DEG,
    assess_shower,
    conjunctions,
    lunar_eclipses,
    showers_active_between,
)
from engine.locations import LocationError, get_location
from engine.planets import DEFAULT_MIN_ALTITUDE_DEG as PLANET_MIN_ALTITUDE
from engine.planets import report_all
from engine.targets import (
    DEFAULT_GROUP_LIMIT,
    DEFAULT_MIN_ALTITUDE_DEG,
    _observing_window,
    assess_targets,
    group_targets,
    uses_true_dark,
)
from engine.scoring import score_night, verdict
from engine.timeutil import (format_local, format_utc, now_utc,
                             resolve_night_date, to_local)
from engine.weather import get_forecast

app = typer.Typer(
    add_completion=False,
    help="Astronomy night planner: is tonight worth going out, and what should I point at?",
)

DATE_FORMAT = "%Y-%m-%d"


def _parse_date(raw: str | None, tz: str) -> tuple[date, bool]:
    """Parse --date, or resolve "tonight" per PLAN.md 3.1.

    Returns (night_date, was_defaulted).
    """
    if raw:
        try:
            return datetime.strptime(raw, DATE_FORMAT).date(), False
        except ValueError as exc:
            raise typer.BadParameter(
                f"{raw!r} is not a valid date; expected YYYY-MM-DD"
            ) from exc
    return resolve_night_date(now_utc(), tz), True


def _render_intervals(spans: list[Interval], tz: str, indent: str = "  ") -> list[str]:
    if not spans:
        return [f"{indent}none"]
    return [
        f"{indent}{format_local(s, tz)} -> {format_local(e, tz)}"
        f"   ({(e - s).total_seconds() / 3600:.2f} h)"
        for s, e in spans
    ]


def _moon_phase_name(illumination: float, waxing: bool) -> str:
    """A coarse phase label. Illumination alone is ambiguous without waxing."""
    if illumination < 0.02:
        return "new"
    if illumination > 0.98:
        return "full"
    side = "waxing" if waxing else "waning"
    if illumination < 0.45:
        return f"{side} crescent"
    if illumination <= 0.55:
        return f"first quarter" if waxing else "last quarter"
    return f"{side} gibbous"


def _print_night(window: NightWindow, tz: str, defaulted: bool) -> None:
    loc = window.location
    zone = ZoneInfo(tz)
    tz_label = datetime.now(zone).tzname() or tz

    tonight = "  (tonight)" if defaulted else ""
    typer.echo("")
    typer.echo(f"Night of {window.date.isoformat()}{tonight} - {loc.name} ({loc.key})")
    typer.echo(
        f"{loc.lat:.4f}, {loc.lon:.4f}   {loc.elevation_m:.0f} m"
        + (f"   Bortle {loc.bortle} (SQM {loc.sqm})" if loc.bortle else "")
    )
    typer.echo(f"All times {tz_label} ({tz}) unless marked UTC.")
    typer.echo("")

    typer.echo("Sun")
    typer.echo(f"  {'sunset':<24} {format_local(window.sunset_utc, tz)}")
    typer.echo(f"  {'civil twilight ends':<24} {format_local(window.civil_dusk_utc, tz)}")
    typer.echo(f"  {'nautical twilight ends':<24} {format_local(window.nautical_dusk_utc, tz)}")
    typer.echo(f"  {'astronomical dusk':<24} {format_local(window.astronomical_dusk_utc, tz)}   (sun < -18)")
    typer.echo(f"  {'astronomical dawn':<24} {format_local(window.astronomical_dawn_utc, tz)}")
    typer.echo(f"  {'nautical twilight begins':<24} {format_local(window.nautical_dawn_utc, tz)}")
    typer.echo(f"  {'civil twilight begins':<24} {format_local(window.civil_dawn_utc, tz)}")
    typer.echo(f"  {'sunrise':<24} {format_local(window.sunrise_utc, tz)}")
    typer.echo("")

    typer.echo("Moon   (rise/set are calendar-day events, almanac convention)")
    typer.echo(f"  {'moonrise':<24} {format_local(window.moonrise_utc, tz)}")
    typer.echo(f"  {'moonset':<24} {format_local(window.moonset_utc, tz)}")
    typer.echo(
        f"  {'illuminated':<24} {window.moon_illumination * 100:.1f}%"
        f"   ({_moon_phase_name(window.moon_illumination, window.moon_waxing)})"
    )
    if window.astronomical_night:
        state = "up at astronomical dusk" if window.moon_up_at_dusk else "down at astronomical dusk"
        typer.echo(f"  {'at dusk':<24} {state}")
    typer.echo("")

    typer.echo(f"Astronomical night   ({window.astronomical_night_hours:.2f} h)")
    for line in _render_intervals(window.astronomical_night, tz):
        typer.echo(line)
    typer.echo("")

    typer.echo(f"True dark window     ({window.dark_hours:.2f} h)   sun < -18 and moon down")
    for line in _render_intervals(window.dark_intervals, tz):
        typer.echo(line)
    typer.echo("")

    typer.echo("UTC reference")
    typer.echo(f"  sunset            {format_utc(window.sunset_utc)}Z")
    typer.echo(f"  astronomical dusk {format_utc(window.astronomical_dusk_utc)}Z")
    typer.echo(f"  moonrise          {format_utc(window.moonrise_utc)}Z")
    typer.echo("")


@app.command()
def night(
    date_str: str = typer.Option(
        None, "--date", "-d", metavar="YYYY-MM-DD",
        help="Night to plan. Defaults to tonight (before local noon means the night in progress).",
    ),
    location_key: str = typer.Option(
        None, "--location", "-l",
        help="Location key from config/locations.yaml. Defaults to the configured default.",
    ),
) -> None:
    """Print the night window: twilights, moon, and the true dark window."""
    try:
        loc = get_location(location_key)
    except LocationError as exc:
        typer.secho(exc.args[0], fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    night_date, defaulted = _parse_date(date_str, loc.tz)

    if not ephemeris_is_cached():
        typer.secho(
            "Fetching DE440s ephemeris (~32 MB) - one time only.",
            fg=typer.colors.YELLOW, err=True,
        )

    _print_night(night_window(night_date, loc), loc.tz, defaulted)


@app.command()
def targets(
    date_str: str = typer.Option(
        None, "--date", "-d", metavar="YYYY-MM-DD",
        help="Night to plan. Defaults to tonight.",
    ),
    location_key: str = typer.Option(
        None, "--location", "-l", help="Location key from config/locations.yaml.",
    ),
    scope_key: str = typer.Option(
        None, "--scope", "-s", help="Scope key from config/equipment.yaml.",
    ),
    min_altitude: float = typer.Option(
        DEFAULT_MIN_ALTITUDE_DEG, "--min-altitude",
        help="Altitude floor in degrees.",
    ),
    limit: int = typer.Option(
        DEFAULT_GROUP_LIMIT, "--limit", "-n", help="Objects per group.",
    ),
    group_filter: str = typer.Option(
        None, "--group", "-g",
        help="Show only one group, e.g. Galaxies. Case-insensitive.",
    ),
) -> None:
    """Rank tonight's deep-sky targets, grouped by object type."""
    try:
        loc = get_location(location_key)
    except LocationError as exc:
        typer.secho(exc.args[0], fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    night_date, defaulted = _parse_date(date_str, loc.tz)
    kit = load_equipment()
    scope = kit.scope(scope_key)
    window = night_window(night_date, loc)

    span = _observing_window(window)
    if span is None:
        typer.secho("No astronomical night at this location on this date.",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    from db import observations as _observations

    assessments = assess_targets(window, kit, scope,
                                 min_altitude_deg=min_altitude,
                                 logged=_observations.logged_object_ids())
    grouped = group_targets(assessments, limit_per_group=limit)

    if group_filter:
        wanted = group_filter.strip().lower()
        grouped = {k: v for k, v in grouped.items() if k.lower() == wanted}
        if not grouped:
            typer.secho(f"No group matching {group_filter!r}.",
                        fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2)

    tz = loc.tz
    tonight = "  (tonight)" if defaulted else ""
    using_dark = uses_true_dark(window)

    typer.echo("")
    typer.echo(f"Targets for {night_date.isoformat()}{tonight} - {loc.name}")
    typer.echo(f"{scope.name}   Bortle {loc.bortle}   floor {min_altitude:.0f} deg")
    typer.echo(
        f"Window: {format_local(span[0], tz)} -> {format_local(span[1], tz)}"
        f"  ({'true dark' if using_dark else 'astronomical night, moon up part of it'})"
        f"   moon {window.moon_illumination * 100:.0f}%"
    )
    typer.echo(f"{len(assessments)} objects pass the filters.")
    for text, is_warning in _horizon_notes(loc, min_altitude):
        typer.secho(text, fg=typer.colors.YELLOW if is_warning else None)

    for group, items in grouped.items():
        typer.echo("")
        typer.echo(f"{group}  ({len(items)})")
        typer.echo(f"  {'score':>5}  {'object':<34} {'peak':>5} {'when':>6} "
                   f"{'up':>5}  {'best window':<15} size")
        for a in items:
            size = f"{a.obj.size_arcmin:.0f}'" if a.obj.size_arcmin else "-"
            start, end = a.best_window
            typer.echo(
                f"  {a.score:5.1f}  {a.obj.display_name[:34]:<34}"
                f" {a.peak_altitude_deg:4.0f}d"
                f" {format_local(a.peak_time_utc, tz):>6}"
                f" {a.hours_above_floor:4.1f}h"
                f"  {format_local(start, tz)}-{format_local(end, tz):<9}"
                f" {size}"
            )
            if a.notes:
                typer.echo(f"         {'; '.join(a.notes)}")
    typer.echo("")


def _horizon_notes(loc, min_altitude: float) -> list[tuple[str, bool]]:
    """Lines describing the obstruction horizon. (text, is_warning)."""
    horizon = loc.horizon
    if horizon.is_flat:
        return []

    facing = "" if horizon.facing is None else f" facing {horizon.facing:.0f} deg"
    notes = [(f"Horizon profile: {horizon.name}{facing} "
              f"(max obstruction {horizon.max_obstruction_deg:.0f} deg)", False)]
    if horizon.is_generic:
        notes.append((
            "  This is a GENERIC preset, not a survey of your site. Measure "
            "your real horizon and replace it with an explicit az->alt map.",
            True,
        ))
    if horizon.max_obstruction_deg <= min_altitude:
        notes.append((
            f"  It sits entirely below the {min_altitude:.0f} deg altitude "
            f"floor, so it is not affecting these results.",
            False,
        ))
    return notes


def _factor_line(label: str, factors, width: int = 13) -> str:
    return (f"  {label:<{width}} clear {factors.clear:.2f}   "
            f"transp {factors.transparency:.2f}   moon {factors.moon:.2f}   "
            f"seeing {factors.seeing:.2f}   wind {factors.wind:.2f}   "
            f"dew {factors.dew:.2f}")


@app.command()
def tonight(
    date_str: str = typer.Option(
        None, "--date", "-d", metavar="YYYY-MM-DD",
        help="Night to plan. Defaults to tonight.",
    ),
    location_key: str = typer.Option(
        None, "--location", "-l", help="Location key from config/locations.yaml.",
    ),
    scope_key: str = typer.Option(
        None, "--scope", "-s", help="Scope key from config/equipment.yaml.",
    ),
    limit: int = typer.Option(4, "--limit", "-n", help="Targets per group."),
    no_targets: bool = typer.Option(
        False, "--no-targets", help="Skip the target list.",
    ),
) -> None:
    """The whole verdict: both scores, the grade, the breakdown, and targets."""
    try:
        loc = get_location(location_key)
    except LocationError as exc:
        typer.secho(exc.args[0], fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    night_date, defaulted = _parse_date(date_str, loc.tz)
    kit = load_equipment()
    scope = kit.scope(scope_key)
    window = night_window(night_date, loc)
    tz = loc.tz

    # Weather is requested for the whole astronomical night, which is the span
    # score_night() actually scores. The true dark window would be the wrong
    # ask: under a bright moon it can be a few minutes long.
    night_span = window.astronomical_night
    forecast = get_forecast(
        loc,
        start=night_span[0][0] if night_span else None,
        end=night_span[-1][1] if night_span else None,
    )
    score = score_night(window, forecast)

    tonight_label = "  (tonight)" if defaulted else ""
    typer.echo("")
    typer.echo(f"{night_date.isoformat()}{tonight_label} - {loc.name}   "
               f"Bortle {loc.bortle}   {scope.name}")
    for text, is_warning in _horizon_notes(loc, DEFAULT_MIN_ALTITUDE_DEG):
        typer.secho(text, fg=typer.colors.YELLOW if is_warning else None)
    typer.echo("")
    typer.echo(verdict(score, window))
    typer.echo("")

    if score.is_gradeable:
        typer.echo(f"  Deep-sky    {score.deep_sky_peak:5.1f}  "
                   f"grade {score.deep_sky_grade}"
                   f"   (night average {score.deep_sky_mean:.1f})")
        typer.echo(f"  Planetary   {score.planetary_peak:5.1f}  "
                   f"grade {score.planetary_grade}"
                   f"   (night average {score.planetary_mean:.1f})")
    else:
        # No weather means every weather factor defaults to 1.0. Printing a
        # grade here would claim a clear night we have no evidence for.
        typer.secho("  Deep-sky      n/a   no weather - cannot grade this night",
                    fg=typer.colors.YELLOW)
        typer.secho("  Planetary     n/a   no weather - cannot grade this night",
                    fg=typer.colors.YELLOW)
        typer.echo(f"  Moonlight   {score.deep_sky_peak / 100:5.2f}  "
                   f"darkness+moon factor only, not a condition score")
    typer.echo(f"  True dark   {score.dark_hours:5.2f} h"
               f"   astronomical night {window.astronomical_night_hours:.2f} h")
    typer.echo("")

    if score.best_window:
        start, end = score.best_window
        typer.echo(f"Best window   {format_local(start, tz)} -> "
                   f"{format_local(end, tz)}   "
                   f"(mean deep-sky {score.best_window_score:.0f})")
        typer.echo("")

    if not score.weather_available:
        typer.secho(f"Weather unavailable: {score.weather_note}",
                    fg=typer.colors.YELLOW)
        typer.echo("Scores below reflect darkness and moonlight only.")
        typer.echo("")
    else:
        typer.echo(f"Weather sources: {', '.join(forecast.sources)}")
        if score.seeing_estimated:
            typer.secho(
                "Seeing/transparency are ESTIMATED past the 7Timer 72 h horizon.",
                fg=typer.colors.YELLOW,
            )
        if score.dew_warning:
            typer.secho("Dew warning: dew point spread under 2 C.",
                        fg=typer.colors.YELLOW)
        typer.echo("")

    peak = max(score.slots, key=lambda s: s.deep_sky) if score.slots else None
    if peak:
        typer.echo(f"Factor breakdown at peak ({format_local(peak.time_utc, tz)})")
        typer.echo(_factor_line("deep-sky", peak.deep_sky_factors))
        typer.echo(_factor_line("planetary", peak.planetary_factors))
        name, value = peak.deep_sky_factors.weakest()
        if value < 0.99:
            typer.echo(f"  limiting factor: {name} ({value:.2f})")
        else:
            typer.echo("  limiting factor: none - every factor is at full marks")
        typer.echo("")

    if score.slots:
        typer.echo("Hourly")
        typer.echo(f"  {'time':<6} {'deep':>5} {'plan':>5} {'cloud':>6} "
                   f"{'gust':>6} {'moon':>6}")
        for slot_score in score.slots[::2]:            # hourly from 30-min slots
            conditions = (forecast.at(slot_score.time_utc)
                          if forecast.available else None)
            cloud = (f"{conditions.cloud_cover:.0f}%"
                     if conditions and conditions.cloud_cover is not None else "-")
            gust = (f"{conditions.wind_gust_kmh:.0f}"
                    if conditions and conditions.wind_gust_kmh is not None else "-")
            typer.echo(
                f"  {format_local(slot_score.time_utc, tz):<6}"
                f" {slot_score.deep_sky:5.0f} {slot_score.planetary:5.0f}"
                f" {cloud:>6} {gust:>6}"
                f" {slot_score.moon_altitude_deg:5.0f}d"
            )
        typer.echo("")

    if no_targets:
        return

    assessments = assess_targets(window, kit, scope)
    grouped = group_targets(assessments, limit_per_group=limit)
    typer.echo(f"Targets ({len(assessments)} pass the filters)")
    for group, items in grouped.items():
        typer.echo("")
        typer.echo(f"  {group}")
        for a in items:
            size = f"{a.obj.size_arcmin:.0f}'" if a.obj.size_arcmin else "-"
            start, end = a.best_window
            typer.echo(
                f"    {a.score:5.1f}  {a.obj.display_name[:32]:<32}"
                f" {a.peak_altitude_deg:3.0f}d"
                f"  {format_local(start, tz)}-{format_local(end, tz)}"
                f"  {size}"
            )
    typer.echo("")


@app.command()
def planets(
    date_str: str = typer.Option(None, "--date", "-d", metavar="YYYY-MM-DD"),
    location_key: str = typer.Option(None, "--location", "-l"),
    min_altitude: float = typer.Option(
        PLANET_MIN_ALTITUDE, "--min-altitude", help="Altitude floor in degrees.",
    ),
) -> None:
    """Planet apparitions: altitude, size, magnitude, and where in the cycle."""
    try:
        loc = get_location(location_key)
    except LocationError as exc:
        typer.secho(exc.args[0], fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    night_date, defaulted = _parse_date(date_str, loc.tz)
    window = night_window(night_date, loc)
    tz = loc.tz

    typer.echo("")
    typer.echo(f"Planets for {night_date.isoformat()}"
               f"{'  (tonight)' if defaulted else ''} - {loc.name}")
    typer.echo("")

    reports = report_all(loc, window, min_altitude_deg=min_altitude)
    observable = [r for r in reports if r.observable]
    hidden = [r for r in reports if not r.observable]

    if observable:
        typer.echo("Observable tonight")
        typer.echo(f"  {'planet':<9} {'mag':>6} {'size':>7} {'peak':>6} "
                   f"{'when':>6} {'up':>6}  apparition")
        for r in reports:
            if not r.observable:
                continue
            typer.echo(
                f"  {r.name:<9} {r.magnitude:+6.2f}"
                f" {r.apparent_diameter_arcsec:6.1f}\""
                f" {r.peak_altitude_deg:5.0f}d"
                f" {format_local(r.peak_time_utc, tz):>6}"
                f" {r.hours_above_floor:5.1f}h  {r.trend}"
            )
            detail = []
            if r.event_name:
                detail.append(f"{r.event_name} {r.event_date} "
                              f"({r.days_to_event:+d} d)")
            if r.ring_tilt_deg is not None:
                detail.append(f"ring tilt {r.ring_tilt_deg:+.1f} deg")
            if r.illuminated_fraction is not None and r.name in {"mercury", "venus"}:
                detail.append(f"{r.illuminated_fraction * 100:.0f}% lit")
            if detail:
                typer.echo(f"            {' | '.join(detail)}")

    if hidden:
        typer.echo("")
        typer.echo("Not observable tonight")
        for r in hidden:
            # PLAN.md 3.4: if it is not up tonight, say when it will be.
            if r.next_visible_date:
                when = f"next above {min_altitude:.0f} deg on {r.next_visible_date}"
            elif r.visibility and r.visibility.floor_unreachable:
                # Mercury never clears 25 deg from mid-northern latitudes.
                # "No return" would read as though it had vanished.
                when = (f"never clears {min_altitude:.0f} deg within a year; "
                        f"best is {r.visibility.best_altitude_deg:.0f} deg "
                        f"around {r.visibility.best_date}")
            else:
                when = "no return within a year"
            typer.echo(f"  {r.name:<9} peak {r.peak_altitude_deg:5.0f}d"
                       f"   {r.trend}")
            typer.echo(f"            {when}")
            if r.event_name:
                typer.echo(f"            {r.event_name} {r.event_date} "
                           f"({r.days_to_event:+d} d)")
    typer.echo("")


@app.command()
def events(
    days: int = typer.Option(90, "--days", "-n", help="How far ahead to look."),
    date_str: str = typer.Option(None, "--from", metavar="YYYY-MM-DD"),
    location_key: str = typer.Option(None, "--location", "-l"),
) -> None:
    """Upcoming meteor showers, eclipses and conjunctions."""
    try:
        loc = get_location(location_key)
    except LocationError as exc:
        typer.secho(exc.args[0], fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    start, _ = _parse_date(date_str, loc.tz)
    tz = loc.tz

    typer.echo("")
    typer.echo(f"Events {start.isoformat()} -> "
               f"{(start + timedelta(days=days)).isoformat()} - {loc.name}")

    # --- meteor showers ---
    typer.echo("")
    typer.echo("Meteor showers")
    peaks = showers_active_between(start, days)
    if not peaks:
        typer.echo("  none peaking in this window")
    for shower, peak in peaks:
        peak_night = night_window(peak, loc)
        forecast = assess_shower(shower, loc, peak_night)
        best = (format_local(forecast.best_time_utc, tz)
                if forecast.best_time_utc else "-")
        typer.echo(
            f"  {peak.isoformat()}  {shower.name:<26} ZHR {shower.zhr:>3}"
            f"   best {best}  radiant {forecast.best_radiant_altitude_deg:3.0f}d"
            f"   ~{forecast.best_rate:.0f}/h"
        )
        notes = list(forecast.notes)
        notes.append(f"{shower.velocity_km_s:.0f} km/s")
        if shower.parent:
            notes.append(f"parent {shower.parent}")
        typer.echo(f"      {'; '.join(notes)}")

    # --- eclipses ---
    typer.echo("")
    typer.echo("Lunar eclipses")
    eclipses = lunar_eclipses(start, days, loc)
    if not eclipses:
        typer.echo("  none in this window")
    for eclipse in eclipses:
        where = ("visible from here" if eclipse.visible
                 else "moon below horizon here")
        typer.echo(f"  {format_local(eclipse.time_utc, tz)} on "
                   f"{to_local(eclipse.time_utc, tz).date()}  "
                   f"{eclipse.kind:<11} {where}")

    # --- conjunctions ---
    typer.echo("")
    typer.echo(f"Conjunctions  (planets under {PLANET_CONJUNCTION_DEG:.0f} deg, "
               f"Moon under {MOON_CONJUNCTION_DEG:.0f} deg)")
    close = conjunctions(start, days)
    if not close:
        typer.echo("  none in this window")
    for event in close:
        typer.echo(
            f"  {to_local(event.time_utc, tz).date()}  "
            f"{event.body_a:<8} - {event.body_b:<8} "
            f"{event.separation_deg:5.2f} deg"
        )
    typer.echo("")


@app.command()
def locations() -> None:
    """List the configured observing sites."""
    from engine.locations import load_locations, default_location_key

    default = default_location_key()
    typer.echo("")
    for key, loc in sorted(load_locations().items()):
        mark = "*" if key == default else " "
        bortle = f"Bortle {loc.bortle}" if loc.bortle else "Bortle -"
        typer.echo(
            f" {mark} {key:<20} {loc.name:<26} {loc.lat:>8.4f} {loc.lon:>10.4f}"
            f"  {loc.elevation_m:>5.0f} m  {bortle:<9}"
            f"  horizon {loc.horizon.name:<7}{'*' if loc.horizon.is_generic else ' '}"
            f"  {loc.tz}"
        )
    typer.echo("\n * = default   |   horizon * = generic preset, not surveyed\n")


# The observation log lives in its own module and mounts as `planner log ...`.
from .log_commands import log_app  # noqa: E402

app.add_typer(log_app, name="log")


if __name__ == "__main__":
    app()
