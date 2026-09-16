# Handoff — making the planner usable at any location

Three pieces of work, in the order they unblock each other. Phases 0–5 of
[PLAN.md](PLAN.md) are complete; see [PHASES.md](PHASES.md) for what was built
and [README.md](README.md) for the external verification already done.

**The engine is already latitude-general.** It was probed at six sites across
both solstices — Agoura Hills 34°N, Sydney 34°S, Quito 0°, Tromsø 70°N,
Longyearbyen 78°N, Ushuaia 55°S — and nothing crashed. Polar summer correctly
reports no astronomical night; polar winter scores normally. What is missing is
everything *around* the engine.

---

## Ground rules that already hold — do not break them

These are enforced by tests. Read them before touching anything.

1. **All internal datetimes are timezone-aware UTC.** Naive datetimes raise
   `NaiveDatetimeError`. Conversion to local happens only in `cli/` and in
   `web/src/format.ts`. `tests/test_tz_boundaries.py` enforces this by AST
   inspection.
2. **`engine/` imports nothing web-related** and only two modules may open a
   socket: `weather.py` and `geocode.py`, listed in `NETWORK_ALLOWED` in
   `tests/test_tz_boundaries.py:211`. Adding a third requires justifying it
   there. A separate test asserts the astronomy modules stay offline.
3. **`api/` is a thin adapter** (PLAN.md §4). No astronomy is computed in it.
   If you find yourself doing maths in `api/`, it belongs in `engine/`.
4. **`Location` is a frozen, hashable dataclass.** `night_window` is
   `lru_cache`d on it. A `dict` field silently makes it unhashable and breaks
   that cache — this has happened once with the horizon profile, which is why
   `HorizonProfile.points` is a tuple.
5. **Never assert your own computed values as truth** (PLAN.md §7). Every
   phase cross-checked against a published external source and committed the
   literals with provenance comments. Keep doing that.
6. **Tests must not touch the user's real observation log.** `tests/test_log.py`
   uses `tmp_path` throughout.

Run the suite with:

```bash
.venv/Scripts/python.exe -W ignore -m pytest -q
```

412 tests pass at handoff time. Frontend checks:

```bash
npm --prefix web run build
```

---

## Item 1 — Add-location UI

**Why first:** it is the only one of the three that is purely a blocker. The
API is finished and tested; nothing in the frontend calls it. Today a new site
means editing `config/locations.yaml` or POSTing by hand.

### What already exists

- `GET /api/geocode?q=` → `list[GeocodeCandidate]`, backed by
  `engine/geocode.py` (Open-Meteo geocoding, keyless). Returns `[]` when
  offline rather than raising.
- `POST /api/locations` → accepts either explicit `lat`/`lon` or a `query` to
  geocode. Resolves the timezone with `timezonefinder`. Persists to SQLite via
  `db/store.py`.
- `DELETE /api/locations/{key}` → 204, or **409 for YAML-defined sites**, which
  are config-owned and must not be deletable through the API.
- Client methods for these do **not** exist yet in `web/src/api.ts`.

### Build

1. `web/src/api.ts` — add `geocode(q)`, `createLocation(body)`,
   `deleteLocation(key)`, plus the `GeocodeCandidate` and `NewLocationRequest`
   types mirroring `api/schemas.py`.
2. `web/src/components/LocationManager.tsx` — a dialog or panel opened from the
   location `<select>` in `App.tsx`. It needs:
   - a search box calling `/api/geocode` (debounce it — see the search-perf
     note below);
   - a candidate list showing `label`, lat/lon, elevation;
   - fields for **Bortle** and **horizon preset**, both optional;
   - a manual lat/lon path for sites the geocoder does not know, which is most
     dark-sky sites;
   - delete, disabled with an explanation for `source === "config"` entries.
3. `App.tsx` — refresh the location list after a create/delete and select the
   new site.

### Watch out for

- **Debounce the geocode calls.** The target search had a 3.9 s per-keystroke
  stall from unbounded work on every input event; it is now fixed with
  `useDeferredValue`, memoised haystacks and render caps in
  `web/src/components/SkyPanel.tsx`. Do not reintroduce the pattern by firing a
  network request per keystroke.
- **A location with no Bortle silently falls back to Bortle 5**
  (`engine/targets.py`, `BORTLE_SQM[5]`). That is a real trap: the target list
  will look plausible and be wrong. Either require Bortle in the form, or show
  the assumed value prominently. Item 2 removes the need to ask at all.
- Offline, `/api/geocode` returns `200 []`, not an error. The UI must say
  "geocoder unreachable — enter coordinates manually" rather than "no results".

### Acceptance

Add a site by name and by raw coordinates, observe the whole dashboard
(night, targets, planets, events, chart, log) work for it, then delete it.
A YAML site must refuse deletion with a readable message. Tests in
`tests/test_api.py` already cover the endpoints; add frontend coverage only if
you set up a component test runner (there is none today).

---

## Item 2 — Light-pollution lookup (removes the manual Bortle guess)

**Why it matters:** `bortle` drives `sqm`, which drives limiting magnitude, the
surface-brightness contrast test, and the moon-glare threshold — i.e. *which
objects appear at all*. It is currently a number the user types from memory.

PLAN.md §2 is explicit: there is **no good free coordinate→Bortle API**, do not
go hunting for one. The intended solution is to vendor a raster and do a local
pixel lookup, which also keeps the engine offline.

### Source — verify before vendoring

Two candidates. **Confirm the licence and the exact download before committing
anything**; do not take the following as settled fact.

