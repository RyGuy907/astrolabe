# Astro Night Planner — Build Plan

A hand-off spec for Claude Code. Goal: given a **date** (default: tonight) and a **location** (default: configured home site), answer two questions — *is this night worth going out?* and *what should I point at, and when?*

---

## 1. Design decisions (made up front so Claude Code doesn't re-litigate them)

| Decision | Choice | Why |
|---|---|---|
| Language | Python 3.11+ for the engine | Skyfield/Astropy live here; no JS equivalent is as good |
| Ephemeris | **Skyfield** + JPL DE440s | Handles planets, Moon, rise/set/transit, comets/asteroids from MPC elements, eclipses |
| Interface order | CLI first → HTTP API → web UI | Each layer is testable before the next exists; a CLI is also just faster to use in the field |
| Backend | FastAPI + Uvicorn | Thin wrapper over the engine; auto OpenAPI docs |
| Frontend | React + TypeScript + Vite | Matches existing stack |
| Storage | SQLite (single file) | Personal-scale; observation log and location book fit fine. No server to run |
| API keys | **Zero required** | Every data source below is keyless. Keep it that way — it's the difference between "works forever" and "broke when a trial expired" |

**Hard constraint to enforce throughout:** the engine package must be importable and fully usable with no network for anything astronomical (ephemeris + catalogs are vendored/cached). Only weather and live event feeds hit the network, and every one of them must degrade gracefully to "unknown" rather than crash.

---

## 2. Data sources

### Weather — two sources, blended
1. **Open-Meteo** (`https://api.open-meteo.com/v1/forecast`) — primary. Keyless, hourly, ~16-day horizon.
   Request: `cloud_cover`, `cloud_cover_low`, `cloud_cover_mid`, `cloud_cover_high`, `temperature_2m`, `dew_point_2m`, `relative_humidity_2m`, `wind_speed_10m`, `wind_gusts_10m`, `visibility`, `precipitation_probability`, `is_day`. Set `timezone=auto`.
   Low/mid/high split matters: high cirrus kills transparency for galaxies but leaves planets workable; low cloud kills everything.
2. **7Timer! ASTRO** (`https://www.7timer.info/bin/api.pl?lon=..&lat=..&product=astro&output=json`) — the only free source of **astronomical seeing** and **transparency**. 3-hourly, **72-hour horizon only**, GFS-derived, refreshed every 6h.
   Indices: `seeing` 1–8 (1 = <0.5″, 8 = >2.5″), `transparency` 1–8 (1 = best), `cloudcover` 1–9, `liftedindex`. Note the inversion — lower is better for all of these. `ac` param applies altitude correction for sites >1000 m above surrounding terrain (relevant for Sierra/Utah sites).

**Blend rule:** cloud/humidity/wind from Open-Meteo (better resolution, longer horizon); seeing/transparency from 7Timer when the date is within 72h, otherwise fall back to a humidity-and-wind-shear proxy and clearly label it as estimated. Cache responses to SQLite keyed by `(lat_rounded, lon_rounded, model_run)` with a 3-hour TTL — do not re-fetch on every page load.

### Light pollution
There is **no good free coordinate→Bortle API**. Don't waste time hunting for one.
- **Phase 1:** Bortle class is a per-location field the user sets when saving a site. Ship the known sites pre-seeded.
- **Later (optional):** vendor a downsampled World Atlas / VIIRS raster (Falchi et al. 2016 or newer annual VIIRS composite) as a GeoTIFF and do a local pixel lookup with `rasterio`. Offline, no API, ~tens of MB. Do this only after everything else works.

Store sky brightness as **SQM (mag/arcsec²)** internally, with Bortle as a display convenience. Rough mapping: B1 ≈ 21.9, B2 ≈ 21.7, B3 ≈ 21.4, B4 ≈ 20.9, B5 ≈ 20.4, B6 ≈ 19.4, B7 ≈ 18.5, B8 ≈ 18.0, B9 ≈ 17.5.

### Deep-sky catalog
**OpenNGC** (`mattiaverga/OpenNGC`, CC-BY-SA) — NGC + IC in CSV: RA/Dec, object type, V/B magnitude, major/minor axis, position angle, surface brightness, common names, Messier cross-reference. Vendor the CSV into the repo, parse once into SQLite at build time.

Supplement with:
- **Messier list** — flag via the OpenNGC `M` column.
- **Caldwell** — small static JSON, worth adding.
- **Double stars** — WDS is unwieldy; ship a curated ~200-entry list of showpiece doubles (Albireo, Mizar, Almach, etc.) with separation and component magnitudes. Separation vs. seeing is the interesting filter here.

