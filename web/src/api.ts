/**
 * Typed client for the planner API.
 *
 * Every datetime arriving from the API is ISO-8601 UTC. Nothing in this file
 * converts to local time — that happens in `format.ts`, which is the single
 * display boundary, mirroring the rule the Python engine enforces.
 */

export interface LocationModel {
  key: string;
  name: string;
  lat: number;
  lon: number;
  elevation_m: number;
  /** The observer's own Bortle class, or null. Never filled from the atlas. */
  bortle: number | null;
  /** Effective sky brightness; observer's value, else atlas, else null. */
  sqm: number | null;
  /** "observer" | "atlas" | "assumed" — where `sqm` came from. */
  sky_source: string;
  /** The class target filtering actually uses, however it was arrived at. */
  effective_bortle: number;
  /** The class with a decimal (4.3) when read off the map; else null. */
  bortle_decimal: number | null;
  timezone: string;
  horizon_name: string;
  horizon_is_generic: boolean;
  /** Azimuth -> altitude for a measured or custom horizon; null for a preset. */
  horizon_points: Record<string, number> | null;
  horizon_max_deg: number;
  horizon_facing: number | null;
  /** True when the profile rises above the default altitude floor and can
   *  therefore change which targets are listed. A profile below the floor is
   *  inert — worth saying outright rather than letting the user assume. */
  horizon_binds: boolean;
  source: string;
}

/** A geocoder hit. Mirrors `GeocodeCandidate` in api/schemas.py. */
/** Where the configured light-pollution atlas has data, if there is one. */
export interface SkyBrightnessCoverage {
  configured: boolean;
  /** [west, south, east, north] in degrees, or null. */
  bounds: number[] | null;
  /** What the raster says it is, e.g. "modelled from 2025 satellite data". */
  source: string | null;
  /** Overlay tiles of the map, or null when it came without any. */
  tiles: SkyGlowTiles | null;
}

/** Pre-rendered overlay tiles, served at /api/skybrightness/tiles/{z}/{x}/{y}.png. */
export interface SkyGlowTiles {
  min_zoom: number;
  max_zoom: number;
  /** [SQM, [r, g, b, a]] stops the tiles were coloured by, darkest first. */
  legend: [number, number[]][];
}

/** Ground height at a coordinate; null when the terrain service is unreachable. */
export interface ElevationReading {
  elevation_m: number | null;
}

/** The atlas's answer for one coordinate. */
export interface SkyBrightnessReading {
  sqm: number | null;
  bortle: number | null;
  /** The same with a decimal, e.g. 4.3. */
  bortle_decimal: number | null;
  /** False when the atlas simply has nothing here — a real answer. */
  in_coverage: boolean;
  /** What the raster says it is, as in SkyBrightnessCoverage. */
  source: string | null;
}

/** A constellation visible toward one bearing, for measuring a horizon. */
export interface SkyMark {
  abbreviation: string;
  name: string;
  altitude_deg: number;
  azimuth_deg: number;
}

export interface HorizonMarks {
  azimuth_deg: number;
  at: string;
  /** The site's IANA timezone, for showing `at` on its clock. */
  timezone: string;
  marks: SkyMark[];
}

/** The hours the observer plans to be outside. Mirrors ObservingWindowModel. */
export interface ObservingWindow {
  start: string;
  end: string;
  hours: number;
  /** How much of it is true dark -- astronomical night, moon down. */
  dark_hours: number;
  /** False once the observer has chosen the hours themselves. */
  is_default: boolean;
}

export interface GeocodeCandidate {
  label: string;
  name: string;
  lat: number;
  lon: number;
  elevation_m: number;
  suggested_key: string;
}

/**
 * Mirrors `NewLocationRequest` in api/schemas.py.
 *
 * The server accepts either `query` (which it geocodes itself) or explicit
 * `lat`/`lon`. This client always sends coordinates: the search UI resolves a
 * name to a candidate first and shows the user the numbers, so what gets saved
 * is what was on screen rather than whatever a second geocode call returns.
 *
 * `bortle: null` is a real value, not a missing one — see LocationManager.
 */
