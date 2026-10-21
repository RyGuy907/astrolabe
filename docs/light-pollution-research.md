# Building a better sky-brightness model for Astrolabe

Research report, October 2026. Scope: the lower-48 sky-brightness map
(`scripts/build_skyglow.py`), how it is turned into what an observer sees
(`engine/skybrightness.py`, `engine/equipment.py`, `engine/targets.py`), and
what it would take to make it as accurate as possible on a t3.micro with free
data that needs no API key.

## Evidence labels

Every number in this report carries one of these labels:

- **[M]** Peer-reviewed measurement.
- **[Mod]** Peer-reviewed model study.
- **[G]** Grey literature: theses, dataset documentation, agency reports,
  product pages, IDA applications.
- **[L]** Local analysis I ran for this report on the repository's own data
  (`data/skyglow/`). These were read-only scripts, and the method is given
  where each result appears.
- **[E]** My own estimate or arithmetic from cited numbers. It is not
  published.

If I could not find a value, the report says so.

---

## 1. Summary

### 1.1 What the offset of about 0.65 mag against Globe at Night most likely is

The offset is a sum of several known effects, not one missing factor on
artificial light. Fitting the published functional form of Falchi et al. 2016
to our own Globe at Night (GaN) data accounts for nearly all of it with
physically sensible parameters [L]. The pieces:

| Piece | Size | Applies to | Evidence |
|---|---|---|---|
| Natural sky brighter at solar maximum. F10.7 averaged about 160–200 sfu in 2024–25, against about 70 at minimum. | 0.3–0.5 mag at pristine sites | natural | [M] Krisciunas 1997 and 2007, Noll 2012 |
| SQM radiance against V luminance: Falchi's fitted S = 1.15 on the natural sky | 0.15 mag | natural | [M] Falchi 2016 |
| Milky Way and zodiacal light in the SQM's field of view on evening readings | 0–0.4 mag, up to about 1 mag near the galactic centre | natural | [M] Alarcon 2021 |
| Evening readings against the satellite's pass at about 01:30. Artificial light falls about 4.5–5% per hour. | about 0.15–0.2 mag on artificial light for a 21:00 reading | artificial | [M] Falchi 2016 d = −4.5%/h; Kyba 2015 about 5%/h; [L] −5.1%/h |
| GaN readers' own bias against the atlas, already in 2014 data | about 0.2 mag | all | [M] Falchi 2016 Table 3: GaN −0.21 ± 0.34 |
| LED spectral shift and real growth since 2014 that VIIRS cannot see | 0.2–0.9 mag on artificial light, depending on LED share and colour temperature | artificial | [Mod] Bará & Castro-Torres 2025; Sánchez de Miguel 2017; [M] Kyba 2023 |

My local fit, set out in section 2, is:

`B_obs = s·N_phys(F10.7, galactic latitude) + a·B_model·e^(c·h)`

It gives:

- **s = 1.22.** That is 0.21 mag on top of the physical natural sky, the same
  as Falchi's GaN bias.
- **a = 1.59** at midnight.
- **c = −5.1% per hour.**

This form removes the residual trend with brightness band and with time of
night. **The shipped ×1.55 on artificial light leaves GaN 0.2 mag (suburban)
to 0.6 mag (pristine) brighter than the map** [L], exactly as the audit
suspected. The research-grade comparison in the README says the uncalibrated
model is already within −0.03 mag at lit sites. So most of the ×1.55
"calibration" is really fitting the natural sky, evening timing and GaN's own
bias, and it should be replaced.

### 1.2 Prioritized roadmap

| # | Change | Effort | Expected gain (mag/arcsec²) | Evidence |
|---|---|---|---|---|
| **Quick wins (days)** | | | | |
| Q1 | Replace the flat 22.00 natural sky with a time-varying one: F10.7 from NOAA's keyless JSON, plus galactic latitude and ecliptic position. | 1–2 d | 0.3–0.5 at dark sites in 2024–25; 0.1–0.2 at solar minimum | strong [M] |
| Q2 | Replace ×1.55 with a joint fit: natural scale s, artificial scale a, time slope d. Weight each site once, use a robust loss, and treat GaN's bias as a nuisance term. | 1 d | Removes the 0.2–0.6 mag band-dependent bias against GaN [L]. Stops dark sites being predicted 0.4 too bright against research-grade sites | strong [L]/[M] |
| Q3 | Time of night: scale artificial light by (1 + d·h), with d ≈ −4.5 to −5%/h and h in hours after midnight. | 0.5 d | Up to 0.15–0.25 between dusk and 02:00 at lit sites; about 0 at pristine sites | strong [M] |
| Q4 | Switch naked-eye limiting magnitude to Crumey 2014 with an observer factor F, and add his surface-brightness limit for galaxies. | 0.5 d | Current formula is about 1.1–1.7 mag too optimistic at dark sites against Crumey F = 2. Not an SQM gain, but it changes what the app promises. | strong [M]/[Mod] |
| Q5 | Report the "SQM-band" value with an honest range: about ±0.3 now, from the 0.29 GaN scatter floor and ±0.1–0.25 instrument spread. | 0.5 d | Communication only | — |
| **Medium (weeks)** | | | | |
| M1 | Snow multiplier on artificial light from Open-Meteo `snow_depth`, applied over source and site. | 2–4 d | 0.3–1.0 mag brighter under snow, clear sky | moderate [M] (Falchi 2011, 6 sites) |
| M2 | Aerosol term from Open-Meteo / CAMS `aerosol_optical_depth`: more glow near cities, less far away. | 3–5 d | about ±0.2–0.6 mag near lit areas at AOD swings of 0.1–0.3; small at remote sites | moderate–weak [Mod]+1 field test |
| M3 | Explicit altitude physics instead of the NNLS haze layer, or validate the high-city question with ground data first. | 1–2 wk | 0.2–0.6 mag at the five Wasatch/Front Range cities. The *sign* is uncertain (see section 4.3). | weak / contested |
| M4 | Per-site, on-demand engine: polar histogram of light power by ring × azimuth (17 × 36) from the lights raster. Gives per-direction "light domes" and terrain shielding per sector. | 2–3 wk | Direction: horizon 10–30× zenith is now invisible. Terrain: 0.03 to >1 mag, site-dependent. | moderate [Mod]; terrain weak |
| M5 | Ground truth: NPS photometric reports (public domain), TESS (CC BY 4.0), GaN-MN fixed stations, Utah IDA applications; plus the user's own SQM transects in Utah County. | 1–2 wk, ongoing | Enables validation below about 0.3 mag | — |
| **Ambitious (months)** | | | | |
| A1 | Black Marble VNP46A4 snow-free near-nadir annual input. Earthdata account, manual download, no key on the server. | 1–2 wk | Removes snow-season bias in the input lights; size unquantified for skyglow | moderate [G]/[M] |
| A2 | LED / spectral correction layer: city-level LED fraction from SDGSAT-1 or ISS colour, or a single fitted spectral factor by time since conversion. | 1–2 mo | 0.2–0.9 mag in converted cities (artificial part) | [Mod], hard to validate |
| A3 | Garstang/Cinzano-style single-scatter angular kernel, K(d, altitude, Δazimuth), computed offline and calibrated so its zenith matches the fitted ring kernel. | 1–2 mo | All-sky and horizon brightness; also needed for Q1's zenith-angle airglow | [Mod] |
| A4 | Clouds: partly-cloudy amplification in cities and darkening at dark sites. | 1 mo | Up to 10–18× brighter in cities under overcast; down to about 0.6× at dark sites | strong [M], but a cloudy night is rarely a planning target |