### Solar system
- Planets, Sun, Moon: DE440s via Skyfield.
- **Comets:** MPC current comet elements — Skyfield has `mpc.load_comets_dataframe()` + `mpc.comet_orbit()`. Refresh weekly, cache locally. Filter to predicted mag < 11.
- **Bright asteroids:** MPC `MPCORB` bright subset (Ceres, Vesta, Pallas, Juno, Iris). Optional.

### Events
- **Meteor showers:** ship the IMO Working List as JSON — `{name, radiant_ra, radiant_dec, drift, active_start, active_end, peak_date, zhr, velocity_km_s, parent}`. This needs a manual annual refresh; put a `# TODO: update for <year>` marker and a script that diffs against the published list.
- **Lunar eclipses:** compute with `skyfield.eclipselib.lunar_eclipses()` and filter to ones visible from the location.
- **Solar eclipses:** Skyfield's support is thin. Ship a JSON slice of the NASA Five Millennium Canon for the next ~15 years with global circumstances, and compute a rough local visibility check.
- **Conjunctions / appulses:** compute, don't fetch. Scan the next 90 days for planet–planet and Moon–planet separations below a threshold (5° for planets, 3° for Moon) using `find_minima` on the separation function.
- **ISS / bright satellite passes:** Celestrak TLE + Skyfield `EarthSatellite`. Nice-to-have, cheap to add, put it behind a config flag.

---

## 3. Core computations