export interface NewLocationRequest {
  key?: string;
  name?: string;
  lat?: number;
  lon?: number;
  elevation_m?: number;
  bortle?: number | null;
  horizon?: string;
  /** Bearing of the obstruction, for the directional presets. */
  horizon_facing?: number | null;
  query?: string;
}

export interface IntervalModel {
  start: string;
  end: string;
  hours: number;
}

export interface NightWindowModel {
  date: string;
  location: LocationModel;
  sunset: string | null;
  sunrise: string | null;
  civil_dusk: string | null;
  nautical_dusk: string | null;
  astronomical_dusk: string | null;
  astronomical_dawn: string | null;
  nautical_dawn: string | null;
  civil_dawn: string | null;
  moonrise: string | null;
  moonset: string | null;
  moon_illumination: number;
  moon_waxing: boolean;
  moon_up_at_dusk: boolean;
  astronomical_night: IntervalModel[];
  dark_intervals: IntervalModel[];
  astronomical_night_hours: number;
  dark_hours: number;
}

export interface FactorsModel {
  clear: number;
  transparency: number;
  moon: number;
  seeing: number;
  wind: number;
  dew: number;
}

export interface SlotModel {
  time: string;
  deep_sky: number;
  planetary: number;
  moon_altitude_deg: number;
  cloud_cover: number | null;
  wind_gust_kmh: number | null;
  temperature_c: number | null;
  humidity_pct: number | null;
  dew_point_spread_c: number | null;
}

export interface ScoreModel {
  deep_sky_peak: number;
  deep_sky_mean: number;
  planetary_peak: number;
  planetary_mean: number;
  deep_sky_grade: string;
  planetary_grade: string;
  is_gradeable: boolean;
  best_window: IntervalModel | null;
  best_window_score: number;
  dark_hours: number;
  weather_available: boolean;
  weather_note: string | null;
  weather_sources: string[];
  seeing_estimated: boolean;
  dew_warning: boolean;
  verdict: string;
  /** The six factors for the best single slot -- the best half hour, not the night. */
  peak_factors_deep_sky: FactorsModel | null;
  /** Each factor averaged across the night. What conditions were actually like. */
  mean_factors_deep_sky: FactorsModel | null;
  mean_factors_planetary: FactorsModel | null;
  peak_factors_planetary: FactorsModel | null;
  limiting_factor: string | null;
  slots: SlotModel[];
}

export interface NightResponse {
  session: ObservingWindow | null;
  window: NightWindowModel;
  score: ScoreModel;
}

export interface TargetModel {
  name: string;
  display_name: string;
  group: string;
  object_type: string;
  messier: number | null;
  constellation: string | null;
  ra_deg: number;
  dec_deg: number;
  magnitude: number | null;
  size_arcmin: number | null;
  surface_brightness: number | null;
  score: number | null;
  peak_altitude_deg: number | null;
  peak_time: string | null;
  hours_above_floor: number | null;
  best_window: IntervalModel | null;
  moon_separation_deg: number | null;
  contrast_margin: number | null;
  visible_tonight: boolean;
  visible_late: boolean;
  too_faint: boolean;
  /** On engine/showpieces.py's curated list of well-known objects. */
  showpiece: boolean;
  /** Reference data from engine/reference.py. Curated, so often null. */
  distance_ly: number | null;
  diameter_ly: number | null;
  /** Every common name the catalogue carries, so search can match one that
   *  is not the one on display. */
  aliases: string[];
  /** Double stars only. */
  separation_arcsec: number | null;
  component_mags: string | null;
  discovered_by: string | null;
  /** Negative for BCE. */
  discovered_year: number | null;
  about: string | null;
  notes: string[];
}

