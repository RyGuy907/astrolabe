"""Pydantic response models for the HTTP layer.

**Every datetime crosses this boundary as ISO-8601 UTC**, matching the engine's
internal rule. Each response also carries the site's IANA `timezone` so the
frontend — the display layer — can convert once, at render time. The API never
sends local times.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class LocationModel(BaseModel):
    key: str
    name: str
    lat: float
    lon: float
    elevation_m: float
    bortle: int | None = Field(
        default=None,
        description="The observer's own Bortle class, or null. Never filled "
                    "in from the atlas -- a value here is a measurement they "
                    "made, and it outranks any lookup.",
    )
    sqm: float | None = Field(
        default=None,
        description="Effective sky brightness, mag/arcsec^2. From the "
                    "observer's Bortle if they gave one, otherwise from the "
                    "atlas, otherwise null.",
    )
    sky_source: str = Field(
        default="assumed",
        description='Where sqm came from: "observer", "atlas" or "assumed". '
                    '"assumed" means nothing is known and target filtering '
                    "falls back to Bortle 5, which changes which objects "
                    "appear at all.",
    )
    effective_bortle: int = Field(
        default=5,
        description="The class filtering actually uses, however it was "
                    "arrived at. Display only; SQM is the internal unit.",
    )
    timezone: str
    horizon_name: str
    horizon_is_generic: bool = Field(
        description="True for a built-in preset, which is a generic assumption "
                    "about terrain rather than a survey of the site.",
    )
    horizon_max_deg: float
    horizon_facing: int | None = Field(
        default=None,
        description="Bearing the preset's obstruction was rotated onto, for "
                    "the directional presets. None if it was never rotated.",
    )
    horizon_binds: bool = Field(
        default=False,
        description="True when the profile rises above the default altitude "
                    "floor and can therefore change which targets are listed. "
                    "A profile below the floor is inert, which is worth "
                    "saying rather than leaving the user to infer.",
    )
    source: str = Field(default="config",
                        description="config (YAML) or stored (added at runtime)")


class IntervalModel(BaseModel):
    start: datetime
    end: datetime
    hours: float


class NightWindowModel(BaseModel):
    date: date
    location: LocationModel

    sunset: datetime | None
    sunrise: datetime | None
    civil_dusk: datetime | None
    nautical_dusk: datetime | None
    astronomical_dusk: datetime | None
    astronomical_dawn: datetime | None
    nautical_dawn: datetime | None
    civil_dawn: datetime | None

    moonrise: datetime | None
    moonset: datetime | None
    moon_illumination: float
    moon_waxing: bool
    moon_up_at_dusk: bool

    astronomical_night: list[IntervalModel]
    dark_intervals: list[IntervalModel]
    astronomical_night_hours: float
    dark_hours: float


class FactorsModel(BaseModel):
    clear: float
    transparency: float
    moon: float
    seeing: float
    wind: float
    dew: float


class SlotModel(BaseModel):
    time: datetime
    deep_sky: float
    planetary: float
    moon_altitude_deg: float
    cloud_cover: float | None = None
    wind_gust_kmh: float | None = None
    temperature_c: float | None = None
    humidity_pct: float | None = None
    dew_point_spread_c: float | None = None


class ScoreModel(BaseModel):
    deep_sky_peak: float
    deep_sky_mean: float
    planetary_peak: float
    planetary_mean: float
    deep_sky_grade: str
    planetary_grade: str
    is_gradeable: bool = Field(
        description="False when weather is unavailable. The scores then "
                    "describe darkness and moonlight only and must not be "
                    "presented as a verdict on conditions.",
    )
    best_window: IntervalModel | None
    best_window_score: float
    dark_hours: float
    weather_available: bool
    weather_note: str | None
    weather_sources: list[str]
    seeing_estimated: bool
    dew_warning: bool
    verdict: str
    peak_factors_deep_sky: FactorsModel | None = Field(
        default=None,
        description="The six factors for the single best slot of the night. "
                    "Says what the best half hour was like, not the night.",
    )
    peak_factors_planetary: FactorsModel | None = None
    mean_factors_deep_sky: FactorsModel | None = Field(
        default=None,
        description="Each factor averaged across the night. The honest answer "
                    "to 'what were conditions like?' -- the peak breakdown "
                    "reports the least cloudy slot by construction.",
    )
    mean_factors_planetary: FactorsModel | None = None
    limiting_factor: str | None = Field(
        default=None,
        description="The weakest factor averaged over the night.",
    )
    slots: list[SlotModel]


class ObservingWindowModel(BaseModel):
    """The hours the observer plans to be outside.

    Echoed on every response that was computed over it, so the UI can show
    what it is and offer to change it without guessing the server's default.
    """

    start: datetime
    end: datetime
    hours: float
    dark_hours: float = Field(
        default=0.0,
        description="How much of the session is true dark -- astronomical "
                    "night with the Moon down. Reported, not enforced.",
    )
    is_default: bool = Field(
        default=True,
        description="False when the caller chose these hours explicitly.",
    )


class NightResponse(BaseModel):
    window: NightWindowModel
    score: ScoreModel
    session: ObservingWindowModel | None = None


class TargetModel(BaseModel):
    name: str
    display_name: str
    group: str
    object_type: str
    messier: int | None
    constellation: str | None

    ra_deg: float
    dec_deg: float
    magnitude: float | None
    size_arcmin: float | None
    surface_brightness: float | None

    # Null for objects that are not observable tonight. The unfiltered view
    # lists them anyway, badged, rather than pretending they do not exist.
    score: float | None = None
    peak_altitude_deg: float | None = None
    peak_time: datetime | None = None
    hours_above_floor: float | None = None
    best_window: IntervalModel | None = None
    moon_separation_deg: float | None = None
    contrast_margin: float | None = None
    visible_tonight: bool = False
    visible_late: bool = Field(
        default=False,
        description="Only clears the altitude floor after the observing "
                    "session ends -- local midnight when no session was given.",
    )
    too_faint: bool = Field(
        default=False,
        description="Up and pointable, but below the detection threshold for "
                    "this sky - light pollution, moonlight, or both. Listed "
                    "rather than filtered, and sorted last.",
    )
    distance_ly: float | None = Field(
        default=None,
        description="Distance in light years, from engine/reference.py. Not "
                    "derived from the catalogue's parallax or redshift "
                    "columns -- see that module for why both are traps.",
    )
    diameter_ly: float | None = Field(
        default=None,
        description="How big the object actually is, in light years. A quoted "
                    "value from engine/reference.py, not derived from the "
                    "catalogue's angular size -- that is an isophotal extent "
                    "and came out around 30% low.",
    )
    aliases: list[str] = Field(
        default_factory=list,
        description="Every common name the catalogue carries, so search can "
                    "match one that is not the one on display. M17 is shown "
                    "as the Checkmark Nebula and is also the Swan, the "
                    "Lobster and the Omega.",
    )
    separation_arcsec: float | None = Field(
        default=None, description="Double stars: how far apart the pair sits.",
    )
    component_mags: str | None = None
    discovered_by: str | None = None
    discovered_year: int | None = Field(
        default=None, description="Negative for BCE.",
    )
    about: str | None = Field(
        default=None,
        description="One line on what the object is, where the type label "
                    "alone undersells it.",
    )
    showpiece: bool = Field(
        default=False,
        description="On the curated list of well-known objects worth "
                    "pointing a telescope at - see engine/showpieces.py for "
                    "how that list is drawn and what it deliberately omits.",
    )
    notes: list[str] = Field(default_factory=list)


class GroupInfo(BaseModel):
    """A bucket header. For constellations this carries the plottable centroid."""

    key: str
    label: str
    is_constellation: bool = False
    ra_deg: float | None = None
    dec_deg: float | None = None
    spread_deg: float | None = None
    is_wide: bool = Field(
        default=False,
        description="A constellation whose extent makes one centroid a rough "
                    "summary — Hydra spans over 100 degrees of RA.",
    )
    visibility: str = Field(
        default="none",
        description="tonight | late | none — when the constellation itself "
                    "clears the altitude floor, judged on its centroid.",
    )
    window_start: datetime | None = None
    window_end: datetime | None = None
    hours_up: float = 0.0
    peak_altitude_deg: float | None = None
    has_gap: bool = Field(
        default=False,
        description="Dips below the floor and returns, so the window is not "
                    "continuous end to end.",
    )


class TargetsResponse(BaseModel):
    session: ObservingWindowModel | None = None
    date: date
    location: LocationModel
    scope: str
    min_altitude_deg: float
    total_passing: int
    total_too_faint: int = 0
    window: IntervalModel | None
    using_true_dark: bool
    horizon_warning: str | None
    group_by: str
    sort: str
    include_all: bool
    group_info: dict[str, GroupInfo]
    groups: dict[str, list[TargetModel]]


class PlanetFactsModel(BaseModel):
    """Constants about a planet, from engine/reference.py."""

    equatorial_diameter_km: float
    rotation_hours: float = Field(
        description="Sidereal rotation. Negative for retrograde rotation, "
                    "which is Venus and Uranus.",
    )
    year_earth_years: float
    moons: int
    discovered_by: str | None = Field(
        default=None,
        description="Null for the five naked-eye planets, which have no "
                    "discoverer to name.",
    )
    discovered_year: int | None = None
    about: str


class PlanetModel(BaseModel):
    name: str
    observable: bool
    peak_altitude_deg: float
    peak_time: datetime | None
    hours_above_floor: float
    magnitude: float | None
    apparent_diameter_arcsec: float | None
    illuminated_fraction: float | None
    distance_au: float | None
    elongation_deg: float | None
    trend: str
    event_name: str | None
    event_date: date | None
    days_to_event: int | None
    ring_tilt_deg: float | None
    next_visible_date: date | None
    best_altitude_deg: float | None = Field(
        default=None,
        description="Set when the altitude floor is unreachable within a year, "
                    "so the UI can say what the planet does reach.",
    )
    facts: PlanetFactsModel | None = None
    notes: list[str]


class MoonFactsModel(BaseModel):
    """Constants about the Moon, from engine/reference.py."""

    diameter_km: float
    sidereal_month_days: float
    synodic_month_days: float
    mean_distance_km: float
    visible_surface_fraction: float
    about: str


class MoonModel(BaseModel):
    """The Moon on this night, with the same row fields a planet has.

    Where a planet reports its apparition, the Moon reports its phase: the
    next principal phase stands in for the next opposition.
    """

    observable: bool
    peak_altitude_deg: float
    peak_time: datetime | None
    hours_above_floor: float
    magnitude: float | None = Field(
        description="Null within about a day of New Moon, where the "
                    "brightness formula no longer holds.",
    )
    apparent_diameter_arcsec: float
    illuminated_fraction: float
    waxing: bool
    distance_km: float
    elongation_deg: float
    phase_angle_deg: float
    age_days: float
    next_phase_name: str
    next_phase_time: datetime
    moonrise: datetime | None
    moonset: datetime | None
    facts: MoonFactsModel
    notes: list[str]


class PlanetsResponse(BaseModel):
    date: date
    location: LocationModel
    min_altitude_deg: float
    planets: list[PlanetModel]
    moon: MoonModel | None = None


class ShowerModel(BaseModel):
    name: str
    code: str
    peak_date: date
    zhr: float
    velocity_km_s: float
    parent: str | None
    best_time: datetime | None
    best_radiant_altitude_deg: float
    estimated_rate_per_hour: float
    moon_illumination: float
    notes: list[str]


class EclipseModel(BaseModel):
    time: datetime
    kind: str
    moon_altitude_deg: float
    visible_from_location: bool


class ConjunctionModel(BaseModel):
    time: datetime
    body_a: str
    body_b: str
    separation_deg: float
    involves_moon: bool


class EventsResponse(BaseModel):
    from_date: date
    days: int
    location: LocationModel
    showers: list[ShowerModel]
    lunar_eclipses: list[EclipseModel]
    conjunctions: list[ConjunctionModel]


class AltitudePointModel(BaseModel):
    time: datetime
    altitude_deg: float
    azimuth_deg: float


class AltitudeSeriesModel(BaseModel):
    label: str
    kind: str                       # planet | moon | sun | deep-sky
    points: list[AltitudePointModel]


class AltitudeResponse(BaseModel):
    """Payload for the altitude-vs-time chart, PLAN.md §4's key visual."""

    date: date
    location: LocationModel
    start: datetime
    end: datetime
    astronomical_night: list[IntervalModel]
    dark_intervals: list[IntervalModel]
    min_altitude_deg: float
    horizon_at_azimuth: dict[str, float]
    series: list[AltitudeSeriesModel]


