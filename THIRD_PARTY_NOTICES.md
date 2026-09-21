# Third-party data and services

The source code in this repository is MIT licensed (see [LICENSE](LICENSE)).
**The bundled astronomical data is not** — it carries its own licences, listed
below. If you fork, redistribute, or deploy this project, these obligations
travel with the data, independently of the MIT licence on the code.

This file is a good-faith summary written by the project author, not legal
advice. Verify the current terms at the linked sources before any commercial
use or large-scale redistribution.

---

## Bundled in this repository

### OpenNGC — deep-sky catalogue

- **Files:** `engine/catalog/data/NGC.csv`, `engine/catalog/data/addendum.csv`
- **Source:** https://github.com/mattiaverga/OpenNGC
- **Licence:** Creative Commons Attribution-ShareAlike 4.0 International
  (CC-BY-SA-4.0) — https://creativecommons.org/licenses/by-sa/4.0/
- **Modifications:** none. The CSVs are vendored byte-for-byte as published, so
  they can be checksummed against upstream. All processing — deduplication,
  V-band surface-brightness derivation — happens at load time in
  `engine/catalog/loader.py` and is never written back over these files.
- **What this means for you:** CC-BY-SA requires attribution and applies
  share-alike terms to adaptations of the data. Redistributing this repository
  means redistributing that catalogue; keep this notice with it.

### IMO Working List of Visual Meteor Showers

- **File:** `engine/data/meteor_showers.json`
- **Source:** International Meteor Organization,
  https://www.imo.net/resources/calendar/
- **Retrieved:** 2026-08-25. Provenance, units and an annual-refresh `_TODO`
  are recorded in the file's own header keys.
- **Modifications:** transcribed into JSON; radiant coordinates, ZHR, velocity
  and activity dates are reproduced as published.

### SIMBAD — coordinates for the named double stars

- **File:** `engine/catalog/data/double_stars.csv` — 20 rows, built by
  `scripts/fetch_double_stars.py`.
- **Source:** SIMBAD astronomical database, CDS, Strasbourg, via its TAP
  service. Retrieved 2026-09-17.
- **Why:** OpenNGC is a deep-sky catalogue. Its 244 entries typed `**` are
  unnamed NGC pairs, almost all far too faint to point at; Albireo, Mizar,
  Almach and Cor Caroli are not in it at all, because they are stars.
- **Taken from SIMBAD:** J2000 right ascension, declination, V magnitude
  where one exists for the composite, and parallax. Separations, component
  magnitudes and the one-line notes are quoted from standard observing guides
  and live in `engine/reference.py`.
- **Fetched at development time only.** `engine/` reaches the network from
  exactly two modules and this is not one of them; the engine only ever reads
  the vendored CSV.
- **Attribution**, as SIMBAD asks: *"This research has made use of the SIMBAD
  database, operated at CDS, Strasbourg, France."* Wenger et al. 2000, A&AS
  143, 9.

### NASA planetary imagery

- **Files:** `web/public/planets/*.jpg` — seven images, 208 KB in total.
- **Source:** NASA, via science.nasa.gov. Retrieved 2026-09-17.
- **Licence:** NASA imagery is not subject to copyright in the United States
  and may be reused freely. Credit is still owed and is shown in the app under
  each image, as well as here.
- **Credits:**
  | File | Credit |
  |---|---|
  | `mercury.jpg` | NASA / Johns Hopkins APL / Carnegie Institution — MESSENGER (PIA15162) |
  | `venus.jpg` | NASA / JPL-Caltech — Mariner 10, natural colour (PIA23791) |
  | `mars.jpg` | NASA / JPL-Caltech / MSSS — Mars Global Surveyor (PIA04304) |
  | `jupiter.jpg` | NASA / ESA / STScI — Hubble OPAL, 5 January 2024 |
  | `saturn.jpg` | NASA / ESA / STScI — Hubble OPAL, 22 October 2023 |
  | `uranus.jpg` | NASA / JPL-Caltech — Voyager 2 (PIA18182) |
  | `neptune.jpg` | NASA / JPL-Caltech — Voyager 2 (PIA01492) |
