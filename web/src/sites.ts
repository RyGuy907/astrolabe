/**
 * Observing sites, kept in the browser.
 *
 * The server stores nothing: every request carries the site it is about (the
 * `site` parameter), and the list of sites lives here. What is kept is only
 * what the observer chose -- where, how high, how dark, what is in the way.
 * Everything that follows from those (the timezone, the atlas's reading, the
 * horizon's peak) the server works out again whenever it is asked, so a
 * saved site picks up a better atlas the moment there is one.
 */

import type { LocationModel } from "./api";
import { hasKey, readJson, writeJson } from "./storage";

export interface SiteSpec {
  key: string;
  name: string;
  lat: number;
  lon: number;
  elevation_m: number;
  /** The observer's own Bortle class; null for "I don't know", which the
   *  atlas fills in if it can. */
  bortle: number | null;
  /** The horizon as one string: a preset (`ridge@250`), a uniform angle
   *  (`10`), or a measured azimuth->altitude map as JSON. */
  horizon: string | null;
}

const KEY = "astro:sites";

interface Saved {
  version: 1;
  sites: SiteSpec[];
  /** The site open last time, so a reload comes back to it. */
  selected: string | null;
}

function isSpec(value: unknown): value is SiteSpec {
  const site = value as SiteSpec;
  return !!site && typeof site.key === "string" && typeof site.name === "string" &&
    Number.isFinite(site.lat) && Number.isFinite(site.lon);
}

/** The saved sites, or null when this browser has never saved any -- the
 *  first visit, when there may be sites to import from the server. */
export function loadSites(): { sites: SiteSpec[]; selected: string | null } | null {
  if (!hasKey(KEY)) return null;
  const saved = readJson<Partial<Saved> | null>(KEY, null);
  return {
    sites: Array.isArray(saved?.sites) ? saved.sites.filter(isSpec) : [],
    selected: typeof saved?.selected === "string" ? saved.selected : null,
  };
}

export function saveSites(sites: SiteSpec[], selected: string | null): boolean {
  return writeJson(KEY, { version: 1, sites, selected } satisfies Saved);
}

const PRESETS = ["flat", "hilly", "trees", "ridge", "valley"];

/** The horizon rebuilt from the fields every version of the server sends,
 *  for one too old to send `horizon_spec` -- a browser can load the new page
 *  before the server behind it has been restarted, and the import on a first
 *  visit happens once. */
function horizonFrom(model: LocationModel): string | null {
  if (model.horizon_points) return JSON.stringify(model.horizon_points);
  if (PRESETS.includes(model.horizon_name)) {
    return model.horizon_facing !== null && model.horizon_facing !== undefined
      ? `${model.horizon_name}@${model.horizon_facing}` : model.horizon_name;
  }
  return String(model.horizon_max_deg);        // a uniform ring, "10 deg"
}

/** What to keep of a site the server described. `horizon_spec` is the
 *  profile exactly as a request takes it back, bearing and all. */
export function specFromModel(model: LocationModel): SiteSpec {
  return {
    key: model.key,
    name: model.name,
    lat: model.lat,
    lon: model.lon,
    elevation_m: model.elevation_m,
    bortle: model.bortle,
    horizon: model.horizon_spec ?? horizonFrom(model),
  };
}

/** The `site` parameter for a request. Built field by field, in a fixed
 *  order, so a site is always the same string -- the server caches on it. */
export function siteParam(site: SiteSpec): string {
  return JSON.stringify({
    key: site.key,
    name: site.name,
    lat: site.lat,
    lon: site.lon,
    elevation_m: site.elevation_m,
    bortle: site.bortle,
    horizon: site.horizon,
  });
}