- **Falchi et al. 2016, World Atlas of Artificial Night Sky Brightness** —
  published via GFZ Data Services. This is the better fit because it gives
  *artificial sky brightness* directly, which converts to SQM without a
  radiance model. Check the current DOI and licence terms.
- **VIIRS annual composites** (Earth Observation Group, Colorado School of
  Mines) — upward radiance, not sky brightness. Usable but needs a published
  radiance→sky-brightness relation, which is an extra modelling step and an
  extra thing to verify.

Prefer Falchi unless you find a reason not to. Downsample to keep the vendored
file to tens of MB (PLAN.md §2's own guidance) and record the downsampling in
the file's provenance header, like `engine/data/meteor_showers.json` does.

### Build

- `engine/skybrightness.py`:
  ```python
  def sqm_at(lat: float, lon: float) -> float | None
  def bortle_from_sqm(sqm: float) -> int          # inverse of BORTLE_SQM
  ```
  `BORTLE_SQM` already lives in `engine/locations.py` with the mapping from
  PLAN.md §2. Return `None` outside coverage rather than guessing.
- `engine/locations.py` — when a `Location` has no `bortle`, fill `sqm` from the
  raster. **Keep `Location` frozen and hashable.** Do the lookup at construction
  in `load_locations` / `db.store`, not lazily in a property, or you will make
  the dataclass stateful.
- Add `rasterio` (or `numpy` + a plain binary grid, if that avoids a heavy
  dependency) to `pyproject.toml`.
- Surface the derived value in the UI: the location header should distinguish
  "Bortle 5 (measured, from atlas)" from "Bortle 5 (assumed)".

### Watch out for

- **This must not become a third networked engine module.** The raster is
  vendored; the lookup is a local file read. `NETWORK_ALLOWED` stays at two.
- **Do not silently overwrite a user-supplied Bortle.** An explicit value in
  `locations.yaml` is the observer's own measurement and outranks the atlas.
- SQM, not Bortle, is the internal unit (PLAN.md §2). Bortle is display only.

### Acceptance

`sqm_at()` for Agoura Hills should land near the configured Bortle 5
(SQM ≈ 20.4). Cross-check two or three sites against published values — a
recognised dark-sky park should come out Bortle 1–2, a city centre 8–9 — and
commit those as a test fixture with the source named, per PLAN.md §7.

---

## Item 3 — Southern-hemisphere external verification

**Why it matters:** every external cross-check so far was done at Agoura Hills
(34°N). The maths is latitude-general and the probe gives sane numbers, but
"probably fine" is not the standard the rest of this project has held to.

### Build

Add `tests/test_reference_southern.py`, mirroring the structure of
`tests/test_reference_agoura_2026_09_15.py` — which is the model to copy:
verbatim external values, a provenance comment per value naming the source and
retrieval date, and a stated tolerance with the reason for it.

Pick one southern site and one fixed date. **Sydney (−33.87, 151.21)** is a
good choice: it mirrors the northern reference latitude almost exactly, and
USNO and timeanddate both cover it.

Verify at minimum:

| Quantity | Source |
|---|---|
| Sunset, sunrise, civil twilight | USNO `aa.usno.navy.mil/api/rstt/oneday` |
| Astronomical twilight | sunrise-sunset.org (USNO does not publish it) |
| Moonrise, moonset, illumination | USNO |
| A planet's opposition date | EarthSky / In-The-Sky.org |

Notes from doing this for the northern site:

- **USNO double-applies DST** if you pass `tz=-7&dst=true`. Use `tz` as the
  *standard* offset with `dst=true`, or the true offset with `dst=false`, and
  cross-check the two agree. Sydney is UTC+10 standard, UTC+11 in DST — and its
  DST runs October to April, opposite to the north, which is exactly the kind
  of thing worth having a test for.
- **USNO's `fracillum` is a local-noon figure**, not a whole-night one. Compare
  it against a noon sample, not against `NightWindow.moon_illumination`, which
  samples the middle of the night deliberately.
- **timeanddate.com returns 403** to automated fetches. Do not build the check
  around it.
- Tolerance of 2 minutes is right: published tables round to the minute and
  sources differ slightly on refraction and horizon conventions.

Also worth asserting explicitly, since they are the things a northern-only test
suite would never catch:

- the moon-phase `waxing` flag is correct in the southern hemisphere;
- `_max_possible_altitude` and the target prefilter work for negative
  declinations — a Sydney list should be full of Centaurus, Crux and
  Sagittarius, and contain no Ursa Major;
- `engine/constellations.classify_visibility` returns `none` for far-*northern*
  constellations from Sydney, the mirror of the existing Crux assertion.

### Acceptance

The new test file passes with externally sourced literals, and its provenance
comments name every source and retrieval date. Add a Southern-hemisphere row to
the verification table in `README.md`.

---

## Things deliberately not done, so you do not re-litigate them

- **Solar eclipses** — removed at the user's request. They happen in daylight;
  a night planner has no use for them.
- **Eyepiece calculations** — TFOV, magnification, exit pupil and eyepiece
  recommendation were removed at the user's request as common knowledge. The
  aperture-derived detectability maths stays, because target filtering depends
  on it. `tests/test_equipment.py` pins the removal so it cannot creep back.
- **Comet magnitude filtering** — the MPC gives orbital elements and H/G, not
  predicted magnitudes. Filtering to "mag < 11" needs per-comet orbit
  propagation. `bright_comets()` reports availability only.
- **Score weights are uncalibrated.** They use PLAN.md §3.2's suggested starting
  values, which that section marks "tune later". Same for the contrast floor in
  `engine/targets.py` and the generic horizon presets. All three are documented
  as guesses in code and README; do not quietly present them as measured.