class NewLocationRequest(BaseModel):
    query: str | None = Field(
        default=None, description="Place name to geocode, e.g. 'Lone Pine, CA'.",
    )
    key: str | None = None
    name: str | None = None
    lat: float | None = None
    lon: float | None = None
    elevation_m: float | None = None
    bortle: int | None = None
    horizon: str | None = Field(
        default=None,
        description="Preset name: flat, hilly, trees, ridge or valley.",
    )
    horizon_facing: int | None = Field(
        default=None, ge=0, lt=360,
        description="Bearing in degrees of the obstruction, for the "
                    "directional presets (ridge, valley). Ignored by the "
                    "symmetric ones.",
    )


class SkyBrightnessCoverage(BaseModel):
    """Whether a light-pollution atlas is configured, and where it reaches."""

    configured: bool = Field(
        description="True when engine/skybrightness.py has a raster to read.",
    )
    bounds: list[float] | None = Field(
        default=None,
        description="[west, south, east, north] in degrees, or null. The map "
                    "picker opens here so that clicks land inside coverage.",
    )


class SkyBrightnessReading(BaseModel):
    """The atlas's answer for one coordinate."""

    sqm: float | None = Field(
        default=None,
        description="Total sky brightness, mag/arcsec^2, or null when the "
                    "point falls outside the raster or on a nodata cell.",
    )
    bortle: int | None = Field(
        default=None,
        description="`sqm` expressed as a Bortle class, or null. Display "
                    "only; SQM is the internal unit.",
    )
    in_coverage: bool = Field(
        description="False when the atlas simply has nothing here, which is "
                    "a real answer and not an error.",
    )