export interface GroupInfo {
  key: string;
  label: string;
  is_constellation: boolean;
  ra_deg: number | null;
  dec_deg: number | null;
  spread_deg: number | null;
  is_wide: boolean;
  visibility: string;      // "tonight" | "late" | "none"
  window_start: string | null;
  window_end: string | null;
  hours_up: number;
  peak_altitude_deg: number | null;
  has_gap: boolean;
}

export interface TargetsResponse {
  session: ObservingWindow | null;
  date: string;
  location: LocationModel;
  scope: string;
  min_altitude_deg: number;
  total_passing: number;
  total_too_faint: number;
  window: IntervalModel | null;
  using_true_dark: boolean;
  horizon_warning: string | null;
  group_by: string;
  sort: string;
  include_all: boolean;
  group_info: Record<string, GroupInfo>;
  groups: Record<string, TargetModel[]>;
}

/** Constants about a planet, from engine/reference.py. */
export interface PlanetFactsModel {
  equatorial_diameter_km: number;
  /** Sidereal rotation. Negative for retrograde: Venus and Uranus. */
  rotation_hours: number;
  year_earth_years: number;
  moons: number;
  /** Null for the five naked-eye planets, which have no discoverer to name. */
  discovered_by: string | null;
  discovered_year: number | null;
  about: string;
}

export interface PlanetModel {
  name: string;
  observable: boolean;
  peak_altitude_deg: number;
  peak_time: string | null;
  hours_above_floor: number;
  magnitude: number | null;
  apparent_diameter_arcsec: number | null;
  illuminated_fraction: number | null;
  distance_au: number | null;
  elongation_deg: number | null;
  trend: string;
  event_name: string | null;
  event_date: string | null;
  days_to_event: number | null;
  ring_tilt_deg: number | null;
  next_visible_date: string | null;
  best_altitude_deg: number | null;
  facts: PlanetFactsModel | null;
  notes: string[];
}

/** The static sky, for the interactive chart. Stars are brightest first, as
 *  parallel arrays, so "everything to magnitude m" is a prefix. */
export interface SkyCatalog {
  ra: number[];
  dec: number[];
  mag: number[];
  /** Star index -> name, for the stars that have one. */
  labels: Record<string, string>;
  constellations: Record<string, {
    name: string;
    rank: number;
    label: [number, number] | null;
    lines: [number, number][][];
  }>;
  objects: {
    id: string; label: string; name: string; ra: number; dec: number;
    group: string; constellation: string | null;
  }[];
}

/** One moment at one site: the J2000-to-horizon rotation, and the bodies. */
/** What a star on the sky chart is. Size, output and age are estimates;
 *  `distance_quality` and `age_basis` say how far to trust them. */
export interface StarProfile {
  index: number;
  hip: number | null;
  name: string | null;
  magnitude: number;
  spectral_type: string | null;
  kind: string;
  colour: string | null;
  temperature_k: number | null;
  distance_ly: number | null;
  distance_quality: "precise" | "approximate" | "rough" | null;
  luminosity_sun: number | null;
  radius_sun: number | null;
  age: string | null;
  age_basis: "published" | "upper limit" | null;
  /** Where each figure came from. */
  distance_source: "gaia" | "hipparcos" | null;
  radius_source: "measured" | "gaia" | "estimated" | null;
  temperature_source: "spectrum" | "type" | "colour" | null;
  luminosity_source: "measured" | "gaia" | "estimated" | null;
  /** Why the catalogue's spectral type was set aside, when it was. */
  type_note: string | null;
}

export interface SkyFrame {
  at: string;
  /** Maps a J2000 unit vector to (east, north, up). */
  matrix: [number, number, number][];
  bodies: { name: string; ra: number; dec: number }[];
}

export interface MoonFactsModel {
  diameter_km: number;
  sidereal_month_days: number;
  synodic_month_days: number;
  mean_distance_km: number;
  visible_surface_fraction: number;
  about: string;
}

