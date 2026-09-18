# Astro Night Planner

Given a date and a location: is this night worth going out, and what should I
point at? See [PLAN.md](PLAN.md) for the full spec.

**Status: all phases (0-5) complete.** See [PHASES.md](PHASES.md) for per-phase state.

The code is MIT licensed ([LICENSE](LICENSE)). The bundled star catalogue is
not -- see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Setup

```bash
python -m venv .venv
```

```bash
.venv/Scripts/python.exe -m pip install -e ".[dev]"
```

On first run the DE440s ephemeris (~32 MB) is downloaded once into a cache
directory and never re-fetched:

| Platform | Cache location |
|---|---|
| Windows | `%LOCALAPPDATA%\astro-night-planner\ephemeris\` |
| Linux / macOS | `$XDG_CACHE_HOME/astro-night-planner/ephemeris/` (or `~/.cache/...`) |

Override with `ASTRO_EPHEM_DIR`. Set `ASTRO_EPHEM_NO_DOWNLOAD=1` to make a
missing cache a hard error instead of a network fetch — useful for confirming
the engine runs fully offline.

## Usage

```bash
planner night --date 2026-09-15 --location home
```

```bash
planner targets --date 2026-09-15 --limit 8
```

```bash
planner tonight
```

```bash
planner events --days 90
```

```bash
planner planets
```

```bash
planner log start && planner log suggest
```

Run the API and web UI together:

```bash
.venv/Scripts/python.exe -m uvicorn api.main:app --port 8000
```

```bash
npm --prefix web run dev
```

`--date` defaults to tonight and `--location` to the configured default.
"Tonight" follows PLAN.md §3.1: before local noon you are mid-session, so the
night in progress is the one that began yesterday.

Also available: `planner locations`.

On first use of `planner targets`, the vendored OpenNGC CSV is parsed once into
a SQLite cache next to the ephemeris. Both are local file reads.

## Layout

```
api/        FastAPI adapter over the engine; computes no astronomy itself
db/         SQLite: runtime locations, sessions, observations
web/        React + TypeScript + Vite, desktop-first
engine/     pure Python; no web imports, no I/O beyond local caches
  timeutil.py    tz-aware-UTC invariant, "tonight" resolution
  locations.py   locations.yaml -> Location, timezone from coordinates
  ephem.py       Skyfield setup, night_window(), altaz_series()
  equipment.py   limiting magnitude, extinction, airmass
  targets.py     filters, detectability, ranking, grouping
  planets.py     apparitions, oppositions, elongations, next visibility
  events.py      meteor showers, lunar eclipses, conjunctions
  scoring.py     dual deep-sky/planetary condition scores
  weather.py     Open-Meteo + 7Timer (networked; degrades to unavailable)
  geocode.py     place name -> coordinates (networked; degrades to empty)
  events.py      showers and eclipses are local; comet elements come from the
                 MPC through Skyfield's loader (networked; degrades to
                 "MPC unavailable"). No astronomy depends on it.
  horizon.py     obstruction horizon profiles
  session.py     the hours you plan to be outside, and the default (dusk-01:00)
  showpieces.py  the curated "popular targets" list, and why each entry is on
                 it or not
  reference.py   distances, discovery credits and planet constants: the facts
                 no ephemeris can derive
  constellations.py  sky regions, plus marks_toward() for measuring a horizon
                 by naming what you can see
  skybrightness.py   coordinate -> SQM from an optional local atlas
  catalog/       OpenNGC ingest (vendored CSV -> SQLite) and lookup