class SkyMarkModel(BaseModel):
    """A constellation visible toward one bearing, for measuring a horizon."""

    abbreviation: str
    name: str
    altitude_deg: float
    azimuth_deg: float


class HorizonMarksResponse(BaseModel):
    """What can be seen toward one bearing, lowest first.

    The observer names the lowest constellation they can actually make out;
    its altitude is how high the obstruction reaches there. That produces a
    *measured* horizon rather than a preset chosen from a menu of generic
    assumptions.
    """

    azimuth_deg: float
    at: datetime = Field(description="Instant these altitudes were computed "
                                     "for, UTC. Which constellations sit low "
                                     "in a direction depends on the time.")
    marks: list[SkyMarkModel]


class GeocodeCandidate(BaseModel):
    label: str
    name: str
    lat: float
    lon: float
    elevation_m: float
    suggested_key: str


# --- observation log (PLAN.md §5) -------------------------------------------

class ObservationModel(BaseModel):
    id: int | None = None
    session_id: int | None = None
    object_id: str | None = None
    object_name: str
    observed_at_utc: datetime | None = None
    eyepiece: str | None = None
    notes: str | None = None
    rating: int | None = Field(default=None, ge=1, le=5)
    sketch_path: str | None = None


class SessionModel(BaseModel):
    id: int | None = None
    date: date
    location_key: str
    start_utc: datetime | None = None
    end_utc: datetime | None = None
    scope_key: str | None = None
    conditions: dict | None = Field(
        default=None,
        description="Conditions frozen at session time. A forecast expires; "
                    "this keeps the entry meaningful afterwards.",
    )
    seeing_actual: int | None = Field(default=None, ge=1, le=5)
    transparency_actual: int | None = Field(default=None, ge=1, le=5)
    notes: str | None = None
    observations: list[ObservationModel] = Field(default_factory=list)