export interface MoonModel {
  observable: boolean;
  peak_altitude_deg: number;
  peak_time: string | null;
  hours_above_floor: number;
  /** Null within about a day of New Moon, where the formula stops holding. */
  magnitude: number | null;
  apparent_diameter_arcsec: number;
  illuminated_fraction: number;
  waxing: boolean;
  distance_km: number;
  elongation_deg: number;
  phase_angle_deg: number;
  age_days: number;
  next_phase_name: string;
  next_phase_time: string;
  moonrise: string | null;
  moonset: string | null;
  facts: MoonFactsModel;
  notes: string[];
}

export interface PlanetsResponse {
  date: string;
  location: LocationModel;
  min_altitude_deg: number;
  planets: PlanetModel[];
  moon: MoonModel | null;
}

export interface ShowerModel {
  name: string;
  code: string;
  peak_date: string;
  zhr: number;
  velocity_km_s: number;
  parent: string | null;
  best_time: string | null;
  best_radiant_altitude_deg: number;
  estimated_rate_per_hour: number;
  moon_illumination: number;
  notes: string[];
}

export interface EclipseModel {
  time: string;
  kind: string;
  moon_altitude_deg: number;
  visible_from_location: boolean;
}

export interface ConjunctionModel {
  time: string;
  body_a: string;
  body_b: string;
  separation_deg: number;
  involves_moon: boolean;
}

export interface EventsResponse {
  from_date: string;
  days: number;
  location: LocationModel;
  showers: ShowerModel[];
  lunar_eclipses: EclipseModel[];
  solar_eclipses_supported: boolean;
  solar_eclipse_note: string;
  conjunctions: ConjunctionModel[];
}

export interface AltitudePointModel {
  time: string;
  altitude_deg: number;
  azimuth_deg: number;
}

export interface AltitudeSeriesModel {
  label: string;
  kind: string;
  points: AltitudePointModel[];
}

export interface AltitudeResponse {
  date: string;
  location: LocationModel;
  start: string;
  end: string;
  astronomical_night: IntervalModel[];
  dark_intervals: IntervalModel[];
  min_altitude_deg: number;
  horizon_at_azimuth: Record<string, number>;
  series: AltitudeSeriesModel[];
}

export interface ObservationModel {
  id: number | null;
  session_id: number | null;
  object_id: string | null;
  object_name: string;
  observed_at_utc: string | null;
  eyepiece: string | null;
  notes: string | null;
  rating: number | null;
  sketch_path: string | null;
}

export interface SessionModel {
  id: number | null;
  date: string;
  location_key: string;
  start_utc: string | null;
  end_utc: string | null;
  scope_key: string | null;
  conditions: Record<string, any> | null;
  seeing_actual: number | null;
  transparency_actual: number | null;
  notes: string | null;
  observations: ObservationModel[];
}

export interface LogCandidateModel {
  object_id: string;
  object_name: string;
  group: string;
  score: number;
  peak_altitude_deg: number;
  already_logged: boolean;
}

export interface LogPrefillResponse {
  date: string;
  location_key: string;
  scope_key: string | null;
  candidates: LogCandidateModel[];
  conditions: Record<string, any> | null;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* keep the status line */
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

function post<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, signal ? { signal } : undefined);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* keep the status line */
    }
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<T>;
}

function query(params: Record<string, string | number | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, String(value));
    }
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : "";
}