The dependency order is Q2 → Q1/Q3, which share one fit, then M5, which you
need to tell whether M1–M4 help.

---

## 2. What the local data say (new analyses for this report)

Scripts lived in the session scratchpad and touched nothing in the repo.

**Data and filtering.**

- GaN 2024 and 2025 CSVs (CC BY 4.0), restricted to the lower 48.
- SQM readings between 14 and 23.
- Sun below −18° and Moon below −5°, computed with DE440s from the UT
  timestamps. I did not trust the campaign dates.
- Result: 2,857 rows, of which 1,988 are dark and **1,628 are dark and
  "clear"**, at **532 distinct sites**. Sites are rounded to 0.01°.
- Model values are the uncalibrated `model_2025.tif` and the Falchi crop
  `atlas.tif`.

### 2.1 Data-quality facts [L]

- `SQMSerial` is empty in every US SQM row, so there is no way to separate
  instruments.
- 59 clear rows carry 5-decimal readings. A handheld SQM reports 2 decimals,
  so these are probably app-derived or converted values.
- A few sites dominate the rows: 114 rows at one site near Carson City NV and
  99 at Emporia KS. **Weight each site once.** The row-level and site-level
  answers differ by up to 0.2 mag.
- Pristine sites (model SQM > 21.5): the per-site median GaN reading is
  **21.10**. Research photometers at pristine sites read 21.6–22.0, or about
  21.5 at solar maximum. Dark-site readings show **no** galactic-latitude
  dependence (21.10 / 21.14 / 21.08 for |b| 0–20 / 20–40 / 40–90). Physics
  says it should be about 0.4 mag. So GaN's dark-site readings are dominated
  by observer and instrument scatter. Use them only for relative shape, never
  to pin the absolute dark-sky level.

### 2.2 Functional form of the calibration [L]

Site-weighted, robust soft-L1 fits. Residual is model minus observed;
positive means the model is darker than the reading. All columns except the
first are median residuals.

| Form | median abs. residual | city <19 | 19–20 | 20–21 | 21–21.5 | >21.5 | evening <22:30 | late ≥22:30 |
|---|---|---|---|---|---|---|---|---|
| Shipped: 1.55·B_art + 22.00 natural | 0.47 | +0.07 | +0.20 | +0.21 | +0.39 | **+0.61** | +0.36 | +0.23 |
| Constant offset: total × 1.82, i.e. −0.65 mag everywhere | 0.29 | −0.15 | −0.03 | −0.09 | −0.02 | +0.03 | −0.01 | **−0.19** |
| Free natural floor + a·B_art. Fit: natural = SQM 21.24, a = 1.83 | 0.30 | −0.14 | −0.02 | −0.09 | −0.02 | +0.02 | −0.01 | −0.19 |
| **Falchi form: s·N_phys + a·B_art·e^(c·h)** | 0.30 | −0.13 | +0.01 | −0.13 | 0.00 | 0.00 | −0.04 | −0.05 |

In the Falchi-form row:

- N_phys = 22.00 sky × (1 + 0.00495·(F10.7 − 90)), using the monthly NOAA
  F10.7 for the reading's month, × a heuristic Milky Way term
  10^(0.16·e^(−(b/12°)²)).
- Fitted: **s = 1.22, a = 1.59, c = −5.1%/h**.

Readings of the fit:

- **The GaN noise floor is about 0.29–0.30 mag.** No functional form beats
  it. GaN can choose between forms by their *bias pattern*, not by scatter.
- A constant offset fits the brightness bands. That is the signature of a
  measurement or zero-point effect plus the natural sky, not of missing
  artificial light. It leaves a time-of-night trend, though. The physical
  form removes both trends.
- The physical form's parameters agree with the literature. The −5.1%/h
  matches Falchi's −4.5%/h and Kyba's about 5%/h. The extra natural scale of
  1.22 (−0.21 mag) matches Falchi's GaN bias of −0.21.
- That leaves a = 1.59 on artificial light at midnight. GaN data cannot tell
  whether that is real (LED shift and growth since 2014) or GaN bias that
  happens to scale with light, because local glare scales with how lit a site
  is. The README's 31 research-grade measurements (median −0.03 at lit sites,
  uncalibrated) argue for an *a* close to 1. The Kyba 2023 and LED literature
  argues for a > 1 today. **This one number needs research-grade ground truth
  from 2023 or later (M5).**

### 2.3 Time of night within the same site [L]

40 sites have at least 6 readings spanning at least 2 h. I took each reading
minus its site's mean and pooled them:

- Overall: **+0.042 mag/h** (Theil-Sen), meaning darker later.
- Lit sites (model < 20): +0.015 mag/h.
- Intermediate sites: +0.042 mag/h.
- Dark sites: +0.10 mag/h.

At dark sites this cannot be lights-out. It may be the end of twilight or
dark adaptation, or airglow decline. The literature puts airglow decline at
about 0.02–0.03 mag/h [M], so 0.10 is suspicious. Treat it as a GaN protocol
effect: readings taken soon after twilight.

### 2.4 Elevation [L]

Per-site medians of observed minus model:

- Below 300 m: −0.65.
- 300–900 m: −0.67.
- 0.9–1.5 km: −0.55.
- Above 1.5 km: −0.47.

The atlas shows the same pattern: −0.73 / −0.75 / −0.65 / −0.56. With only 27
sites above 1.5 km this is suggestive at most. It does *not* settle whether
Falchi or the model is right for high cities (see section 4.3).

### 2.5 Utah reference points [L + G]

Sampled from the map rasters at approximate coordinates:

| Site | Measured | Atlas (2014) | Model 2014 | Model 2025 | Shipped (×1.55) |
|---|---|---|---|---|---|
| North Fork Park, Ogden Valley, 1.67 km (IDA application, 5 SQMs, 2013–14) | **20.94** mean | 21.33 | 21.34 | 21.07 | 20.77 |
| Antelope Island SP, coordinates approximate (IDA application, SQM-L, 2016) | **20.79–20.88** new moon, Apr; 20.25–20.66 Dec inversion | 21.38 | 21.44 | 21.27 | 21.01 |
| St. George-area GaN observer (10 clear dark readings, 2025, standard deviation about 0.03) | **19.84–19.94** | 20.72–20.74 | — | 20.15–20.19 | 19.71–19.75 |
| Tibble Fork Reservoir | none found | 20.92 | 21.05 | 20.82 | 20.49 |

[E] Checking North Fork against the physics:

- 2013–14 was the cycle-24 maximum, F10.7 about 145.
- That puts the physical natural sky at about 21.74 in V, or about 21.6 in
  SQM terms with S = 1.15.
- Add model-2014 artificial light: predicted about 21.1 against 20.94
  measured.
- So the natural-sky physics closes about 60% of the gap, *without* any
  artificial-light multiplier.