cli/        Typer front end; the only layer that converts UTC to local
config/     locations.yaml, equipment.yaml
tests/
```

## Where things appear

| | CLI | API | Web UI |
|---|---|---|---|
| Night window, twilights, moon | `planner night` | `/api/night` | The night |
| Condition scores + breakdown | `planner tonight` | `/api/night` | Score panel |
| Deep-sky targets | `planner targets` | `/api/targets` | Targets |
| Planets and apparitions | `planner planets` | `/api/planets` | Planets |
| **Showers, eclipses, conjunctions** | `planner events` | `/api/events` | **Upcoming events** |
| Altitude vs time | — | `/api/altitude` | Altitude chart |
| Observation log | `planner log …` | `/api/sessions` | Observation log |

## No eyepiece calculations

True field of view, magnification, exit pupil and "which eyepiece frames this
best" were removed at the user's request — common knowledge, not worth the app
restating. `planner equipment` and `GET /api/equipment` are gone with them, and
`config/equipment.yaml` no longer lists eyepieces (an `eyepieces:` section left
in the file is ignored rather than erroring).

What stayed, because it is aperture-derived and target filtering depends on it:
limiting magnitude, the aperture's magnitude gain over the dark-adapted eye,
airmass and extinction. Target listings now show the object's **angular size**
where the eyepiece suggestion used to be.

The observation log still has an `eyepiece` field — a free-text record of what
you actually used, not a calculation or a suggestion. Nothing pre-fills it.

## Observation log

`planner log start` opens a session and freezes the night's conditions onto
it. `planner log suggest` lists what to look at; `planner log add <id> M31
--rating 5` records it. The web panel does the same with one click per target.

Two things the log does deliberately:

- **The snapshot records what was *not* known.** Alongside the scores it stores
  `weather_available` and `is_gradeable`, so an entry can never read as a
  confident verdict on a night that had no forecast.
- **It feeds back into ranking.** Objects you have never logged get a small
  novelty bonus (PLAN.md §3.3.4). It is 3 points — enough to nudge, not enough
  to put a faint smudge above a well-placed showpiece.

## No site is configured for you

`config/locations.yaml` ships empty, and the planner will not assume a
location. When it gets dark, what clears your horizon, and which objects are
bright enough for your sky all follow from the coordinates — a plausible
default belonging to somebody else produces a complete, confident and entirely
wrong night. The CLI says so, and the web UI opens on a prompt to add a site
rather than a dashboard for a place you have never been.

## Adding an observing site

The web UI's **Sites…** dialog offers three ways in, because no single one
covers real observing sites:

- **Pick on map** — click a point. Most dark-sky sites have no name a
  gazetteer would know: during testing, `Lone Pine, Calif` returned zero
  results while fully online (Open-Meteo does not parse comma-qualified
  queries), and `Griffith Observatory` returns nothing at all because it is a
  landmark rather than a populated place. A click has no such gap.
- **Find by name** — faster when the place *does* have one.
- **Enter coordinates** — the fallback, and the one that still works with no
  network.

Coordinates are all the engine needs: the timezone is resolved from them with
`timezonefinder`, and every astronomical quantity follows from geometry. What
a click cannot tell you is **sky darkness**, so Bortle stays a required choice
in the form — "I don't know" is stored as a real null rather than silently
inheriting Bortle 5, and the site is then labelled "Bortle 5 (assumed)"
everywhere it appears. Filling that in automatically is the light-pollution
raster work in [HANDOFF.md](HANDOFF.md) item 2.

Map tiles come from OpenStreetMap under
[their tile usage policy](https://operations.osmfoundation.org/policies/tiles/),
which permits small personal and portfolio use with attribution and forbids
bulk prefetching. Nothing here prefetches. Re-read that policy before
deploying this anywhere public.

Your own sites do not belong in version control — a real observing site is
often a home address to four decimal places. `config/locations.yaml` ships
public examples only; put yours in `config/locations.local.yaml`, which uses
the same schema, is merged over the top, and is gitignored.

## Sky brightness from a light-pollution atlas (optional)

By default the Bortle class of a site is something you choose. If you have a
light-pollution raster, `engine/skybrightness.py` can read one instead:

```bash
pip install -e ".[skybrightness]"
```

```bash
ASTRO_SKYBRIGHTNESS_RASTER=/path/to/atlas.tif planner targets
```

Or simply drop the file at `config/skybrightness.tif`, which is checked when
the variable is unset. Either way the file is gitignored: it is a third-party
derived product and not ours to redistribute. With neither present the feature
is off and nothing changes.

A regional export is plenty. At 30 arcsec the grid is about 120 pixels per
degree, so a box covering a whole state is a few megabytes — a 10° box is
roughly 5.5 MB. Cover everywhere you might plausibly drive rather than just
the site you use now: outside the raster's coverage the answer is `None`, and
`None` falls back to assuming Bortle 5.

**No atlas ships with this project, deliberately.** The obvious dataset, Falchi
et al. 2016 ([DOI 10.5880/GFZ.1.4.2016.001](https://doi.org/10.5880/GFZ.1.4.2016.001)),
is not a public download — it is behind a request form — and is CC BY-NC, so a
derivative of it does not belong in an MIT repository. EOG's VIIRS annual
composites now need an account. Either file is gigabytes. Fetch one yourself
and point this at it.

**The raster must hold artificial sky brightness in mcd/m², not satellite
radiance.** This matters more than it sounds. A satellite measures light
leaving the ground beneath each pixel; the glow above your head is that light
scattered by the atmosphere from everything for tens of kilometres around. A
dark canyon an hour outside a city has almost no radiance of its own and still
sits under the city's dome. Bridging the two takes a light-propagation model —
that modelling is precisely what Falchi's atlas contributes, and what
lightpollutionmap.info's sky-brightness layer means by "light spread modeled
via convolution". Feeding raw VIIRS radiance to this module would produce
numbers that are systematically too dark exactly where people drive to observe.

The conversion is published rather than invented:

```
SQM = log10(B_total / 1.08e8) / -0.4       B in mcd/m²
B_total = B_artificial + 0.171168465       the natural night sky
```

after lightpollutionmap.info's published formula; `-1/0.4` is the Pogson factor
of `-2.5`. Two things anchor it in `tests/test_skybrightness.py`: the natural
sky with no artificial component returns exactly **22.00 mag/arcsec²**, the
accepted dark-sky value, and the relation round-trips against its own inverse.
Outside the raster's coverage, or over a nodata cell, the answer is `None` —
PLAN.md §2 asks for "we do not know" rather than a guess.

The tests that need a raster skip loudly when one is not configured, so a green
run never quietly means the lookup went untested.

## Obstruction horizons

Each location may carry an optional `horizon:` profile — a uniform angle, a
preset, a preset pointed at where the obstruction actually is, or an explicit
azimuth→altitude map:

```yaml
home:
  horizon: 20                                 # a uniform 20° ring, generic
  # horizon: trees                            # a named generic preset
  # horizon: {preset: ridge, facing: 250}     # pointed where the ridge is
  # horizon: {0: 18, 90: 5, 180: 5, 270: 22}  # or a measured profile
