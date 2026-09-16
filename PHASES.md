# Build phase state

Durable state for the phase loop. Read this first; it survives context loss and
new sessions. Source of truth for *what* each phase means is
[PLAN.md](PLAN.md) §6 — this file tracks only status and evidence.

**Rule: a phase is `done` only when its acceptance check has actually been run
and its output recorded below. Not when the code looks finished.**

| Phase | Name | Status |
|---|---|---|
| 0 | Ephemeris core | **done** |
| 1 | Catalog + targets | **done** |
| 2 | Weather + scoring | **done** |
| 3 | Planets + events | **done** |
| 4 | API + web UI | **done** |
| 5 | Observation log (stretch) | **done** |

---

## Phase 0 — Ephemeris core — done

**Acceptance (PLAN.md §6):** `planner night --date 2026-09-15 --location home`
prints sunset/sunrise, all three twilights, moon rise/set/illumination, and the
true dark window. Cross-check three values against an external source and
record the comparison in the test file.

**Result: passed.** Output recorded in [README.md](README.md#verification).
Five values cross-checked against USNO and one against sunrise-sunset.org, all
within whole-minute rounding. Literals committed in
`tests/test_reference_agoura_2026_09_15.py`. 85 tests pass.

**Not verified externally:** the true dark window — no published source has it.
Its inputs are each verified; the intersection is unit-tested only.

---

## Phase 1 — Catalog + targets — done

**Acceptance (PLAN.md §6):** `planner targets --date 2026-09-15` outputs grouped
targets with visibility windows and peak altitudes. Sanity check: M42 must NOT
appear as a good September evening target; M31 should rank highly.

**Result: passed.** 1,625 objects clear the filters for the reference night.
M31 ranks **1st among galaxies, 7th overall**. M42 ranks **310th** and does not
appear in the displayed Nebulae group. Both assertions are in
`tests/test_targets.py`. 151 tests pass.

**Delivered:**
- [x] `engine/catalog/loader.py` — OpenNGC → SQLite, 12,371 objects after dedup
- [x] `engine/catalog/data/` — vendored NGC.csv + addendum.csv (CC-BY-SA)
- [x] `engine/targets.py` — filters, detectability, framing, ranking, grouping
- [x] `cli` — `planner targets`
- [x] Tests including the M42/M31 acceptance check

**External check:** M31 and M13 coordinates agree with SIMBAD to ~1 arcsec.
See README. Magnitudes agree but are only *partly* independent — OpenNGC and
SIMBAD share upstream photometry sources.

**Two bugs found and fixed during the phase:**
1. The ranking score had no brightness term, so an anonymous mag-12 galaxy at
   the zenith outranked M31. This is why the acceptance check exists.
2. `best_eyepiece` returns None for objects both too large *and* too small for
   the field; treating both as "overflows" mislabelled small planetaries.

**Known weak spot:** the Double Stars group is poor. OpenNGC's `**` entries
have sparse magnitudes and no separations. PLAN.md §2 calls for a curated
~200-entry showpiece list — that is the fix, and it is not done.

---

## Phase 2 — Weather + scoring — done

**Acceptance:** `planner tonight` prints both scores, the grade, the factor
breakdown, the best observing window, and the target list. Kill the network and
confirm it still runs with weather marked unavailable.

**Result: passed.** Both halves verified — online it grades the night from live
Open-Meteo + 7Timer data; with `ASTRO_NO_NETWORK=1` it runs identically and
marks weather unavailable. 219 tests pass.

**Delivered:**
- [x] `engine/weather.py` — Open-Meteo + 7Timer clients, SQLite cache (3 h TTL),
      blending, and graceful degradation. The **only** networked engine module.
- [x] `engine/scoring.py` — dual deep-sky/planetary scores, six-factor
      multiplicative breakdown, letter grade, best contiguous window
- [x] `cli` — `planner tonight`

**External check:** the 7Timer index direction was verified against 7Timer's
own documentation (lower = better for all three indices), *and* cross-checked
against Open-Meteo — converted cloud cover agreed to 2.7 percentage points.
An inverted scale would have read clear skies as overcast. Observed horizons
also matched the documented ones: 69 h from 7Timer, 15 d from Open-Meteo.

**Three bugs found and fixed during the phase:**
1. With no weather, every weather factor defaults to 1.0, so the CLI printed
   "Deep-sky 100.0 grade A" for a date past the forecast horizon — exactly the
   silent degradation PLAN.md §7 warns about. Grades are now suppressed
   entirely when weather is unavailable.
2. The forecast was requested for the *true dark* window, which under a bright
   moon is minutes long, and the coverage test required an hourly sample to
   fall strictly inside it — so `planner tonight` reported "forecast does not
   cover that window" for tonight. Now requests the astronomical night and
   tests range overlap.
3. The "seeing estimated" warning fired on every night, because Open-Meteo
   returns 16 days and most of those hours are past the 7Timer horizon. The
   flag now describes only the hours actually scored.

**Not verified:** the factor weights and curves. They follow PLAN.md §3.2's
suggested starting values, which that section explicitly marks as "tune later".
No comparison against real observing outcomes has been made.

---

## Phase 3 — Planets + events — done

**Acceptance:** `planner events --days 90` lists upcoming showers with expected
rates, any eclipses, and conjunctions under 5°.

**Result: passed.** For 2026-09-15 +90 d it lists 5 showers with observed-rate
estimates, correctly reports no lunar eclipse in that window, and finds 10
conjunctions including Mars–Jupiter at 1.19°. 276 tests pass.

**Delivered:**
- [x] `engine/planets.py` — apparitions, oppositions/elongations, apparent
      diameter, magnitude, illuminated fraction, Saturn ring tilt,
      next-visibility search
- [x] `engine/events.py` — meteor showers, lunar eclipses, conjunctions, comets
- [x] `engine/data/meteor_showers.json` — vendored IMO Working List with an
      annual-refresh TODO
- [x] `cli` — `planner events`, `planner planets`

**External checks — all passed:**
- **Lunar eclipses vs NASA GSFC:** all three 2027 eclipses match on date and
  type, within ~2 min on greatest-eclipse time.
- **Opposition dates vs EarthSky / In-The-Sky.org:** Saturn 2026-10-04,
  Uranus 2026-11-25, Jupiter 2027-02-11, Mars 2027-02-19 — all exact.
- **Saturn ring tilt:** passes through zero in March 2025, the real
  ring-plane crossing, then opens steadily. A physical landmark rather than a
  published table.
- **Meteor showers:** vendored verbatim from the IMO Working List.

**Two bugs found and fixed:**
1. `almanac.fraction_illuminated` needs the kernel's own key, and DE440s
   stores outer planets as barycenters ("mars barycenter", not "mars").
2. **Venus reported "no return within a year."** PLAN.md §3.4 says to search
   during astronomical night, which is right for outer planets and wrong for
   inner ones — their elongation caps how far from the Sun they can get, so
   they almost never clear 25° with the Sun 18° down. Inner planets are now
   assessed sunset-to-sunrise and flagged as twilight objects. Mercury, which
   genuinely cannot reach 25° from 34°N, now reports the altitude it *does*
   reach instead of an unhelpful "no return".

**Deliberately not implemented: solar eclipses.** PLAN.md §2 asks for a
vendored slice of the NASA Five Millennium Canon. Skyfield's support is thin
and writing eclipse dates from memory is exactly the self-generated astronomy
PLAN.md §7 forbids. `solar_eclipses()` returns empty and the CLI says
"not implemented" rather than implying there are none.

**Also incomplete: comets.** The MPC gives orbital elements plus H/G
parameters, not predicted magnitudes. Filtering to "mag < 11" needs orbit
propagation per comet, which is not done. `bright_comets()` reports
availability only.

**Performance note:** `planner planets` takes ~16 s when a planet needs a
forward visibility scan (up to 400 days at a 5-day stride). `night_window` is
memoized, which is what keeps it from being far worse.

---

## Phase 4 — API + web UI — not started

**Acceptance:** FastAPI endpoints per PLAN.md §6; React UI with date picker,
location selector, score dial, hourly conditions strip, collapsible target
groups. The altitude-vs-time chart is the highest-value visual.

**Result: passed.** API and UI both run; the UI was driven in a real browser,
not just unit-tested. 333 tests pass, `tsc -b` and `vite build` are clean.

**Delivered:**
- [x] `api/` — FastAPI over the engine. `/api/night`, `/api/targets`,
      `/api/planets`, `/api/events`, `/api/locations` (GET/POST/DELETE),
      plus `/api/altitude`, `/api/equipment`, `/api/geocode`, `/api/health`.
      No astronomy is computed in `api/`; it resolves and serialises only.
- [x] `db/store.py` — SQLite for runtime-added locations, merged over YAML.
      YAML sites cannot be deleted through the API (409).
- [x] `engine/geocode.py` — Open-Meteo geocoding, degrades like weather.
- [x] `web/` — React + TS + Vite, desktop-first. Date picker defaulting to
      tonight (§3.1 rule reimplemented in `format.ts` to match the engine),
      location selector, score dials with expandable factor breakdown,
      altitude chart, hourly strip, planets table, collapsible target groups.
- [x] Altitude-vs-time chart as hand-rolled SVG: twilight and true-dark
      shading, altitude floor, hover crosshair reading all series. Clicking a
      target adds it to the chart.

**Three bugs found — two only because the UI was actually run:**
1. **Targets returned nothing on a bright-moon night.** `_observing_window`
   preferred the true dark window whenever it was non-empty; under a 97% moon
   that window is ~5 minutes, giving a single sample, and the target list came
   back empty. Now requires an hour of true dark before preferring it.
2. **The window was mislabelled.** `using_true_dark` was `bool(dark_intervals)`,
   so output said "true dark" while actually sampling astronomical night.
3. **Moonlight degraded surface-brightness contrast but not the point-source
   limiting magnitude**, so compact mag-12 galaxies passed under a full moon
   while diffuse ones were correctly rejected. Under a 97% moon the list went
   from 384 objects topped by a mag-12.7 galaxy, to 78 topped by a mag-5.6
   open cluster.

**Timezone rule across the wire:** the API emits ISO-8601 UTC only, plus the
site's IANA zone. `web/src/format.ts` is the single browser-side conversion
point, mirroring `engine/timeutil.to_local`. Times render in the *site's*
zone, not the browser's.

**`engine/geocode.py` is the second networked engine module.** The AST guard
caught it immediately; it was added to a short, documented allowlist rather
than the rule being loosened, and a new test asserts the astronomy modules
specifically remain offline.

**Gap found after the phase closed and since fixed:** the web UI had no events
view at all. `api.events()` existed in the TypeScript client but nothing called
it, so meteor showers, eclipses and conjunctions were reachable only via
`planner events` and `GET /api/events`. `web/src/components/EventsPanel.tsx`
now surfaces them with a 30 day / 90 day / 1 year horizon selector, including
the "solar eclipses not implemented" notice so an empty list is never read as
"there are none".

**Not done:** the UI has no way to add a location yet — `POST /api/locations`
and `/api/geocode` work and are tested, but nothing in the frontend calls
them. Sites must still be added via config or the API directly.

**PLAN.md §9 answered by the user 2026-08-25:**
- **Desktop-first.** The altitude-vs-time chart needs width; mobile gets a
  responsive fallback, not a separate layout.
- **Purely visual.** No exposure planning or meridian-flip timing.
- **Horizon: flat floor is fine, plus an optional generic profile** — done
  ahead of Phase 4, see below.
- **Continue through Phase 5.**

---

## Interlude — obstruction horizons — done

Not a numbered phase; requested by the user when answering the §9 questions,
and built before Phase 4 because it changes target and planet filtering.

- `engine/horizon.py` — `HorizonProfile`, azimuth→altitude with wrap-around
  interpolation, four presets (`flat`, `hilly`, `ridge`, `trees`), or an
  explicit measured az→alt map.
- `config/locations.yaml` — optional `horizon:` key; both seeded sites use
  `hilly`.
- `engine/targets.py` — the effective floor is `max(min_altitude, horizon at
  the object's azimuth)`. The horizon can raise the floor, never lower it.
- CLI warns on every use that a preset is **generic, not surveyed**, and says
  when the profile sits entirely below the altitude floor and so is having no
  effect.

**Honesty note:** the presets are assumptions about hilly terrain, not surveys.
They exist so a hilly site is not modelled as an open plain. `is_generic`
flags them everywhere; an explicit az→alt map is treated as measured and is
not flagged.

`HorizonProfile` stores points as a sorted tuple, not a dict — `Location`
embeds one and is frozen and `lru_cache`d, and a dict field silently made
`Location` unhashable, which broke `night_window`'s cache.

---

## Follow-up — the horizon presets were inert — done

Requested by the user 2026-08-28, after asking whether anything could
realistically be done about obstruction horizons given that manually surveying
a site is not practical.

**What was actually wrong.** The effective floor is
`max(min_altitude, horizon)`, and every preset topped out at or below the
default 25 deg altitude floor — `flat` 0, `hilly` 12, `trees` 20, `ridge` 25.
So no preset could change a single target at default settings, and the web UI
pins the floor at 25 with no control for it, which made the horizon feature
**completely inert in the UI for every site**. Both seeded sites use `hilly`
and were getting results identical to `flat`. Nothing was wrong with the
numbers; they described distant terrain that genuinely does not matter above
25 deg, and there was no preset for the close obstruction that actually blocks
a suburban yard.

**Delivered:**
- [x] Presets re-derived from a stated height and distance, so each is an
      auditable assumption rather than a magic number — the geometry is in the
      comment beside each one. `trees` 30 deg (15 m at 25 m), `ridge` 35 deg
      (150 m at 200 m), new `valley` 35 deg. `flat` and `hilly` unchanged and
      documented as deliberately below the floor.
- [x] **Direction.** `ridge` and `valley` are rotated onto a bearing via
      `facing`, replacing `ridge`'s hardcoded "assume the ridge lies north",
      which is wrong three times in four. Which way you are blocked is the one
      thing about a horizon answerable without instruments, so it is asked for.
      Surfaced in the UI as compass points, not degrees.
- [x] `engine/horizon.serialise` / `build`, and `parse_horizon` extended to
      read `ridge@250`, `{preset: ridge, facing: 250}` and a JSON map.
- [x] `horizon_binds` on `LocationModel`, so the CLI, API and UI all state when
      a profile is below the floor and therefore doing nothing.

**Bug found and fixed: a measured horizon could not be saved.**
`db/store.save_location` wrote `horizon.name`, which is `"custom"` for an
explicit az->alt map, through a ternary that turned it into NULL. It reloaded
as **flat with `is_generic=False`** — reading downstream as a *measured,
unobstructed* horizon. Losing the data was bad; mislabelling the loss as a
measurement was worse. Only hand-edited YAML could hold a real horizon. There
was no test file for `db/store` at all, which is how it survived; the round
trip is now covered through real SQLite for all five profile kinds.

**Verified in the browser, not just in tests.** Two sites at identical
coordinates and Bortle, differing only in horizon: 196 targets with an inert
profile, **171** with `ridge` facing south. The losses land where the physics
says they should for a southern obstruction at 34N — Sculptor, Fornax and
Piscis Austrinus eliminated entirely (39, 29, 19 -> 0), Sagittarius and Cetus
heavily cut, Ursa Major and Lynx untouched. 441 tests pass.

**Still an assumption.** The presets remain generic and are flagged generic
everywhere. A measured az->alt map is the only unflagged form. A DEM-derived
horizon was considered and rejected for now: it sees terrain only, so at a
suburban site it would confidently return "flat" while trees and rooflines do
the actual blocking.

---

## Phase 5 — Observation log (stretch) — done

**Acceptance:** PLAN.md specifies none for this phase, so one was defined:
start a session, have it pre-populated from that night's computed target list,
log an object, confirm the conditions snapshot persists, and confirm the log
feeds back into ranking. All five verified via CLI, API and browser.

**Result: passed.** 371 tests pass. The test suite writes only to temporary
databases and leaves the real log untouched.

**Delivered:**
- [x] `db/observations.py` — `sessions` and `observations` tables per PLAN.md
      §5, conditions snapshot, history, statistics
- [x] `engine/targets.py` — PLAN.md §3.3.4's novelty bonus, the last
      unimplemented ranking component
- [x] `api/log_routes.py` — session CRUD, observation CRUD, `/api/log/prefill`,
      `/api/objects/{id}/history`, `/api/log/stats`
- [x] `cli/log_commands.py` — `planner log start | suggest | add | list |
      show | history | stats | delete`
- [x] `web/src/components/ObservationLog.tsx` — the pre-populated panel

**The key UX move (PLAN.md §5), implemented as specified:** the log is
pre-populated from the night's computed target list. One click on a scored
chip logs the object with its recommended eyepiece; free text covers anything
else. Nobody retypes what the planner just recommended.

**Conditions snapshot.** Frozen onto the session at creation. It deliberately
records `weather_available` and `is_gradeable` alongside the numbers — a
snapshot reading "deep-sky 100" without noting there was no forecast would be
actively misleading years later, when nobody can check. Same no-silent-
degradation rule as PLAN.md §7, carried into the archive.

**The loop closes.** Logging an object makes it show as "seen before" in later
prefills, and removes its novelty bonus from the ranking.

**One design decision worth recording:** `logged=None` (no log context) and
`logged=set()` (an empty log) are treated differently. Collapsing them added
the novelty bonus to every object whenever a caller simply had no log to pass,
silently inflating every score. Likewise `previously_logged` is False with no
log context — claiming "seen before" without a log would be a fabrication.

**Not done:** ratings and per-observation notes are supported by the schema,
API and CLI, but the web panel only logs object, time and eyepiece — there is
no rating or notes input in the UI yet. Sketch paths are stored but nothing
reads or displays them.

---

## Post-Phase change — eyepiece calculations removed

Requested by the user 2026-08-26: eyepiece maths is common knowledge and does
not need restating by the app.

**Removed:** `Eyepiece`, `FramingOption`, `magnification`, `true_fov_deg`,
`exit_pupil_mm`, `framing_options`, `best_eyepiece`, `recommend_eyepiece`;
`TargetAssessment.framing` and `.overflows_field`; the `planner equipment`
command; `GET /api/equipment`; `FramingModel`, `EyepieceModel`,
`EquipmentResponse`, `LogCandidateModel.suggested_eyepiece`; the eyepiece
columns in the CLI and web target tables; the `eyepieces:` config section.

**Kept, deliberately:**
- Aperture-derived detectability — `telescopic_gain`, `telescopic_limiting_mag`,
  `airmass`, `extinction_mag`. These are not eyepiece maths and target
  filtering depends on them. `observer.eye_pupil_mm` stays because it feeds
  `5*log10(D/pupil)`, an aperture property.
- The observation log's `eyepiece` column and `planner log add --eyepiece`.
  That is a record of what was used, not a calculation or a suggestion, and
  PLAN.md §5's schema specifies it. Nothing pre-fills it any more.

Target listings show the object's angular size where the eyepiece
recommendation used to be. A test pins the removal so the functions cannot
quietly return, and another confirms a leftover `eyepieces:` section in config
is ignored rather than crashing the loader.

358 tests pass (13 eyepiece tests removed).