The December Antelope Island readings are 0.2–0.5 mag brighter than April
ones under an inversion. This is the only quantitative Wasatch inversion
signal I found, and it confounds a different date, moon phase and snow.

### 2.6 Terrain at Tibble Fork, a crude estimate [L/E]

Method:

- Horizon angles from the 30″ ETOPO grid: 7–17° in every direction, 3.5°
  only toward the southwest canyon mouth.
- For every lit cell within 60 km, I computed the "shadow height" h_min. That
  is the height above the observer below which the column is not lit directly
  by that source over the intervening terrain:
  `h_min = max_x (z(x) − z0 − (zs − z0)·x/d) / (1 − x/d)`.
- I weighted cells by the Walker law, d^(−2.5).
- I integrated a single-scatter zenith column using Garstang's emission
  function, with Rayleigh scattering (9.6 km scale height) or Henyey-Greenstein
  haze (g = 0.7, 1.5 km scale height).

Result: the shadowed fraction is **0.32 for molecules (−0.42 mag) and 0.79 for
haze (−1.7 mag)**.

So the terrain effect at Tibble Fork plausibly lies between about 0.4 and 1.5
mag, darker than the map. That matches the reported experience. But it is a
toy calculation:

- single scattering only;
- no extinction;
- a 900 m DEM that under-resolves a canyon;
- Garstang's emission function.

The literature spans "a few hundredths" (Sunrise Rock, Cinzano & Elvidge 2004)
to a factor of 2.5 *brighter* with topography (Mont-Mégantic, Simoneau 2021,
where terrain raised the scattering column toward the observer). So terrain is
a real, site-specific effect. A general correction cannot be trusted without
local SQM readings.

---

## 3. State-of-the-art skyglow models