```

The web form offers the uniform angle in five-degree steps, because "how high
does the terrain reach?" is a question you can answer about your own site
without matching it to a description written for somebody else's. It is still
flagged generic: one angle in every direction is an assumption about a place,
not a survey of it.

**Measuring a real horizon without instruments.** The site form can ask what
the lowest constellation you can still make out is in each of four directions,
and take the altitude from the ephemeris. Anything below the thing you can see
is blocked, so that altitude is the obstruction height. The result is stored as
an az→alt map and is *not* flagged generic — it is the only way to produce a
measured horizon without editing YAML by hand.

The effective altitude floor becomes `max(min_altitude, horizon at the
object's azimuth)` — a horizon can raise the floor, never lower it.

| Preset | Assumes | Geometry | Changes the target list? |
|---|---|---|---|
| `flat` | nothing in the way | — | no |
| `hilly` | distant rolling terrain | 60 m at 300–400 m → 8–12° | **no** — below the 25° floor |
| `trees` | trees or buildings close all round | 15 m at 25 m → 31°, taken as 30° | yes |
| `ridge` | one side blocked close in | 150 m at 200 m → 37°, taken as 35° | yes |
| `valley` | two opposing sides blocked | same 35° walls | yes |

Each number is derived from a stated height and distance rather than picked to
feel right, so the assumption is auditable; the geometry is in the comment
beside each preset in `engine/horizon.py`. `ridge` and `valley` are
directional — set `facing` to the compass bearing of the obstruction. Which way
your view is blocked is the one thing about a horizon you can answer without
instruments, so it is asked for rather than assumed; `ridge` previously
hardcoded "assume the ridge lies north".

`flat` and `hilly` top out below the default 25° altitude floor and therefore
cannot change which targets are listed. That is the correct answer — distant
terrain does not matter if you are not observing that low — and the CLI, the
API (`horizon_binds`) and the web UI all say so outright rather than leaving
you to infer it from an unchanged list.

**The presets are generic assumptions, not surveys of your site.** Every use of
one is flagged generic in the CLI and the UI. An explicit az→alt map is treated
as measured and is not flagged; it is the only form here that is not a guess.

## Observing sessions

Everything used to be computed across the whole astronomical night. That
answers "what is this night like", which is not the question anyone asks —
nobody observes dusk to dawn on a Tuesday. A night clear until midnight and
overcast afterwards averaged out to "partly cloudy", and an object rising at
04:00 was listed as tonight's target.

A **session** is the hours you actually plan to be outside. The default is
astronomical dusk to 01:00 local, and it is editable under **More info**. The
condition scores, the temperature and cloud summaries, the target ranking and
the visible/late split are all computed over it — "late" means *after you pack
up*, not after midnight. The altitude chart marks the end of the session with a
dashed line, so a curve that only climbs to the right of it is visibly a
different night's target.

**The session is a plan, not a filter.** Target *windows* are the object's own
rise and set, not clipped to the hour you said you were going home, and the
list reaches past the session so that things rising later can be flagged late
rather than silently omitted. The same goes for the constellation group
headers: Orion in September reads `01:12–07:11 · visible late`, not a window
that stops dead at one in the morning.

What the session changes is the ranking: `usable_hours` (the part of the window
you can use) and whether the object transits while you are there both feed the
score, so something up for four of your four hours outranks something that
clears the horizon as you pack up.

Two things are deliberately scoped to the session rather than the night:
whether the **Moon is up** — it rises or sets during nearly every night, so
asking "at any point tonight?" made every sky moonlit — and the **peak
altitude** shown, which is the best you will actually see rather than the best
the object reaches at 4 a.m.

Both ends clip to sunset and sunrise, because the engine has nothing to say
about a daylit sky. Polar summer returns no session at all rather than a
fabricated interval.

`/api/night` and `/api/targets` take `session_start` and `session_end` as
ISO-8601 UTC instants and echo back the session they used, so a client never
has to guess the default. The planets tab is deliberately **not** narrowed:
`observing_span` assesses Mercury and Venus from sunset, because they almost
never clear an altitude floor with the Sun more than 18° down, and a session
beginning at astronomical dusk would report the brightest planet in the sky as
unobservable.

## The altitude floor is the site's horizon

There is no universal 25° floor in the web UI, and no control to set one. Each
site carries an obstruction angle — or a horizon measured by naming
constellations — and that *is* its floor. A site under trees and a site on a
playa are different places and now produce different lists.

The engine still takes `min_altitude_deg`, because the effective floor is
`max(min_altitude, horizon at the object's azimuth)` and the CLI wants a
general-purpose default. The web UI sends 0, which leaves the horizon alone to
decide. Low objects are not specially excluded: extinction is modelled, so
something at 3° scores accordingly rather than being hidden.

## Two things the scoring gets deliberately right, and one it does not

**The grade is the peak, the description is the night.** A two-hour clear
window is still worth driving out for, so the deep-sky and planetary grades
come from the best slot. Every *description* of the night used to come from
there too — and the best slot is by construction the least cloudy one. On a
night that is clear for an hour and overcast for six, the factor breakdown
reported a clear factor of 0.97 next to an hourly row reading 100%, and the
verdict blamed whatever came second, because cloud could never be the weakest
factor of the clearest slot. The verdict, the limiting factor and the left-hand
factor column are now the night's averages; the right-hand column is still the
best slot, labelled as such. When the best window covers less than 60% of the
dark time the verdict says how long it is, because "Workable" describing a
single clear hour is true and useless.

**Large bright objects are judged on their cores.** The contrast test compares
the sky to an object's *mean* surface brightness over its full catalogued
ellipse. For a large object with a concentrated core that statistic describes
the faint outer isophote and not what anyone sees — so from a Bortle 6 sky this
engine called the Orion Nebula, the Andromeda Galaxy, the Lagoon, the Eagle and
both Magellanic Clouds too faint, while passing M32, a compact companion nearly
five magnitudes fainter that happens to be small.

The catalogue carries no light-profile data, so the core brightness cannot be
computed. Integrated magnitude is the one discriminator available and it is a
sharp one: of the ~1,900 extended objects failing the contrast test at Bortle 6,
exactly twelve are brighter than magnitude 8, and they are the objects the
statistic is wrong about. Those get a 1.0 mag/arcsec² concentration bonus —
half an exponential disc's light falls inside 1.68 scale lengths while the
catalogued D25 diameter runs to 3.2–4, which is 0.65–1.13 magnitudes — and are
then put through the *same* test. That last part matters: an earlier version
simply exempted them, which made M31 immune to light pollution and scored it 77
from an inner-city sky. It now passes to Bortle 6 and fails from 7 up.

**What this still gets wrong.** M42 is rejected from Bortle 8, where it is
plainly visible — the Trapezium is orders of magnitude brighter than the
90-arcminute mean, and no single constant can express that. Separating M42 from
M31 needs a concentration index the catalogue does not carry.

## What a target's detail panel knows

Expanding a row shows a survey cutout and, in the column that used to hold
nothing but the attribution line, the object's facts: type, constellation,
distance, apparent size, magnitude, surface brightness, and who first recorded
it. Planets expand the same way.

**Distance is curated, not derived, and that is deliberate.** OpenNGC carries
`Pax` and `Redshift` columns and both are traps. Parallax is right for a
planetary nebula whose central star Gaia has measured — M27 comes out at 376 pc
against an accepted 380 — and nonsense for a galaxy: M31's row says 6.0 mas,
which is 167 parsecs, and M31 is 780,000 parsecs away. Redshift works for
distant galaxies and fails nearby, where peculiar motion swamps the expansion;
M31's is *negative*, so Hubble's law returns a negative distance. A number
wrong by a factor of 4,600 beside a photograph is worse than no number, so
`engine/reference.py` holds quoted values and anything without one shows no
distance.

That file covers the showpieces — the objects anyone actually opens — plus the
planets. The other twelve thousand entries get what the catalogue genuinely
knows, and every row the UI has no value for is omitted rather than printed
blank. `tests/test_reference.py` checks that every identifier still resolves,
that no showpiece has been left without an entry, and that the distances and
years are in plausible ranges; it cannot check that Halley really found M13 in
1714, which is what the sources named in the module docstring are for.

**Planet photographs are bundled, not hotlinked.** Seven NASA images,
208 KB the lot, in `web/public/planets/`. They are static and they never
change, so a runtime dependency on somebody else's CDN buys nothing and breaks
quietly later. All are public domain; credits appear under each image, in
`THIRD_PARTY_NOTICES.md`, and in the component.

Two candidates were rejected as misleading beside an observing list: Magellan's
radar map of Venus's surface, which no telescope has ever shown, and the JWST
portrait of Uranus blazing with rings. Three more were replaced for being
*unrecognisable* rather than wrong — Juno's Jupiter is a partial disc with the
Great Red Spot in an odd corner, the Voyager Saturn was grainy, and Mariner
10's ultraviolet Venus is blue and white. The set is now the view each planet
is usually pictured as: Hubble's OPAL portraits for Jupiter and Saturn, natural
colour for Venus, Voyager 2 for Uranus and Neptune. Even so, a
spacecraft close-up is not the eyepiece — Jupiter's Great Red Spot is a faint
notch, and Uranus and Neptune are featureless dots — which is the same caveat
the survey cutouts carry for deep-sky objects. Tonight's *actual* numbers
(apparent size, phase, ring tilt) sit in the fact sheet beside the photograph,
and those do change nightly.

## Popular targets

The catalogue holds 12,371 objects and perhaps 137 of them are what anyone
means by "things worth looking at". **Popular targets only** in the Targets
panel narrows either view — tonight's list or the whole catalogue — to a
curated list of showpieces.

The list and, more importantly, the reasoning behind it are in
`engine/showpieces.py`. In short: the Messier catalogue, minus the entries
that are not deep-sky objects (M40 is a double star, M73 four unrelated
stars), the one of disputed identity (M102), the dozen featureless Virgo and
Coma ellipticals, and the two that are genuinely hard (M74, M76); plus the
famous non-Messier objects, which are mostly the southern sky Messier never
saw — Omega Centauri, 47 Tucanae, the Magellanic Clouds, Eta Carinae — and
the northern ones that acquired names anyway: the Double Cluster, the Veil,
the Helix, the Coathanger.

Deliberately absent: the famous *photographic* targets. The Horsehead needs an
H-beta filter and a genuinely dark sky. It is a household name because of
pictures, and listing it under "popular targets" would send a beginner hunting
for something they will not see. It stays in the full catalogue, where the
scoring can say what it thinks of it.

`tests/test_showpieces.py` checks that every hardcoded identifier still
resolves against the catalogue, so a catalogue update cannot quietly shorten
the list.

## Timezone rule

All internal datetimes are timezone-aware UTC. Naive datetimes raise
`NaiveDatetimeError` rather than being silently assumed to be UTC or local.
Conversion to local happens only in `cli/`, via `timeutil.to_local`.

`tests/test_tz_boundaries.py` enforces this: it asserts every datetime on
`NightWindow` and `AltAzSeries` is aware-UTC, that naive input raises at each
engine entry point, and — by AST inspection — that no engine module imports
anything web-related, calls `utcnow()` or bare `now()`, or converts to local
time.

## Verification

Computed values are cross-checked against published external sources, per
PLAN.md §7. Reference night: **2026-09-15, Griffith Observatory
(34.11833, −118.300333)** — a public landmark, so anyone can re-run the
queries below and check the answers for themselves.

| Quantity | Computed | External | Source | Delta |
|---|---|---|---|---|
| Sunset | 18:59:30 PDT | 19:00 | USNO | −30 s |
| Civil twilight ends | 19:24:34 PDT | 19:25 | USNO | −26 s |
| Astronomical twilight ends | 20:23:46 PDT | 20:24 | sunrise-sunset.org | −14 s |
| Moonrise | 11:17:45 PDT | 11:18 | USNO | −15 s |
| Moonset | 21:22:33 PDT | 21:23 | USNO | −27 s |
| Moon illuminated (local noon) | 22.2% | 22% | USNO | +0.2 pp |

Every delta is under a minute against sources that publish to the minute.
USNO was queried in both `tz=-7&dst=false` and `tz=-8&dst=true` form; the two
agree exactly on every value, which is the cross-check that catches USNO's
double-DST trap.

Sources, both retrieved 2026-09-15:

- **USNO** — US Naval Observatory Astronomical Applications API v4.0.1,
  `aa.usno.navy.mil/api/rstt/oneday`. Elevation-aware. Does not publish
  astronomical twilight, hence the second source.
- **sunrise-sunset.org** — `api.sunrise-sunset.org/json`. Used only for
  astronomical twilight. Ignores site elevation and uses a lower-precision
  solar model; it disagrees with USNO on sunset by ~2 min, so it is not used
  for any quantity USNO publishes.

Tests assert a 2-minute tolerance, since published tables round to the whole
minute and sources differ slightly in refraction and horizon assumptions.
The literal external values live in
`tests/test_reference_agoura_2026_09_15.py` with per-value source comments.

**Not externally verified:** the true dark window (sun < −18° AND moon below
horizon) is a derived quantity that neither source publishes. Its inputs —
astronomical twilight and moonset — are each verified above, and the
intersection logic is unit-tested, but the combined figure itself rests on
our own computation.

### Phase 1 — catalog

Object positions are cross-checked against **SIMBAD** (CDS TAP service, queried
2026-08-25), which resolves coordinates independently of OpenNGC.

| Object | Ours | SIMBAD | Delta |
|---|---|---|---|
| M31 RA / Dec | 10.684792° / 41.269056° | 10.684708° / 41.268750° | +0.30″ / +1.10″ |
| M13 RA / Dec | 250.423458° / 36.461306° | 250.423475° / 36.461319° | −0.06″ / −0.05″ |
| NGC 869 Dec | 57.117250° | 57.133889° | −59.9″ |

NGC 869's 60″ disagreement is a definitional difference, not an error: it is a
30′-wide open cluster with no single agreed centre, and the gap is far inside
its own radius.

**Partly independent only:** V magnitudes agree exactly (M31 3.44, M13 5.80),
but OpenNGC and SIMBAD draw on shared upstream photometry. That agreement
confirms we parsed the column correctly — it is not an independent check of
the photometry.

**A trap worth recording:** SIMBAD reports V=15.769 for M57. That is the Ring
Nebula's *central star*, not the nebula (OpenNGC has it at V=8.8). M57 is
excluded from the magnitude check for that reason. Automating a cross-check
without reading it would have "verified" a wrong number.

**Not externally verified:** the detectability and ranking model. Limiting
magnitude, extinction and surface brightness are standard formulae, but the
contrast threshold and the score weights are tuned guesses, not calibrated
against observing reports. They rank targets sensibly against each other;
they are not detection predictions.

### Phase 2 — weather indices

The 7Timer indices run **lower = better**, and getting that backwards produces
plausible-looking garbage rather than an obvious failure (PLAN.md §7). Verified
two independent ways on 2026-08-25.

**1. Against 7Timer's own documentation** (`7timer.info/doc.php`):

| Index | 1 means | Max means |
|---|---|---|
| `cloudcover` | 0–6% | 9 = 94–100% |
| `seeing` | <0.5″ | 8 = >2.5″ |
| `transparency` | <0.3 mag/airmass | 8 = >1 |

The docs state "the smaller/bluer, the better". Our conversion maps 1 → quality
1.0 and the max → 0.0, matching.

**2. Against Open-Meteo**, which reports cloud cover as a direct percentage:

| | Observed | Documented |
|---|---|---|
| 7Timer horizon | 69 h | 72 h |
| Open-Meteo horizon | 15 d | ~16 d |
| Cloud cover agreement | mean 2.7 pp over 7 matched samples | — |

An inverted scale would have reported clear skies as overcast, so the
agreement is independent evidence of the direction.

**Not verified:** the scoring weights and curves. They use PLAN.md §3.2's
suggested starting values, which that section itself marks "tune later". No
comparison against real observing outcomes has been made — the scores rank
nights against each other, they are not calibrated predictions.

### Phase 3 — events and apparitions

| Quantity | Computed | External | Source | Delta |
|---|---|---|---|---|
| Lunar eclipse | 2027-02-20 23:12:54 penumbral | 23:14:06 penumbral | NASA GSFC | −72 s |
| Lunar eclipse | 2027-07-18 16:03:00 penumbral | 16:04:09 penumbral | NASA GSFC | −69 s |
| Lunar eclipse | 2027-08-17 07:13:47 penumbral | 07:14:59 penumbral | NASA GSFC | −72 s |
| Saturn opposition | 2026-10-04 | 2026-10-04 | EarthSky | 0 d |
| Uranus opposition | 2026-11-25 | 2026-11-25 | In-The-Sky.org | 0 d |
| Jupiter opposition | 2027-02-11 | 2027-02-11 | In-The-Sky.org | 0 d |
| Mars opposition | 2027-02-19 | 2027-02-19 | In-The-Sky.org | 0 d |

All three 2027 lunar eclipses match on **type** as well as date. The ~70 s
offsets reflect a different definition of "greatest eclipse"; a penumbral
minimum is shallow and so poorly constrained in time.

Saturn's ring tilt is checked against a physical landmark rather than a table:
it passes through zero in **March 2025**, the actual ring-plane crossing, then
opens steadily (+8.1° in Sept 2026, +18.9° by 2028).

Meteor shower parameters are vendored verbatim from the **IMO Working List**
(`engine/data/meteor_showers.json`), which carries its own source header and a
TODO to refresh annually.

**Not implemented — stated rather than faked:** solar eclipses. PLAN.md §2
wants a vendored NASA Five Millennium Canon slice; writing eclipse dates from
memory would be the exact failure §7 warns about. `planner events` prints
"not implemented" instead of an empty list that reads as "none".

**Incomplete:** comet magnitude filtering needs per-comet orbit propagation
from the MPC elements, which is not done.

## Tests

```bash
.venv/Scripts/python.exe -m pytest -q
```

Tests needing the ephemeris skip cleanly when the cache is empty. No test
touches the network; the external reference values are committed literals.
