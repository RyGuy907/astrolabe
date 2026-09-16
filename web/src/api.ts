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
  timezone: string;
  horizon_name: string;
  horizon_is_generic: boolean;
  horizon_max_deg: number;
  horizon_facing: number | null;
  /** True when the profile rises above the default altitude floor and can
   *  therefore change which targets are listed. A profile below the floor is
   *  inert — worth saying outright rather than letting the user assume. */
  horizon_binds: boolean;
  source: string;
}

/** A geocoder hit. Mirrors `GeocodeCandidate` in api/schemas.py. */
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
  peak_factors_deep_sky: FactorsModel | null;
  peak_factors_planetary: FactorsModel | null;
  limiting_factor: string | null;
  slots: SlotModel[];
}

export interface NightResponse {
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
  notes: string[];
}

export interface PlanetsResponse {
  date: string;
  location: LocationModel;
  min_altitude_deg: number;
  planets: PlanetModel[];
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

  createLocation: (body: NewLocationRequest) =>
    post<LocationModel>("/api/locations", body),

  /** 409 for YAML-defined sites, which are config-owned. */
  deleteLocation: (key: string) =>
    request<void>(`/api/locations/${encodeURIComponent(key)}`, {
      method: "DELETE",
    }),

  night: (date: string, location: string) =>
    get<NightResponse>(`/api/night${query({ date, location })}`),

  targets: (date: string, location: string, limit = 8, minAltitude = 25,
            groupBy: "type" | "constellation" = "constellation",
            sort: "score" | "brightness" = "brightness",
            includeAll = false) =>
    get<TargetsResponse>(
      `/api/targets${query({ date, location, limit, min_altitude: minAltitude,
                             group_by: groupBy, sort,
                             include_all: includeAll ? "true" : undefined })}`,
    ),

  planets: (date: string, location: string, findNext = false,
            minAltitude = 25) =>
    get<PlanetsResponse>(
      `/api/planets${query({ date, location, find_next: String(findNext),
                             min_altitude: minAltitude })}`,
    ),

  events: (from: string, location: string, days = 90) =>
    get<EventsResponse>(`/api/events${query({ from, location, days })}`),

  altitude: (date: string, location: string, bodies: string,
             objects?: string, constellations?: string, minAltitude = 25) =>
    get<AltitudeResponse>(
      `/api/altitude${query({ date, location, bodies, objects, constellations,
                              min_altitude: minAltitude })}`,
    ),

  // --- observation log ---
  logPrefill: (date: string, location: string, limit = 6) =>
    get<LogPrefillResponse>(`/api/log/prefill${query({ date, location, limit })}`),

  sessions: (limit = 10) => get<SessionModel[]>(`/api/sessions${query({ limit })}`),

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
