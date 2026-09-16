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