export const api = {
  locations: () => get<LocationModel[]>("/api/locations"),

  /** Place name -> candidates. Returns [] both when there is no match and
   *  when the geocoder is unreachable; the API cannot tell those apart. */
  geocode: (q: string, signal?: AbortSignal) =>
    get<GeocodeCandidate[]>(`/api/geocode${query({ q })}`, signal),

  /** Used by the map picker to open where the atlas actually has data. */
  skyBrightness: () => get<SkyBrightnessCoverage>("/api/skybrightness"),

  /** Constellations toward a bearing, lowest first, for measuring a horizon. */
  horizonMarks: (lat: number, lon: number, azimuth: number,
                 signal?: AbortSignal) =>
    get<HorizonMarks>(
      `/api/horizon/marks${query({ lat, lon, azimuth })}`, signal,
    ),

  /** What the atlas says at one point, so the form can fill Bortle in. */
  /** Ground height at a point, for a site picked off the map. */
  elevation: (lat: number, lon: number, signal?: AbortSignal) =>
    get<ElevationReading>(`/api/elevation${query({ lat, lon })}`, signal),

  skyBrightnessAt: (lat: number, lon: number, signal?: AbortSignal) =>
    get<SkyBrightnessReading>(
      `/api/skybrightness/at${query({ lat, lon })}`, signal,
    ),

  createLocation: (body: NewLocationRequest) =>
    post<LocationModel>("/api/locations", body),

  /** 409 for YAML-defined sites, which are config-owned. */
  deleteLocation: (key: string) =>
    request<void>(`/api/locations/${encodeURIComponent(key)}`, {
      method: "DELETE",
    }),

  /** `session` is a [startIso, endIso] pair; omit it for the server default
   *  of astronomical dusk to 01:00 local. */
  night: (date: string, location: string, session?: [string, string]) =>
    get<NightResponse>(`/api/night${query({
      date, location,
      session_start: session?.[0], session_end: session?.[1],
    })}`),

  /** `minAltitude` defaults to 0 here, not to the engine's 25: the site's own
   *  obstruction horizon is the floor, so a universal one would only ever
   *  override what the observer measured. */
  targets: (date: string, location: string, limit = 8, minAltitude = 0,
            groupBy: "type" | "constellation" = "constellation",
            sort: "score" | "brightness" = "brightness",
            includeAll = false, session?: [string, string]) =>
    get<TargetsResponse>(
      `/api/targets${query({ date, location, limit, min_altitude: minAltitude,
                             group_by: groupBy, sort,
                             include_all: includeAll ? "true" : undefined,
                             session_start: session?.[0],
                             session_end: session?.[1] })}`,
    ),

  planets: (date: string, location: string, findNext = false,
            minAltitude = 0) =>
    get<PlanetsResponse>(
      `/api/planets${query({ date, location, find_next: String(findNext),
                             min_altitude: minAltitude })}`,
    ),

  events: (from: string, location: string, days = 90) =>
    get<EventsResponse>(`/api/events${query({ from, location, days })}`),

  altitude: (date: string, location: string, bodies: string,
             objects?: string, constellations?: string, minAltitude = 0) =>
    get<AltitudeResponse>(
      `/api/altitude${query({ date, location, bodies, objects, constellations,
                              min_altitude: minAltitude })}`,
    ),


  skyCatalog: () => get<SkyCatalog>("/api/sky/catalog"),

  skyFrame: (location: string, at: string, signal?: AbortSignal) =>
    get<SkyFrame>(`/api/sky/frame${query({ location, at })}`, signal),

  star: (index: number, signal?: AbortSignal) =>
    get<StarProfile>(`/api/sky/star/${index}`, signal),

  // --- observation log ---
  logPrefill: (date: string, location: string, limit = 6) =>
    get<LogPrefillResponse>(`/api/log/prefill${query({ date, location, limit })}`),

  sessions: (limit = 10, filter: { date?: string; location?: string } = {},
             signal?: AbortSignal) =>
    get<SessionModel[]>(`/api/sessions${query({ limit, ...filter })}`, signal),

  session: (id: number) => get<SessionModel>(`/api/sessions/${id}`),

  createSession: (body: {
    date: string;
    location: string;
    notes?: string;
  }) => post<SessionModel>("/api/sessions", body),

  addObservation: (
    sessionId: number,
    body: {
      object_id?: string;
      object_name: string;
      eyepiece?: string;
      notes?: string;
      rating?: number;
      observed_at_utc?: string;
    },
  ) => post<ObservationModel>(`/api/sessions/${sessionId}/observations`, body),

  deleteObservation: (id: number) =>
    request<void>(`/api/observations/${id}`, { method: "DELETE" }),
};