- **Modifications:** each was cropped square around the planet and resized to
  480 x 480. Jupiter's source is a two-panel Hubble comparison and the left
  globe was taken from it; Saturn's was trimmed clear of its outlying moons so
  they would not drag the crop. No colour or content was altered. The crop
  script is in the commits that added and revised them.
- **Why these ones.** Two candidates were rejected as misleading next to an
  observing list: Magellan's radar map of Venus's surface, which no telescope
  has ever shown, and the JWST portrait of Uranus blazing with rings. Three
  more were replaced for being unrecognisable rather than wrong — Juno's
  Jupiter, which is a partial disc with the Great Red Spot in an odd place; a
  grainy Voyager Saturn; and Mariner 10's *ultraviolet* Venus, which is blue
  and white. The set is now the view each planet is usually pictured as.
- **Bundled rather than hotlinked** because they are static, small, and a
  runtime dependency on a third-party CDN for unchanging bytes is a thing that
  breaks quietly later.

### Finder-chart stars and constellation figures

- **Files:** `engine/catalog/data/stars.csv` (41,411 stars to magnitude 8,
  ~1 MB) and `engine/catalog/data/constellation_lines.json` (the stick
  figures, 18 KB).
- **Source:** [d3-celestial](https://github.com/ofrohn/d3-celestial) by
  **Olaf Frohn**, files `stars.8.json`, `starnames.json` and
  `constellations.lines.json`. Retrieved 2026-09-21 by
  `scripts/fetch_star_chart_data.py`, which rebuilds both files.
- **Licence:** d3-celestial is BSD 3-clause, copyright (c) 2015 Olaf Frohn.
  Its star positions and names derive from the
  [HYG database](https://github.com/astronexus/HYG-Database) by
  **David Nash** (compiled from Hipparcos, the Yale Bright Star Catalogue and
  Gliese), which is CC BY-SA; the derived `stars.csv` is distributed under
  the same terms.
- **Modifications:** the GeoJSON is reduced to right ascension (converted
  from -180..180 to 0..360), declination, magnitude and a single label per
  star -- the proper name where there is one, otherwise the Bayer letter and
  constellation. Positions and magnitudes are unaltered.

### Full Moon photograph

- **File:** `web/public/planets/moon.jpg` — 480 x 480, 36 KB.
- **Source:** [`File:FullMoon2010.jpg`](https://commons.wikimedia.org/wiki/File:FullMoon2010.jpg)
  on Wikimedia Commons, by **Gregory H. Revera**. Retrieved 2026-09-20.
- **Licence:** [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/).
  Unlike the planet images this one is *not* public domain: attribution is
  required, and is shown in the app under the image as well as here. The
  resized file is distributed under the same licence.
- **Modifications:** the 2580 x 2452 original was padded to a square on black
  and resized to 480 x 480. Padded rather than cropped or stretched, so the
  disc keeps its shape. No colour or content was altered.
- **Why this one and not NASA's.** The public-domain candidates were the LRO
  Wide Angle Camera nearside mosaic, which is shaded relief under a low sun
  with its caption burned into the corner and looks nothing like the full
  Moon, and Galileo's 1992 view, which was taken off-axis and shows a slice of
  the far side along the left limb. This is a photograph through a telescope
  from the ground, which is the view the row describes.

---

## Fetched at runtime, not bundled

### JPL DE440s planetary ephemeris

- **Source:** NASA Jet Propulsion Laboratory, via Skyfield's loader.
- Downloaded once (~32 MB) into a per-user cache directory on first run and
  never re-fetched; see the Setup section of [README.md](README.md). It is
  deliberately **not** committed. Set `ASTRO_EPHEM_NO_DOWNLOAD=1` to make a
  missing cache a hard error instead, which is how the offline guarantee is
  tested.
- US Government work; JPL asks that the source be credited.

### Open-Meteo — forecast and geocoding

- **Endpoints:** `api.open-meteo.com`, `geocoding-api.open-meteo.com`
- **Source:** https://open-meteo.com — keyless. Free tier is intended for
  non-commercial use; check current terms before deploying publicly.
- Responses are cached to SQLite with a 3-hour TTL, keyed by rounded
  coordinates and model run, to keep request volume low.

### CDS hips2fits — deep-sky survey images

- **Endpoint:** `alasky.cds.unistra.fr/hips-image-services/hips2fits`
- **Source:** Centre de Données astronomiques de Strasbourg,
  https://cds.unistra.fr — keyless. Requested directly by the browser from a
  URL built out of each object's catalogued position and angular size, so no
  image data passes through this application's own server.
- **Survey used:** `CDS/P/DSS2/color`, chosen for whole-sky coverage.
  PanSTARRS is sharper but stops near -30 degrees declination, which would
  leave silent gaps for southern sites.
- **Required acknowledgement**, reproduced in the application footer and
  beneath every image: *"This research made use of hips2fits, a service
  provided by CDS."*
- No rate limits are documented, which is a reason for restraint rather than a
  licence for volume: images are requested only for target rows the user has
  opened, and lazily even then.

### OpenStreetMap — map tiles for the site picker

- **Endpoint:** `tile.openstreetmap.org`
- **Usage policy:** https://operations.osmfoundation.org/policies/tiles/ —
  permits small personal and portfolio use with attribution, and forbids bulk
  prefetching. Attribution is rendered by the map control; nothing prefetches.
  Re-read that policy before deploying publicly.

### Minor Planet Center — comet orbital elements

- **Endpoint:** `www.minorplanetcenter.net/iau/MPCORB/CometEls.txt`
- **Source:** IAU Minor Planet Center, https://www.minorplanetcenter.net —
  keyless. Fetched once into the local cache directory through Skyfield's
  loader, by `engine/events.py`.
- Used only by `planner events` for comet availability; no astronomy depends
  on it, the HTTP API never calls it, and it degrades to
  "MPC unavailable" rather than failing.
- The MPC asks that use of its data be acknowledged. Note its terms on bulk
  or automated querying before increasing how often this is fetched.

### NASA GIBS — VIIRS night-lights overlay

- **Endpoint:** `gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_Black_Marble`
- **Source:** NASA Global Imagery Browse Services,
  https://nasa-gibs.github.io/gibs-api-docs/ — keyless, no account. Requested
  directly by the browser as ordinary map tiles; nothing passes through this
  application's own server and nothing is vendored.
- **Why tiles rather than a vendored raster:** HANDOFF item 2 proposed
  vendoring Falchi et al. 2016 or a VIIRS composite. Both have since become
  gated. Falchi (DOI 10.5880/GFZ.1.4.2016.001) is behind a human-reviewed
  request form and is CC BY-NC; EOG's VIIRS annual downloads now redirect to an
  OAuth login. GIBS serves the same VIIRS data openly, so nothing needs to be
  downloaded, downsampled or relicensed.
- NASA data is openly available and asks to be credited. GIBS's API
  documentation does not state rate limits or a required attribution string, so
  the overlay is credited in the map's attribution control and requests are
  kept to what the user is actually viewing. Confirm current terms with NASA
  before any public deployment.

### 7Timer! ASTRO — seeing and transparency

- **Endpoint:** `www.7timer.info/bin/api.pl`
- **Source:** http://www.7timer.info — keyless, GFS-derived, 72-hour horizon.

### Reference sources used only in tests

These are not called by the application. They were queried by hand to produce
the externally-verified literals committed in `tests/test_reference_*.py`, each
with its retrieval date and the exact query URL recorded in the test docstring:

- **US Naval Observatory**, Astronomical Applications Department API —
  https://aa.usno.navy.mil/
- **sunrise-sunset.org** — https://sunrise-sunset.org/api
- **SIMBAD** (CDS, Strasbourg) — object coordinates
- **NASA GSFC eclipse canon**, **EarthSky**, **In-The-Sky.org** — eclipse and
  opposition dates

---

## Software dependencies

Python and JavaScript dependencies are declared in `pyproject.toml` and
`web/package.json` and installed from PyPI and npm rather than vendored. Their
licences ship with the packages themselves. The principal ones are Skyfield,
timezonefinder, FastAPI, Typer, PyYAML, NumPy, React and Vite.