### 3.1 Night window
Define **the night of date D** as sunset(D) → sunrise(D+1). Resolve "tonight" as: if local time is before 12:00, D = today; if after midnight but before noon, D = yesterday (you're mid-session, not planning the next one).

Compute and expose all of:
- Sunset / sunrise
- Civil (−6°), nautical (−12°), **astronomical (−18°) twilight** boundaries
- **Dark window** = sun below −18° **and** moon below horizon. Report both "astronomical night" and "true dark window" — they're different and the second one is what matters for galaxies.
- Moon rise/set, illuminated fraction, phase angle, altitude over time

Use `timezonefinder` to get the IANA zone from coordinates. **Store everything in UTC internally**, convert only at the display boundary. Every timestamp crossing a module boundary must be timezone-aware — no naive datetimes anywhere.

### 3.2 Condition scoring
Do **not** produce one number. Produce two, because a bright Moon with steady air is an excellent planetary night and a terrible galaxy night:

- **Deep-sky score** (0–100)
- **Planetary / lunar score** (0–100)

Compute per 30-minute slot across the astronomical night, then aggregate (report both the peak-hour score and the whole-night average, plus the best contiguous window).

Multiplicative, so any single dealbreaker dominates:

```
slot_score = 100 × clear × transparency × moon × seeing × wind × dew
```

Suggested factors — tune later, but start here:

| Factor | Deep-sky | Planetary |
|---|---|---|
| `clear` | `(1 − total_cloud/100)^1.5` | same |
| `transparency` | strong weight; high cloud and RH penalized hard | weak weight (0.8–1.0 range) |
| `moon` | `1 − 0.75 · illum · max(0, sin(moon_alt))^0.6` | `1.0` (Moon is irrelevant or is the target) |
| `seeing` | mild: `0.85 + 0.15 · seeing_quality` | **dominant**: `seeing_quality^1.5` |
| `wind` | full score below 10 mph, linear to 0.4 at 25 mph gusts | same (Dobs shake) |
| `dew` | dewpoint spread < 2 °C → 0.85 and raise a dew warning flag | same |

Also surface a separate **darkness duration** figure (hours of true dark) rather than folding it into the score — a 2-hour dark window that's crystal clear is still a good night, just a short one.

Output a letter grade + a one-line human verdict, and **always show the factor breakdown** so the number is auditable ("87 — clear, but the Moon is 68% and up until 01:20").

### 3.3 Target selection and ranking

For each catalog object, over the dark window sampled every 15 min:

1. **Hard filters**
   - Peak altitude ≥ `min_altitude` (default 25°, configurable; support an optional per-location horizon profile of az→min-alt for tree/ridge obstruction)
   - Above the altitude floor for ≥ 30 continuous minutes
   - Moon separation ≥ threshold that scales with moon illumination (e.g. `20° + 60° × illum` for faint extended objects, relaxed for clusters and doubles)
   - Detectability (below)

2. **Detectability**
   - Naked-eye limiting magnitude from site SQM.
   - Telescopic gain ≈ `5 · log10(aperture_mm / eye_pupil_mm)`; for a 203 mm aperture and a 7 mm pupil that's ~+7.3 mag over naked eye.
   - Apply extinction: `k · (airmass − 1)`, k ≈ 0.2 mag/airmass at a good site.
   - **For extended objects use surface brightness, not integrated magnitude.** A mag-8 galaxy spread over 10′ is harder than a mag-10 planetary nebula. Compare object SB against sky SB; require a contrast margin.

3. **Framing** — compute true FOV per eyepiece: `TFOV = AFOV / (scope_focal_length / eyepiece_focal_length)`. Rank the eyepiece whose TFOV puts the object at 15–50% of the field, and report it ("30 mm, 40×, 1.3° field — M31 overflows; best in the 30 mm").

4. **Ranking score** — weighted blend of: peak altitude, hours above floor, contrast margin, moon separation, transit time falling inside the dark window, and a small novelty bonus for objects not in the observation log.

5. **Grouping** — Solar System (planets, Moon, comets, asteroids) / Galaxies / Nebulae (emission, reflection, planetary, dark, SNR) / Open Clusters / Globular Clusters / Double Stars / Asterisms. Cap each group at N (default 8) and show a "best window" time range per object.

### 3.4 Planets specifically

For each planet report: altitude curve during darkness, transit time, apparent diameter, magnitude, illuminated fraction, and **where it sits in its apparition**:
- Days to/from **opposition** (outer planets) or **greatest elongation** (Mercury, Venus) — find via `skyfield.searchlib.find_maxima` / `find_discrete` on elongation and apparent-diameter functions.
- A trend label: *approaching opposition — improving*, *near opposition — optimal now*, *past opposition — window closing*, *too near the Sun*.
- Saturn only: ring tilt angle (it's the thing that changes visibly year to year).

**If a planet isn't observable tonight, say when it will be.** Search forward for the next date it clears `min_altitude` for ≥1 hour during astronomical night, and report the next opposition/elongation date alongside. Same treatment for seasonal DSOs — "Orion Nebula returns to the evening sky around late November."

### 3.5 Meteor showers
For any shower active on the date: compute radiant altitude through the night, and estimate observed rate:

```
ZHR_obs ≈ ZHR × sin(radiant_alt) × 2^(lm − 6.5) × (1 − cloud_fraction)
```

where `lm` is the limiting magnitude at the site with moonlight accounted for. Report the best hour (usually radiant-highest ∩ moonless).

---

## 4. Repo layout

```
astro-night-planner/
├── engine/                     # pure Python, no web, no I/O side effects
│   ├── ephem.py                # Skyfield setup, night windows, alt/az sampling
│   ├── catalog/
│   │   ├── loader.py           # OpenNGC → SQLite
│   │   └── data/               # vendored CSV/JSON
│   ├── targets.py              # filtering, detectability, ranking
│   ├── planets.py              # apparitions, oppositions, elongations
│   ├── events.py               # showers, eclipses, conjunctions, comets
│   ├── weather.py              # Open-Meteo + 7Timer clients, caching, blending
│   ├── scoring.py              # condition scores
│   └── equipment.py            # scope/eyepiece math, FOV, limiting mag
├── cli/                        # Typer or argparse
├── api/                        # FastAPI
├── web/                        # React + TS + Vite
├── db/                         # SQLite schema + migrations
├── config/
│   ├── locations.yaml
│   └── equipment.yaml
└── tests/
```

Keep `engine/` free of FastAPI imports. The CLI and API are both thin adapters over the same functions.

---

## 5. Config format

`config/equipment.yaml`:
```yaml
default_scope: ad8
scopes:
  ad8:
    name: "Apertura AD8 Dobsonian"
    aperture_mm: 203
    focal_length_mm: 1200
    focal_ratio: 5.9
    type: newtonian
eyepieces:
  - {focal_length_mm: 30,  afov_deg: 52, name: "30mm"}
  - {focal_length_mm: 13,  afov_deg: 82, name: "Astro-Tech 13mm UWA"}
  - {focal_length_mm: 9,   afov_deg: 52, name: "9mm"}
  - {focal_length_mm: 5.5, afov_deg: 60, name: "Astro-Tech 5.5mm"}
observer:
  eye_pupil_mm: 6.5
```

`config/locations.yaml`:
```yaml
default: home
locations:
  home:
    name: "Agoura Hills"
    lat: 34.1361
    lon: -118.7745
    elevation_m: 300
    bortle: 5
  santa_monica_mtns:
    name: "Santa Monica Mountains"
    lat: 34.10
    lon: -118.80
    elevation_m: 600
    bortle: 4
```

Locations should also be addable at runtime (geocode via Open-Meteo's free geocoding endpoint) and persisted to SQLite.

---

## 6. Build phases

Each phase ends with something runnable. Don't move on until the acceptance check passes.

### Phase 0 — Ephemeris core
Skyfield setup, DE440s vendored, location/time resolution, timezone handling, night-window computation, alt/az sampling helper.
**Accept:** `planner night --date 2026-09-15 --location home` prints sunset/sunrise, all three twilights, moon rise/set/illumination, and the true dark window. **Cross-check three of these values against Stellarium or JPL Horizons and record the comparison in the test file** — do not accept self-generated values as ground truth.

### Phase 1 — Catalog + targets
OpenNGC ingest, detectability model, filtering, ranking, grouping, eyepiece recommendation.
**Accept:** `planner targets --date 2026-09-15` outputs grouped targets with visibility windows and peak altitudes. Sanity check: M42 should not appear as a good September evening target; M31 should rank highly.

### Phase 2 — Weather + scoring
Open-Meteo and 7Timer clients with caching and offline fallback, dual scoring model, factor breakdown.
**Accept:** `planner tonight` prints both scores, the grade, the factor breakdown, the best observing window, and target list. Kill the network and confirm it still runs with weather marked unavailable.

### Phase 3 — Planets + events
Apparition tracking, next-visibility search, meteor showers, eclipses, conjunctions, comets.
**Accept:** `planner events --days 90` lists upcoming showers with expected rates, any eclipses, and conjunctions under 5°.

### Phase 4 — API + web UI
FastAPI endpoints; React UI with a date picker (default tonight), a location selector, the score dial with expandable breakdown, an hourly conditions strip, and collapsible target groups. An **altitude-vs-time chart** for the selected targets is the single highest-value visual — build that.

Suggested endpoints:
```
GET /api/night?date=&location=      → windows, moon, scores, hourly conditions
GET /api/targets?date=&location=&groups=&limit=
GET /api/planets?date=&location=
GET /api/events?from=&days=
GET /api/locations  |  POST /api/locations
```

### Phase 5 — Observation log (stretch)
```sql
sessions(id, date, location_id, start_utc, end_utc, scope_id,
         conditions_snapshot_json, seeing_actual, transparency_actual, notes)
observations(id, session_id, object_id, object_name, observed_at_utc,
             eyepiece_id, notes, rating, sketch_path)
```
The key UX move: **pre-populate the log from that night's computed target list** — checkboxes next to everything that was recommended, plus free-text add for anything else. Store a snapshot of the conditions at session time so the log stays meaningful after the forecast expires. Later: "objects I've never logged" as a ranking bonus, and a per-object history view.

---

## 7. Pitfalls to flag for Claude Code

- **Naive datetimes.** Enforce tz-aware UTC everywhere internally; add a test that asserts it.
- **7Timer index inversion.** Lower = better for seeing/transparency/cloud. Easy to get backwards and it silently produces plausible-looking garbage.
- **Forecast horizon mismatch.** 7Timer is 72h; Open-Meteo is ~16 days. Requests beyond each horizon must return an explicit "no forecast" state, not a silently degraded score.
- **Integrated magnitude ≠ surface brightness** for extended objects. Getting this wrong makes the target list recommend faint sprawling galaxies at Bortle 5.
- **Catalog dupes.** OpenNGC has duplicate/cross-referenced entries (NGC and IC designations for the same object). Deduplicate on ingest.
- **Rate limits.** Cache aggressively; both APIs are free and should be treated with restraint. One weather fetch per location per model run.
- **Ephemeris file size.** DE440s is ~32 MB. Vendor it or fetch once at install into a cache dir — don't download at runtime.
- **Don't trust self-generated astronomical values.** Every phase should include at least one cross-check against Stellarium, JPL Horizons, or the USNO/timeanddate values, committed as a test fixture.

---

## 8. Deployment

Local-first is fine, and honestly the CLI plus `uvicorn --reload` covers most real use. If you want it hosted, the existing EC2 + Caddy + GitHub Actions pattern from norhog transfers directly: build the Vite bundle, ship a tarball to S3 keyed by SHA, `ssm send-command` to deploy, run Uvicorn behind Caddy. The Python service can run under systemd rather than pm2 — slightly cleaner for a Python process.

---

## 9. Open questions to answer before Phase 4

- Mobile-first or desktop-first for the UI? (Field use argues mobile; the altitude chart argues desktop.)
- Does the target list need to respect an obstruction horizon at the home site, or is a flat altitude floor good enough for now?
- Astrophotography-relevant outputs at all (exposure planning, meridian flip timing), or purely visual observing?