class NewSessionRequest(BaseModel):
    date: str | None = None
    location: str | None = None
    scope: str | None = None
    notes: str | None = None
    seeing_actual: int | None = Field(default=None, ge=1, le=5)
    transparency_actual: int | None = Field(default=None, ge=1, le=5)
    snapshot_conditions: bool = Field(
        default=True,
        description="Compute and freeze the night's conditions on the session.",
    )


class NewObservationRequest(BaseModel):
    object_name: str
    object_id: str | None = None
    observed_at_utc: datetime | None = None
    eyepiece: str | None = None
    notes: str | None = None
    rating: int | None = Field(default=None, ge=1, le=5)
    sketch_path: str | None = None


class LogCandidateModel(BaseModel):
    """A target from the night's computed list, offered for one-click logging.

    PLAN.md §5 calls this the key UX move: pre-populate the log from what was
    actually recommended, rather than making the observer retype it.
    """

    object_id: str
    object_name: str
    group: str
    score: float
    peak_altitude_deg: float
    already_logged: bool


class LogPrefillResponse(BaseModel):
    date: date
    location_key: str
    scope_key: str | None
    candidates: list[LogCandidateModel]
    conditions: dict | None


class ObjectHistoryModel(BaseModel):
    object_id: str
    times_observed: int
    entries: list[dict]


class LogStatsModel(BaseModel):
    sessions: int
    observations: int
    distinct_objects: int
    first_session: str | None
    last_session: str | None