| Model | Altitude and aerosols | Terrain | Direction | Accuracy against ground | Code and data |
|---|---|---|---|---|---|
| **Garstang 1986/89/91** (PASP 98:364; 101:306; 103:1109) | Exponential molecules (c = 0.104 km⁻¹, scale height 9.6 km) and aerosols (a = 0.657 + 0.059K km⁻¹, about 1.4 km). Clarity K: K = 1 gives 0.33 mag vertical V extinction and 26 km visibility. | 1989: ridge shields the column below H·q/p. Cinzano 2000, eq. 22. | Full line of sight; any altitude and azimuth | Cerro Tololo at high zenith angle 44–72% brighter than predicted [M] (Krisciunas 2010, arXiv:1001.2246) | Formulas only. Transcribed in Cinzano et al. 2000, https://arxiv.org/abs/astro-ph/0003412 |
| **Cinzano, Falchi & Elvidge 2001** (MNRAS 328:689) | Sea level, K = 1, zenith. Altitude deliberately excluded. Cinzano FAQ: from sea level to 1350 m, artificial brightness falls by "less than about 20%" (0.24 mag) [G]. | No | Zenith only | — | All rights reserved |
| **Cinzano & Elvidge 2004** (MNRAS 353:1107, https://arxiv.org/abs/astro-ph/0406610) | Garstang plus GTOPO30 | DEM screening height per scattering point; observer horizon | Full sky | Agreed with NPS data after a uniform −0.3 mag scaling. Terrain changed Sunrise Rock by "a few hundredths" | Cost: 35 h + 10 h + 6 h per site on a 2004 Xeon, 20 GB of screening files per site |
| **Cinzano & Falchi 2012, LPTRAN** (MNRAS 427:3337, https://arxiv.org/abs/1209.2031) | Multiple scattering, Earth curvature, site and source elevation, custom aerosol layers | Not in the axisymmetric solution ("too heavy") | Yes | — | Not public. Walker exponent α ≈ 2.3(1 + d/1000), 8–120 km |
| **Falchi et al. 2016 World Atlas** (Sci. Adv. 2:e1600377, https://doi.org/10.1126/sciadv.1600377) | Garstang/Cinzano, US62 atmosphere, K = 1, sources to 195 km. "Maps were computed by taking into account the elevation above sea level of the sites" (GTOPO30): 0.21 / 0.15 / 0.11 mag extinction at 1 / 2 / 3 km. Falchi's 2024 thesis says the PSF was built with **sources at sea level and observers at their own altitude** [G, single source]. | Not included | Zenith only | Calibrated on 20,865 SQM observations (10,441 effective). σ = 0.15 mag. Table 3 residuals: GaN −0.21/0.34, NPS CCD +0.11/0.13, Unihedron −0.01/0.24 [M] | CC BY-NC 4.0, https://doi.org/10.5880/GFZ.1.4.2016.001 |
| **Falchi 2024 PhD thesis** (USC, https://minerva.usc.gal/entities/publication/e8145fdd-34a1-4d58-940a-cbeb72c759de/full) | PSFs for observers at 0–5.6 km and sources at 0–4 km, 0.12–527 km. Analytic fits for zenith, hemisphere, 30° and 0–10°. | No | Indicator PSFs | Analytic fit RMSE 0.049 dex. A research agent noted the analytic form misbehaves as h_obs → 0, so check it against the discrete tables before use. | CC BY-NC-ND |
| **Lorenz atlas 2025** (https://djlorenz.github.io/astronomy/lp/) | A recomputation of Cinzano's method on EOG VNL v2. No altitude correction is evident; I infer this from its basis, Lorenz does not state it. | No | Zenith | No published validation found | No licence stated; no method write-up found |
| **Kocifaj SkyGlow** (Appl. Opt. 46:3013, 2007; JQSRT 206:260, 2018; Kocifaj & Bará, MNRAS 490:1953, 2019) | All orders of scattering, aerosol microphysics, clouds. About 4 scattering orders needed at 60 km when AOD > 0.3; single scattering is adequate within about 30 km. | No | Full sky; two-index analytic all-sky pattern | Qualitative only | Free Windows binary after email registration (http://www.skyglow.sav.sk). Not open source. |
| **Duriscoe et al. 2018, NPS** (JQSRT 214:133; free PDF https://repository.library.noaa.gov/view/noaa/21007) | Garstang run for Las Vegas, K = 0.35, empirically flattened beyond 100 km | No. Calibration sites were unobstructed mountaintops, so the model is "worst case". | All-sky luminance ratio: ALR = c·Σ r·d^(−α(d)), α = 2.3·(d/350)^0.28. 38 rings to 300 km, 450 m grid. Lower 48 in about 1 h on 8 cores. | 24 NPS CCD sites, adjusted R² 0.98–0.998. No mag RMS reported. Measured ALR/ZLR 2.0–6.0; Garstang ratio 1.9 at 3 km, 2.7 at 10 km, about 3.0–3.15 at 30–90 km [M/Mod]. | Public domain |
| **ILLUMINA** (Aubé; JQSRT 211:25, 2018; MNRAS 2020, https://arxiv.org/abs/2005.14160; https://github.com/aubema/illumina, GPL-3.0) | 3D and hyperspectral, aerosols, 1st and 2nd scattering order | DEM blocking plus sub-grid obstacles | Full sky | 19 Catalan sites, V: mean 0.04, σ 0.18 mag [M] (Linares 2023, https://arxiv.org/abs/2403.00112) | 50,000–200,000 CPU-h per region. Infeasible here except as an offline reference for a few sites. |
| **Bará et al. PSF / FFT** (JQSRT 240:106658, 2020, https://arxiv.org/abs/1907.02891; Bará & Lima 2018, https://arxiv.org/abs/1809.05376) | Shift-invariant point-spread function | Their stated limit: breaks with "regional orographic features or noticeable altitude differences" | Per-indicator PSFs | — | Formally the same as Astrolabe's ring-kernel method |
| **Simoneau et al. 2021** (MNRAS, https://arxiv.org/abs/2103.03341) | ILLUMINA PSFs fitted as polynomials in log d | Topography *raised* Mont-Mégantic's predicted brightness 2.5× over the flat convolution. Convolution "not valid for non-flat territories". | Zenith | — | Coefficients in paper Tables A1–A2 |

**Implication.** Astrolabe's ring-kernel and FFT method is the accepted cheap
method; Bará and Duriscoe both use it. Its two known failure modes are the two
the audit found: altitude differences and terrain. Every heavier model that
handles them costs CPU-hours to CPU-years per region. The practical path is a
hybrid. Keep the fitted ring kernel as the zenith baseline, and apply
*multiplicative per-ring, per-sector corrections* from a single-scatter
Garstang geometry (sections 4.3–4.5).

---

## 4. Topic by topic

### 4.1 Night-lights input

**EOG VIIRS VNL v2.1/v2.2 annual** (current input).
- 15″ (about 464 m), annual, CC BY 4.0.
- Free account with a verified email, downloaded by hand.
- Since 1 June 2026, programmatic OpenID access is limited to paid or
  "nominal fee" users, per https://payneinstitute.mines.edu/eog/viirs [G]. A
  once-a-year manual download is fine.
- `median_masked` is the right layer.
- Monthly VCM/VCMSL products exist for seasonal work, but the product pages
  say nothing about snow screening.
- Monthly VIIRS radiance correlates with snow cover at r > 0.8 in
  northeastern US cities [M] (Levin 2017, RSE,
  https://doi.org/10.1016/j.rse.2017.03.003). Falchi 2016 notes snow-inflated
  November–December radiance in some towns.
- **Implication [E]:** in snowy Western valleys an annual median may still
  carry snow-season inflation, and that would make the 2025 map too bright
  there in summer.

**NASA Black Marble VNP46A4 / VJ146A4** (annual, 15″, HDF5 tiles of about
92 MB).
- Román et al. 2018, RSE 210:113, https://doi.org/10.1016/j.rse.2018.03.017;
  https://ladsweb.modaps.eosdis.nasa.gov/missions-and-measurements/products/VNP46A4.
- Lunar-BRDF, atmosphere and stray-light corrections.
- Separate *snow-free* and *snow-covered* composites, and *near-nadir*
  (0–20°) and off-nadir composites. The default variable is
  `NearNadir_Composite_Snow_Free`.
- Free Earthdata login, so the server still needs no key.
- No usage restrictions; cite the DOI.
- **I found no published head-to-head of A4 against VNL for skyglow.**
- Sketch: add a `crop_blackmarble()` beside `crop_lights()`, mosaic the
  roughly 6–8 tiles over the lower 48, and refit the kernel on 2014 A4
  against the atlas. Disk is about 0.5–1 GB per year during the build only
  [E].
- Validate with M5 ground truth in snowy valleys: Wasatch, Front Range,
  Upper Midwest.

**Overpass timing.** VIIRS passes at about 01:30 local.
- Falchi 2016 fits d = −4.5%/h, with h in hours after midnight; maps are
  calibrated near midnight to 1 a.m. [M].
- Kyba 2015, Sci. Rep. 5:8409: about 5%/h at light-dominated sites, before
  and after midnight [M].
- Posch et al. 2018, https://arxiv.org/abs/1803.09811, 26 Austrian SQMs, from
  22:00 to 02:00 [M]:
  - urban 0.04–0.14 mag/h;
  - intermediate 0.03–0.05;
  - rural about 0.
- Tucson: dimming streetlights from 90% to 30% after midnight cut zenith
  brightness only 5.4 ± 0.9% (city) and 3.6% (suburb). Streetlights are about
  14% of Tucson's skyglow, so the nightly decline is mostly non-street light
  [M] (Barentine 2020, https://arxiv.org/abs/2005.12357; Kyba 2021, LRT
  53:189).
- **No US-specific ratio of evening to 01:30 brightness was found.**

**LED spectral shift.**
- Kyba et al. 2023, Science 379:265, https://doi.org/10.1126/science.abq7781
  [M, naked-eye]: global sky brightening 9.6%/yr, 2011–2022. Press coverage
  gives about 10.4%/yr for North America; I did not verify that in the paper.
  The satellite-detected US trend is roughly flat.
- Bará & Castro-Torres 2025, JQSRT 335:109378,
  https://arxiv.org/abs/2409.13111 [Mod]:
  - VIIRS radiance per emitted lumen, LED against HPS: 0.62 at 4000 K, 0.70
    at 3000 K;
  - skyglow per lumen in the *photopic* band: only about 1.03–1.04;
  - mesopic 1.25–1.41;
  - scotopic 2.0–2.7.
- Sánchez de Miguel et al. 2017, MNRAS 467:2966,
  https://arxiv.org/abs/1701.05019 [Mod]: at constant photopic output an SQM
  reads 0.17 mag (3000 K) and 0.37 mag (4000 K) brighter than under HPS.
  SQM − V spans 0.25–0.8 mag by lamp type.
- [E] Combining the two: SQM brightness per unit of VIIRS radiance, relative
  to an HPS-era calibration, is about ×1.7 (0.56 mag) for 100% 3000 K and
  about ×2.3 (0.89 mag) for 100% 4000 K. At a 50% LED lumen share it is about
  0.26–0.43 mag.
- That would make a fitted a ≈ 1.6 plausible on physics alone. But it is
  uniform only if every city converted equally, and they did not.
- **Data for a per-city LED fraction:**
  - SDGSAT-1 Glimmer: RGB at 40 m, about 21:30 local, free registration at
    sdg.casearth.cn, licence unclear, tasked rather than systematic; LED/HPS
    classification about 90% accurate (Lv 2024, Applied Energy 355) [M].
  - ISS photographs: NASA, credit required, irregular
    (https://eol.jsc.nasa.gov/).
  - Neither is a turnkey US layer. Hence ambitious item A2.

**Upward light function.** Falchi's fitted weights are Wa = 1.9e-3
(Lambertian), Wb = 5.2e-4 (low angle) and Wc = 7.6e-5 (30°). Low-angle light
dominates glow far from cities. A kernel fitted to the atlas inherits this. It
tilts near against far, not a uniform offset [Mod].

### 4.2 Natural sky (Q1)

- **Solar cycle.**
  - Krisciunas 1997 (https://arxiv.org/abs/astro-ph/9706111): Mauna Kea
    yearly V 21.29–21.91, and B_nL = 47.021 + 0.2019·F10.7 [M].
  - CTIO 2000 against 2006: 21.56 against 22.06 [M] (Krisciunas 2007,
    https://arxiv.org/abs/0705.1153).
  - Noll et al. 2012, A&A 543:A92 (https://arxiv.org/abs/1205.2003): zenith V
    22.0 / 21.8 / 21.6 at 90 / 130 / 180 sfu [M-based model].
  - Usable fit in linear units relative to 22.0 [E, exact for Noll's three
    points]: **F_nat = 1 + 0.00495·(F10.7 − 90)**, with F10.7 clamped to
    65–250 and its 27–81-day mean used.
  - Uncertainty about ±0.15 mag. Published cycle amplitudes range 0.3–1.0 mag.
- **F10.7 source (keyless):**
  - Daily: https://services.swpc.noaa.gov/json/f107_cm_flux.json, with
    `flux` and `ninety_day_mean`.
  - Monthly: https://services.swpc.noaa.gov/json/solar-cycle/observed-solar-cycle-indices.json.
    For 2024: 155–246. For 2025: 131–189.
  - Forecast: https://services.swpc.noaa.gov/json/solar-cycle/predicted-solar-cycle.json.
    It falls to about 95 by early 2028.
  - Cache it daily. Offline fallback: the predicted curve.
- **Zodiacal light.**
  - Leinert et al. 1998, A&AS 127:1 (https://doi.org/10.1051/aas:1998105),
    Table 17: brightness by ecliptic latitude β and elongation from the Sun.
    The ecliptic pole is about 23.3 V mag/arcsec²; at β = 0 and 90° from the
    Sun it is about 22.0.
  - Contribution: ΔF_ZL = 0.302·(T − 77)/77 in the units above, with T the
    table value in 1e-8 W m⁻² sr⁻¹ µm⁻¹.
- **Milky Way / integrated starlight.** Alarcon et al. 2021, AJ 162:25
  (https://arxiv.org/abs/2105.01066), from TESS and SQM: up to +1 mag near the
  galactic centre and about +0.4 mag elsewhere in the plane [M]. Masana et al.
  2021, MNRAS 501:5443 (https://doi.org/10.1093/mnras/staa4005), GAMBONS
  model, average zenith V at 40°N [Mod]:
  - airglow 165;
  - zodiacal light 95;
  - integrated starlight 75;
  - diffuse galactic light 10 nW m⁻² sr⁻¹;
  - total about 21.5.

  Simplest implementation: a heuristic term, −0.4·e^(−(b/12°)²) mag, deepened
  toward the bulge. Better: sample GAMBONS (http://gambons.fqa.ub.edu) once
  offline into a small HEALPix table.
- **Zenith angle.** The airglow van Rhijn factor is (1 − 0.972 sin²z)^(−0.5).
  Brightness rises toward the horizon, about 2× at z = 60° and about 4× at z = 80° before extinction. Apply it to the
  airglow share only, about 0.65–0.8 of the natural total [E from Noll].
- **Through the night.** Airglow declines about 0.02–0.03 mag/h (Patat 2003;
  Krisciunas 1997) [M]. That is small; optional.
- **SQM against V.** Cinzano 2005 lab report
  (https://www.unihedron.com/projects/darksky/cd/reference/sqmreport_v1p4.pdf):
  V = SQM − 0.17 ± 0.07 for sky. Falchi's fitted S = 1.15 (0.15 mag) is the
  same effect [M].
  - At the darkest NPS sites a handheld SQM reads 0.4–0.7 mag *brighter* than
    CCD V; for example Dinosaur NM, CCD 22.0–22.1 against SQM 21.4–21.6.
  - NPS writes that the SQM "does not reliably measure" skies darker than
    about 21.5 [G].
  - **So if the app predicts what a user's SQM will read, its natural floor
    should be near 21.6–21.85, not 22.00.** Even at solar minimum.
- **Cost.** Microseconds per evaluation. A small Leinert table (7 × 7 floats)
  and optionally a HEALPix nside = 16 Milky Way table (3,072 floats). No
  storage concern.
- **Validation.** Fit s with dark GaN sites excluded (they are noise; section
  2.1). Then check the predicted dark-sky readings against NPS reports and
  TESS dark stations at solar minimum: La Palma P50 21.94 in the TESS band,
  per Alarcon 2021.

### 4.3 Altitude, aerosols and the high-city question (M2, M3)

**Why the model and the atlas differ for high cities.**

- The atlas reportedly places sources at sea level and observers at their own
  altitude (Falchi 2024 thesis, section 4.3.4) [G]. A Denver observer is
  therefore about 1.6 km "above" Denver's own lights in the atlas's geometry.
  At K = 1, 63% of the sea-level vertical optical depth is aerosol, and only
  about 32% of the aerosol column lies above 1.6 km. That removes much of the
  near-field glow.
- Physically, though, a city and its residents sit in the *same* air, aerosol
  included. In the thesis's same-altitude runs, altitude matters little near
  the source: the higher pair is darker within about 10 km, brighter beyond.
- So the 0.2–0.6 mag "elevation bias" may be partly the **atlas's**
  convention, not a model error. Lorenz's model, which has no altitude
  correction, agrees with ours to about 0.15 at these cities (README).
- The Cinzano FAQ says going from sea level to 1350 m dims artificial
  brightness by less than 20% (0.24 mag) [G].
- The GaN high-elevation evidence is thin: 27 sites [L].
- **I found no research-grade SQM data for downtown Denver, Salt Lake City or
  Provo** that would settle it. The North Fork Park data (section 2.5) favour
  a *brighter* sky than the atlas, but that site is 30–40 km from Ogden, and
  the difference there is mostly natural sky.

**Recommendation.** Do not force the model to match the atlas at high
elevation. Model altitude physically:

- Use relative heights: observer minus source for geometry; Garstang's
  layers for density.
- Constrain it with ground truth (M5): one SQM transect in Salt Lake City and
  Provo would decide it.
- Until then, keep the current behaviour. It is the "safe", brighter side,
  and it agrees with Lorenz.

**Aerosols, AOD.** Kocifaj & Barentine 2021, Sci. Rep. 11:14622
(https://doi.org/10.1038/s41598-021-94241-1) [Mod + one field test]. Ratio of
zenith brightness in clean air to polluted air (AOD 0.3):

| Distance | Clean/polluted ratio |
|---|---|
| 1.3 km | 0.31 |
| 3 km | 0.40 |
| 7 km | 0.60 |
| 15 km | 1.40 |

The crossover is about 12 km [E]. Their Vienna test (AOD 0.40 → 0.06) cut zenith
brightness to 0.57 ± 0.04 of its value, about 1.8 mag per unit AOD.

Other evidence:
- Ściężor & Kubala 2014, MNRAS 444:2487 [M]: about 0.010–0.029 mag per µg/m³
  PM10. Confounded with season.
- Posch 2018 [M]: the seasonal amplitude is about 1.5 mag at urban sites and
  about 0.5 mag rural, tracking PM10.
- Falchi 2016: K = 0.5 for the western US changed predictions by about 0.1
  mag against NPS data [M].

Data: the Open-Meteo Air Quality API
(https://open-meteo.com/en/docs/air-quality-api) has `aerosol_optical_depth`
(550 nm), `pm2_5`, `pm10` and `dust`.
- Source is CAMS global at 0.4° (about 45 km), 3-hourly, 5–7-day forecast.
- Keyless for non-commercial use, CC BY 4.0, attribution to CAMS and
  Open-Meteo.
- Caveat [E]: at night CAMS AOD is model-propagated; satellite AOD is daytime
  only.
- MERRA-2 needs an Earthdata login, which is fine for a climatology built
  offline.

Sketch:
`M_aod = 10^(0.4·k(d)·(AOD − 0.2))`, applied per ring.
- k ≈ +3 mag per unit AOD for rings under 5 km;
- +1.8 at about 7 km;
- 0 at 12 km;
- about −1 beyond 15 km.

This needs the per-ring decomposition from M4. Without it, use a proxy:
positive k where the site's own artificial glow is mostly local (SQM < 19),
and slightly negative k at rural sites.

Uncertainty: about a factor of 2 in k. Validate against TESS time series
alongside CAMS AOD, at Tucson and Oracle in the US.

**Inversions and humidity.**
- **No measurement study links boundary-layer height or inversions to
  skyglow.** That includes Salt Lake City.
- Indirect: surface aerosol is 18–58% higher under surface inversions at the
  ARM SGP site [M]; Salt Lake Valley inversion climatology
  (https://www.inscc.utah.edu/~hoch/daqstudy.xhtml) [G].
- Humidity and hygroscopic growth are qualitative only.
- Open-Meteo exposes `boundary_layer_height`, `relative_humidity_2m` and
  `temperature_<level>hPa`. I could not confirm which US models supply them.
- Treat inversions through the AOD and PM2.5 term rather than a separate
  model.

### 4.4 Snow (M1)

- Falchi 2011, MNRAS 412:33 (https://doi.org/10.1111/j.1365-2966.2010.17845.x)
  [M, clear sky, 6 site comparisons]: zenith brightness with heavy snow
  against usual ground is ×1.3–2.6 (0.3–1.0 mag). The direct-uplight share is
  about 0.92–0.94 at rural-leaning sites and about 0.60 at urban ones.
- Jechow & Hölker 2019, J. Imaging 5:69
  (https://doi.org/10.3390/jimaging5080069) [M, few nights]:
  - overcast plus snow was ×33 against overcast without snow, and ×188
    against clear without snow;
  - **no clear-sky-with-snow measurement**.
- Levin 2017 (cited above) shows snow also inflates the *input* radiance.
- **Sketch** [E, reproduces Falchi's range]:
  - M_snow = 1 + c·f_r·(A_snow/A_0 − 1)·1.1.
  - c: fraction of the source region under snow.
  - f_r: the share of glow from reflected light, 0.06–0.1 rural and 0.3–0.4
    urban.
  - A_snow ≈ 0.8; A_0 ≈ 0.15.
  - Defaults: about ×1.3 far from sources, about ×2 suburban.
- **Data.**
  - Open-Meteo `snow_depth` (m) in the existing weather call, keyless. Sample
    it at the site and at the 2–3 brightest source cells in the site's kernel.
  - Or NSIDC SNODAS G02158 (1 km, daily, CONUS, HTTPS; citation requested,
    https://nsidc.org/data/g02158/versions/1). That would mean a nightly 1 km
    raster, about 30 MB/day compressed [E], too heavy for a t3.micro. Prefer
    Open-Meteo point queries.
- **Validation.** TESS or GaN-MN winter series in snowy places; the USU
  campus SQM 2013–15 thesis (https://digitalcommons.usu.edu/honors/565)
  reports seasonal snow effects; your own Utah County readings before and
  after snow.

### 4.5 Terrain shielding (part of M4)

- Theory: Garstang 1989, transcribed in Cinzano 2000, eq. 22. A ridge shields
  the atmosphere below a "shadow height" above the site, and the brightness
  ratio is the unshadowed fraction of the molecular and aerosol scattering
  integrals.
- Cinzano & Elvidge 2004 compute h_s per scattering point by walking the DEM.
  ILLUMINA ray-traces the DEM [Mod].
- Magnitude:
  - Sunrise Rock: a few hundredths [Mod];
  - Mont-Mégantic: ×2.5 *brighter* than the flat convolution [Mod+M];
  - Catalonia: up to 0.4 mag differences against Falchi, mixed causes [M];
  - Tibble Fork: about 0.4–1.5 mag darker [L/E, toy].
- **No general rule exists.** Simoneau 2021 says one is needed.
- **Cheap implementation (per site, on demand):**
  1. On the server, keep the lights raster (`lights_2025.tif` at 30″, about
     20 MB) and a DEM (30″ int16 lower 48, about 44 MB uncompressed, less
     compressed) [E]. Within 380 km of a site that is about 0.6 M cells.
  2. Compute the horizon profile from the site along 72 azimuths × about 400
     distance steps, about 30k DEM samples.
  3. For each lit cell, get h_min from the running-maximum formula in section
     2.6 along its azimuth.
  4. Multiply its kernel weight by U(h_min, d) from a precomputed 2D table of
     the unshadowed single-scatter fraction (molecular plus haze, about 40 × 40
     floats).
  5. Sum. In vectorized numpy this should take tens of milliseconds [E]. Cache
     the result per site; sites are few.
- **Calibrate** U so that h_min = 0 reproduces the existing kernel exactly.
  The correction is then purely relative, and flat sites are unchanged.
- **Validate** before trusting it. Take paired SQM readings at Tibble Fork and
  at a ridge-top or open site at similar distance, such as Alpine or the
  Cascade Springs overlook, on the same night. Terrain is where the
  literature disagrees most.

### 4.6 Direction dependence and light domes (M4, A3)

- Measured all-sky to zenith luminance (ALR/ZLR) is 2.0–6.0, highest at the
  darkest sites [M] (Duriscoe 2018).
- Falchi's thesis, 28+ observatories: the 0–10° horizon band averages about
  31× zenith (range 10–126); the hemisphere mean is about 4.9× [Mod].
- Walker's law: glow ∝ P·d^(−2.5) at 45° toward the city [M] (Walker 1977,
  PASP 89:405).
- So a single zenith number understates what an observer sees toward a city
  by 1–4 mag near the horizon.

**Implementation.** Reuse the per-site sum from section 4.5:

1. Bin lit power, already shadow-weighted, into a polar histogram
   P[ring, azimuth]: 17 rings × 36 sectors.
2. Precompute offline, once, an angular kernel
   K[ring, altitude, Δazimuth] by Garstang single-scatter line-of-sight
   integration. Altitudes 5–90° in about 10 steps; Δazimuth in 36 steps. That
   is about 6,000 floats.
3. Normalize K so that the zenith row reproduces the fitted ring weights.
4. Brightness at (altitude, azimuth) = Σ P·K, a few thousand multiply-adds
   per site.
5. Feed it to `engine/targets.py` per target, instead of one zenith SQM, and
   use the same grid for an SVG light-dome rose in the UI.
6. Add natural-sky zenith-angle terms (airglow van Rhijn, extinction) to the
   natural part.

Validation:
- Compare with NPS ALR from IRMA reports, where Duriscoe's ALR formula gives
  a public-domain cross-check.
- Compare with the Falchi thesis ratios.
- Kocifaj & Bará 2019 give an analytic all-sky shape for a sanity check.

A map-wide per-sector raster is the alternative. Eight sectors × the current
FFT pipeline is about 8× the 2-minute apply, about 88 MB uncompressed per
float32 sector map [E]. It is unnecessary when the per-site sum is this
cheap.

### 4.7 Clouds (A4)

- Kyba et al. 2011, PLoS One (https://doi.org/10.1371/journal.pone.0017307)
  [M]: clouds amplify zenith luminance ×10 in Berlin and ×2.8 at 32 km.
- Kyba 2015 [M]: overcast ranges from about a third darker to ×18 brighter.
  The inflection is at a clear-sky brightness of 2–4× natural, about SQM
  20.1–20.9.
- Posch 2018 [M]: up to ×15 urban; down to −1 mag remote.
- Astrolabe already scores cloudy nights low. This matters only for
  "partly cloudy, is it worth going". Low priority.

### 4.8 From sky brightness to what an observer sees (Q4)

Current `naked_eye_limiting_mag` is linear: 7.8 at SQM 21.9 and 4.3 at 18.0.

| SQM | Current linear | Schaefer "7.93" (Birriel 2014 eq. 1) | Crumey 2014, F = 2 (typical) | Crumey, F = 1.4 (expert) |
|---|---|---|---|---|
| 18.0 | 4.30 | 3.97 | 4.91 | 5.29 |
| 20.0 | 6.09 | 5.49 | 5.47 | 5.85 |
| 21.0 | 6.99 | 6.12 | 5.85 | 6.24 |
| 21.5 | 7.44 | 6.38 | 6.04 | 6.43 |
| 22.0 | 7.88 | 6.62 | 6.23 | 6.62 |

Sources:
- Crumey 2014, MNRAS 442:2600 (https://arxiv.org/abs/1405.4209):
  - m0 = 0.27µ + 0.8 − 2.5 log F for 18 ≤ µ ≤ 20 (eq. 90);
  - 0.3834µ − 1.44 − 2.5 log F for 20 < µ < 22 (eq. 54);
  - 0.426µ − 2.365 − 2.5 log F for 21 < µ < 25 (eq. 55).
- Extended objects, eq. 57: µ∞ = 0.6864µ + 9.9325 − 2.5 log F, for
  18 < µ < 22. A 1 mag brighter sky costs about 0.69 mag of galaxy surface
  brightness but only about 0.38 mag of stellar limit.
- Telescope limit, eq. 69 (given in full in the source).
- Field cross-check: NPS observers report NELM 6.3–7.1 at SQM 21.4–21.7 [G].
- The "7.93" formula: NELM = 7.93 − 5·log10(10^(4.316 − SQM/5) + 1),
  attributed in Birriel, Walker & Thornsberry 2014, JAAVSO 42:219
  (https://apps.aavso.org/media/jaavso/2976.pdf) to Schaefer 1990 PASP
  102:212.

**Recommendation.** Use Crumey with a user-selectable F: 1.4 expert, 2
default, 2.4 casual. Use eq. 57 for galaxy and nebula scoring in
`engine/targets.py`. The current formula is about 1.1–1.7 mag too optimistic
at Bortle 1–3 against Crumey F = 2. The telescopic limit inherits that error.

---

## 5. Ground truth and calibration (Q2, M5)

### 5.1 Sources

| Source | What | Licence and access | US / Utah coverage |
|---|---|---|---|
| **NPS Night Skies Data Reports** | CCD V zenith (1 deg²), ALR, NELM, Bortle, *paired handheld SQM* | Public domain. Keyless IRMA REST: `https://irmaservices.nps.gov/datastore/v7/rest/QuickSearch?q=...` and `/Reference/{id}/DigitalFiles` | About 100 parks measured since 2001; at least 16 public PDFs found. Dinosaur NM (UT/CO): CCD 21.99–22.09, SQM 21.40–21.60. SE Utah 2003: Canyonlands about 21.8, Natural Bridges about 21.7, Arches and Hovenweep about 21.4 V. Craters of the Moon annual SQM xlsx (IRMA 2266678). |
| **STARS4ALL TESS-W** | Fixed photometers, every minute | CC BY 4.0, monthly CSVs on Zenodo (example https://zenodo.org/records/4265155); `tess-ida-tools` | More than 250 units, mostly Europe. US: Tucson (stars19) and Oracle SP (stars227). None found in Utah. |
| **GaN Monitoring Network (GaN-MN)** | Fixed SQM-LE stations, monthly CSVs, about 6 months' lag | CC BY 4.0 (GaN) | Not enumerated; worth checking for Western stations |
| **Globe at Night handheld** | Citizen SQM and naked-eye observations | CC BY 4.0 | Thousands of US rows; Utah County: none usable [L] |
| **IDA / DarkSky applications** | Campaign SQM | PDFs, informal | North Fork Park 20.94 (2013–14); Antelope Island 20.67 average (2016) |
| **Falchi Table 3 calibration sets** | — | Not redistributed | — |
| **Your own readings** | SQM-L with a logged serial number and protocol | — | The only way to get Utah County data |

Research-grade sources hold no Utah County, Tibble Fork, BYU or University of
Utah Consortium data that I could find.

### 5.2 Calibration recipe

This follows Falchi 2016's published method; [L] above shows it working on our
GaN data.

1. **Filter.**
   - Sun below −18° and Moon below 0° (−5° is safer), computed from UT.
   - CloudCover "clear".
   - SQM between 16 and 22.3.
   - Reject 5-decimal values; or flag them and test both ways.
   - Optional: reject rows whose GaN chart magnitude and SQM disagree by more
     than 2 mag under the "7.93" formula (Birriel 2014).
2. **De-duplicate.** Weight each row 1/(rows at its 30″ cell), as Falchi did.
3. **Model in linear radiance:**
   `B_obs = s·N(F10.7, b, β, λ−λsun) + a·B_map·(1 + d·h)·M_snow·M_aod`,
   with **per-dataset s (and optionally a)**, so GaN's bias is a nuisance
   parameter and never the truth.
4. **Likelihood.** Robust: soft-L1 or Huber with a 0.3 mag scale, or Falchi's
   80% Gaussian plus 20% outlier mixture, on magnitude residuals.
5. **Anchor the absolute level on research-grade data.** That means NPS CCD
   and TESS, with the SQM band handled (V = SQM − 0.17). Use GaN only for the
   *shape*: d, band balance, snow, AOD.
6. **Validate.**
   - Spatial block hold-out: leave one region out, as the README already does
     for the atlas fit.
   - Hold out NPS sites.
   - Report the median residual and MAD by predicted SQM band and by hour.
7. **Ship.** The fit outputs `{s_SQM, a, d}` as constants beside the kernel
   JSON. The engine applies them at query time with the night's F10.7, hour,
   snow and AOD. The raster stays "artificial at about 01:30, satellite
   epoch", so it does not have to be rebuilt for calibration changes.

**Accuracy to expect.**
- Falchi's own fit reaches σ = 0.15 mag on curated data [M].
- ILLUMINA reaches 0.14–0.20 [M].
- Our GaN noise floor is 0.29 [L].
- A realistic target for Astrolabe after Q1–Q3 and M5: **median bias under
  0.1 and scatter about 0.2–0.3 mag** at a given site and time. Terrain and
  inversion cases will stay worse until locally validated.

---

## 6. Cost on a t3.micro

All figures here are my estimates [E].

| Item | Storage | Run time | Network |
|---|---|---|---|
| Natural sky (Q1) | under 50 kB of tables | µs | F10.7 JSON once a day, a few kB |
| Time, snow and AOD terms (Q3, M1, M2) | none | µs | Add `snow_depth` to the existing Open-Meteo call; one Air Quality call per site-night |
| Per-site direction and terrain (M4) | lights 30″ (about 20 MB) + DEM int16 (about 44 MB) | tens of ms per site, cached | none |
| Map-wide per-sector rasters (alternative) | about 88 MB per sector uncompressed | offline, about 8× the apply time | none |
| Black Marble refit (A1) | 0.5–1 GB at build time only | offline | manual Earthdata download |

The t3.micro has 1 GB of RAM. A per-site window of 760 × 760 cells × a few
arrays is about 20 MB, which fits.

---

## 7. Open questions and honest uncertainty

- **Is the artificial factor a ≈ 1.6 real?** GaN cannot separate LED growth
  from glare bias that scales with light. Answering it needs 2023+
  research-grade readings in lit US places. Even 10–20 readings from your own
  calibrated SQM would help.
- **High-elevation cities.** Physics and the atlas's sea-level-source
  convention point opposite ways. There is no ground data for SLC, Provo or
  Denver.
- **Terrain.** Literature results range from 0.03 mag to ×2.5 in either
  direction. My Tibble Fork number is a toy. Validate locally.
- **Inversions and humidity.** No quantitative skyglow study was found. CAMS
  AOD at 45 km cannot resolve the Salt Lake Valley's cold pool.
- **Not found:**
  - a US evening-to-01:30 brightness ratio;
  - a clear-sky snow multiplier with snow at the *observer only*;
  - Wallner & Kocifaj 2019 albedo coefficients;
  - a published Lorenz method;
  - a validated Black Marble against VNL comparison for skyglow;
  - which Open-Meteo US models provide `boundary_layer_height`.

---

## 8. Reference list (primary sources)

- Alarcon et al. 2021, AJ 162:25, https://arxiv.org/abs/2105.01066
- Aubé et al. 2020 (ILLUMINA v2), MNRAS, https://arxiv.org/abs/2005.14160; code https://github.com/aubema/illumina
- Bará et al. 2020, JQSRT 240:106658, https://arxiv.org/abs/1907.02891
- Bará & Castro-Torres 2025, JQSRT 335:109378, https://arxiv.org/abs/2409.13111
- Barentine et al. 2018 (Tucson LED), https://arxiv.org/abs/1802.03474; 2020, https://arxiv.org/abs/2005.12357
- Birriel, Walker & Thornsberry 2014, JAAVSO 42:219, https://apps.aavso.org/media/jaavso/2976.pdf
- Cinzano 2005, SQM report, https://www.unihedron.com/projects/darksky/cd/reference/sqmreport_v1p4.pdf
- Cinzano et al. 2000, MNRAS 318:641, https://arxiv.org/abs/astro-ph/0003412
- Cinzano & Elvidge 2004, MNRAS 353:1107, https://arxiv.org/abs/astro-ph/0406610
- Cinzano & Falchi 2012, MNRAS 427:3337, https://arxiv.org/abs/1209.2031
- Crumey 2014, MNRAS 442:2600, https://arxiv.org/abs/1405.4209
- Duriscoe et al. 2018, JQSRT 214:133, https://repository.library.noaa.gov/view/noaa/21007
- Elvidge et al. 2021 (VNL v2), Remote Sens. 13:922, https://doi.org/10.3390/rs13050922
- Falchi 2011, MNRAS 412:33, https://doi.org/10.1111/j.1365-2966.2010.17845.x
- Falchi et al. 2016, Sci. Adv. 2:e1600377, https://doi.org/10.1126/sciadv.1600377 (full text https://pmc.ncbi.nlm.nih.gov/articles/PMC4928945/)
- Falchi 2024 PhD thesis, https://minerva.usc.gal/entities/publication/e8145fdd-34a1-4d58-940a-cbeb72c759de/full
- Hänel et al. 2018, JQSRT 205:278, https://arxiv.org/abs/1709.09558
- Jechow & Hölker 2019, J. Imaging 5:69, https://doi.org/10.3390/jimaging5080069
- Kocifaj & Barentine 2021, Sci. Rep. 11:14622, https://doi.org/10.1038/s41598-021-94241-1
- Krisciunas 1997, https://arxiv.org/abs/astro-ph/9706111; Krisciunas et al. 2007, https://arxiv.org/abs/0705.1153
- Kyba et al. 2011, PLoS One, https://doi.org/10.1371/journal.pone.0017307; 2013, Sci. Rep. 3:1835, https://pmc.ncbi.nlm.nih.gov/articles/PMC3655480; 2015, Sci. Rep. 5:8409, https://doi.org/10.1038/srep08409; 2023, Science 379:265, https://doi.org/10.1126/science.abq7781
- Leinert et al. 1998, A&AS 127:1, https://doi.org/10.1051/aas:1998105
- Levin 2017, RSE, https://doi.org/10.1016/j.rse.2017.03.003
- Linares et al. 2023, JQSRT 309:108678, https://arxiv.org/abs/2403.00112
- Masana et al. 2021, MNRAS 501:5443, https://doi.org/10.1093/mnras/staa4005
- Noll et al. 2012, A&A 543:A92, https://arxiv.org/abs/1205.2003
- Posch, Binder & Puschnig 2018, JQSRT 211:144, https://arxiv.org/abs/1803.09811
- Román et al. 2018 (Black Marble), RSE 210:113, https://doi.org/10.1016/j.rse.2018.03.017
- Sánchez de Miguel et al. 2017, MNRAS 467:2966, https://arxiv.org/abs/1701.05019
- Ściężor & Kubala 2014, MNRAS 444:2487, https://doi.org/10.1093/mnras/stu1577
- Simoneau et al. 2021, MNRAS, https://arxiv.org/abs/2103.03341
- Data and APIs:
  - NOAA SWPC F10.7: https://services.swpc.noaa.gov/json/f107_cm_flux.json
  - Open-Meteo Air Quality: https://open-meteo.com/en/docs/air-quality-api
  - NSIDC SNODAS: https://nsidc.org/data/g02158/versions/1
  - LAADS VNP46A4: https://ladsweb.modaps.eosdis.nasa.gov/missions-and-measurements/products/VNP46A4
  - EOG VNL: https://eogdata.mines.edu/products/vnl/
  - GaN data: https://globeatnight.org/maps-data/
  - NPS night-sky sites: https://www.nps.gov/subjects/nightskies/datacollectionsites.htm
- Utah: North Fork Park IDSP application, https://darksky.org/app/uploads/2015/04/North_Fork_Park_IDSP_application.pdf; Antelope Island application, https://darksky.org/app/uploads/2017/04/AntelopeIsland_ISDP_application.pdf

Some secondary details were gathered by research sub-agents and not
re-verified line by line:
- the Falchi thesis's sea-level-source statement;
- EOG's June 2026 access change;
- the Kyba 2023 North America rate.

The Falchi 2016 fit values, Table 3 residuals, the altitude treatment and the
22.0 solar-minimum natural sky were re-checked against the paper's full text.
