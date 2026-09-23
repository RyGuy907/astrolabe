"""Build a current sky-brightness map from satellite night lights.

The satellite sees light leaving the ground; an observer sees that light
scattered back down by the atmosphere from everything for a few hundred
kilometres around. Turning one into the other takes a propagation model, and
this script *learns* that model instead of assuming one:

1. **crop** -- cut the United States (plus the neighbours whose glow reaches
   it) out of three global files and put them on one 30-arcsecond grid:
   EOG's annual VIIRS night lights for 2014 and for the latest year, and
   Falchi et al.'s World Atlas, which was computed from 2014 VIIRS data.
2. **fit** -- the atlas is, to a good approximation, the 2014 lights
   convolved with a radial kernel: brightness at a site is a sum over rings
   of distance of (light in that ring) x (how much a ring that far away
   contributes), less the higher the site stands, because there is less air
   above it to scatter light back down. That is linear in the per-ring
   weights, so the weights come from a non-negative least-squares fit of
   2014 lights to the atlas.
3. **apply** -- convolve the latest year's lights with the fitted kernel.

Ground height comes from NOAA's ETOPO 2022 (public domain).

The atlas is used only as a calibration target here and is never
redistributed; its licence forbids that. See THIRD_PARTY_NOTICES.md.

Run the stages in order; each writes into data/skyglow/work/:

    python scripts/build_skyglow.py crop
    python scripts/build_skyglow.py fit      # needs the atlas; writes the kernel
    python scripts/build_skyglow.py apply    # needs only the kernel
    python scripts/build_skyglow.py tiles    # the site map's overlay
    python scripts/build_skyglow.py install  # map and tiles into config/
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window
from scipy import fft, optimize
from scipy.ndimage import map_coordinates

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "skyglow"
WORK = DATA / "work"
# The fitted kernel is committed: a few dozen numbers, not the atlas, and with
# it `apply` needs only the free satellite data -- the atlas is needed only to
# fit it again.
KERNEL = Path(__file__).resolve().with_name("skyglow_kernel.json")

ATLAS = "/vsizip/" + (DATA / "World_Atlas_2015.zip").as_posix() + "/World_Atlas_2015.tif"
# EOG's annual composites, "median_masked": fires, gas flares and background
# removed, and one freak month cannot skew a year. 2014 is the atlas's year,
# so the pair calibrates the model; the latest is the one that ships. Both
# are from the same satellite (Suomi NPP), and v2.1 is the reprocessing of
# the early years that v2.2 continues.
LIGHTS = {
    "2014": "VNL_v21_npp_2014_global_vcmslcfg_c202205302300.median_masked.dat.tif.gz",
    "2025": "VNL_npp_2025_global_vcmslcfg_v2_c202604011200.median_masked.dat.tif.gz",
}
LATEST = max(LIGHTS)

# The region worked on: the lower 48 with about 300 km to spare on every side,
# so a site near the border still sees the glow of Vancouver, Toronto,
# Montreal, Tijuana and Monterrey.
WEST, EAST, SOUTH, NORTH = -128.0, -63.0, 21.0, 53.0

# The atlas grid: cells 1/120 degree square. Its column edges fall on whole
# multiples of 1/120 of a degree and its row edges halfway between, so every
# cell centre is also the centre of a VIIRS cell (1/240 of a degree).
CELL = 1 / 120


def _grid():
    """The working grid's shape and transform: atlas cells, cropped."""
    cols = round((EAST - WEST) / CELL)
    rows = round((NORTH - SOUTH) / CELL)
    transform = rasterio.Affine(CELL, 0, WEST, 0, -CELL, NORTH + CELL / 2)
    return rows, cols, transform


def _save(path: Path, array: np.ndarray, transform) -> None:
    profile = dict(driver="GTiff", width=array.shape[1], height=array.shape[0],
                   count=1, dtype="float32", crs="EPSG:4326", transform=transform,
                   compress="deflate", predictor=3, tiled=True)
    with rasterio.open(path, "w", **profile) as out:
        out.write(array.astype(np.float32), 1)


def _window_for(dataset, transform) -> Window:
    """The window of `dataset` whose top-left cell is the grid's top-left."""
    rows, cols, _ = _grid()
    col, row = ~dataset.transform * (transform.c, transform.f)
    assert abs(col - round(col)) < 0.05 and abs(row - round(row)) < 0.05, (col, row)
    return Window(round(col), round(row), cols, rows)


def crop_atlas() -> None:
    rows, cols, transform = _grid()
    with rasterio.open(ATLAS) as src:
        window = _window_for(src, transform)
        # The file gives the cell size to eight decimals, 0.00833333.
        assert abs(src.transform.a - CELL) < 1e-8
        data = src.read(1, window=window)
    data[~np.isfinite(data) | (data < 0)] = 0.0
    _save(WORK / "atlas.tif", data, transform)
    print(f"atlas: {data.shape}, max {data.max():.1f} mcd/m2")


def crop_lights(year: str) -> None:
    """Night lights on the atlas grid.

    VIIRS cells are 1/240 degree and one sits on each atlas cell's centre, so
    each atlas cell holds that one whole, four halves and four quarters: the
    separable weights [1/4, 1/2, 1/4] along each axis give its mean radiance
    exactly, where a plain 2x2 average would shift the map by a quarter of a
    kilometre.
    """
    rows, cols, transform = _grid()
    with rasterio.open("/vsigzip/" + (DATA / LIGHTS[year]).as_posix()) as src:
        # Centre of the grid's first cell, in VIIRS pixel coordinates, then one
        # VIIRS cell before it so the three-cell stencil has its left column.
        cx, cy = ~src.transform * (WEST + CELL / 2, NORTH)
        assert abs(cx % 1 - 0.5) < 0.05 and abs(cy % 1 - 0.5) < 0.05, (cx, cy)
        c0, r0 = int(cx) - 1, int(cy) - 1
        window = Window(c0, r0, 2 * cols + 1, 2 * rows + 1)
        fine = src.read(1, window=window).astype(np.float64)
    fine[~np.isfinite(fine) | (fine < 0)] = 0.0
    across = 0.25 * fine[:, 0:-2:2] + 0.5 * fine[:, 1:-1:2] + 0.25 * fine[:, 2::2]
    coarse = 0.25 * across[0:-2:2] + 0.5 * across[1:-1:2] + 0.25 * across[2::2]
    assert coarse.shape == (rows, cols), coarse.shape
    _save(WORK / f"lights_{year}.tif", coarse, transform)
    print(f"lights {year}: {coarse.shape}, total {coarse.sum():.3g}")


# NOAA's ETOPO 2022 relief model: public domain, 30 arcseconds, one global
# file stored in compressed 256-cell blocks -- so reading the region over HTTP
# fetches about 50 MB of its 1.5 GB.
ELEVATION = ("/vsicurl/https://www.ngdc.noaa.gov/mgg/global/relief/ETOPO2022/data/30s/"
             "30s_surface_elev_gtif/ETOPO_2022_v1_30s_N90W180_surface.tif")


def crop_elevation() -> None:
    """Ground height in km on the working grid.

    ETOPO's cells share the working grid's columns, but its rows are offset
    by half a cell, so each working cell is the mean of the two ETOPO rows it
    straddles. The sea is height zero: ETOPO's "surface" is the sea floor
    offshore, and nowhere in the region is dry land below -100 m.
    """
    rows, cols, transform = _grid()
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"), rasterio.open(ELEVATION) as src:
        col, row = ~src.transform * (WEST, NORTH + CELL / 2)
        assert abs(col - round(col)) < 0.01 and abs(row % 1 - 0.5) < 0.01, (col, row)
        window = Window(round(col), int(row), cols, rows + 1)
        metres = src.read(1, window=window).astype(np.float64)
    metres[~np.isfinite(metres) | (metres < -100)] = 0.0
    km = (metres[:-1] + metres[1:]) / 2000
    _save(WORK / "elevation.tif", km, transform)
    print(f"elevation: {km.shape}, {km.min():.2f} to {km.max():.2f} km")


def crop() -> None:
    """Each source once; a file already cut is kept, so a rerun only does
    what is missing."""
    WORK.mkdir(parents=True, exist_ok=True)
    steps = [("atlas.tif", crop_atlas), ("elevation.tif", crop_elevation),
             *[(f"lights_{y}.tif", lambda y=y: crop_lights(y)) for y in LIGHTS]]
    for name, run in steps:
        if (WORK / name).exists():
            print(f"  {name} already cut")
            continue
        if name == "atlas.tif" and not (DATA / "World_Atlas_2015.zip").exists():
            print("  no atlas: fine for apply, which uses the committed kernel; needed only to fit")
            continue
        t = time.time()
        run()
        print(f"  {name} took {time.time() - t:.0f} s", flush=True)


# --- the propagation model ------------------------------------------------

EARTH_KM = 6371.0
# Rings of distance the kernel is fitted over, in km. Narrow near the site,
# where a kilometre makes a large difference, and wide far out, where the
# glow changes slowly. The outer edge is deliberately generous, so the fit
# rather than this list decides where the contribution runs out.
RINGS_KM = [0, 0.75, 1.5, 2.5, 4, 6, 9, 13, 18, 25, 35, 50, 70, 100, 140, 200, 280, 380]
PLANE_KM = 1.0  # cell size of the flat grid the convolution runs on

# Scale heights of the two things in the air that scatter light back down, in
# km, from Garstang (1986, PASP 98, 364): the air's molecules, which thin out
# slowly with height, and haze and dust, which are mostly in the lowest
# couple of kilometres. A site 1.6 km up, like Denver, has a sixth less air
# above it than one at sea level but only about a third of the haze, and its
# sky is correspondingly darker for the same lights. Each ring's weight is
# fitted separately for each: how much of the glow from that distance is
# scattered by molecules and how much by haze is for the atlas to say.
LAYERS_KM = (9.62, 1.52)

# Sites whose glow is compared with the atlas: the lower 48, kept far enough
# inside the cropped region that nearly all of their surroundings are in it.
FIT_WEST, FIT_EAST, FIT_SOUTH, FIT_NORTH = -124.0, -67.5, 25.0, 49.0
# What ships: the whole of the lower 48, coast to coast. Whole degrees, so
# the edges fall on the grid's cell edges.
OUT_WEST, OUT_EAST, OUT_SOUTH, OUT_NORTH = -125.0, -66.0, 24.0, 50.0

# The natural night sky in the atlas's units, and its SQM conversion; the same
# numbers as engine/skybrightness.py.
NATURAL = 0.171168465

# Artificial light in the shipped map is the model's times this. The model
# reproduces the atlas, and the atlas is built on what the satellite sees --
# which is little of the blue in white LED light, so a satellite map runs dark
# wherever streets have gone LED, and more so every year (Kyba et al. 2023,
# Science 379, 265: skies brightening several times faster to people on the
# ground than to the satellite). 1.55 is fitted to ground readings: 519 Globe
# at Night sky-meter sites, 2024-25, clear, moonless and fully dark, fitting
# measured = natural + k x model. It errs toward a brighter sky than the atlas,
# which is the safe side for planning a drive: at the research-grade sites
# (mostly Tucson, 2017) it reads about 0.4 mag bright.
GROUND_CALIBRATION = 1.55


def sqm(artificial: np.ndarray) -> np.ndarray:
    return np.log10((artificial + NATURAL) / 1.08e8) / -0.4


def albers(lon: np.ndarray, lat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Equal-area conic coordinates in km, on the sphere.

    The standard projection for the lower 48 (the parallels of EPSG:5070):
    equal area, so a cell's light lands in the plane undiluted, and within
    about 1% on distances across the region, so a ring 100 km wide on the
    plane is about 100 km wide on the ground.
    """
    p1, p2, p0, l0 = np.radians([29.5, 45.5, 23.0, -96.0])
    n = (np.sin(p1) + np.sin(p2)) / 2
    c = np.cos(p1) ** 2 + 2 * n * np.sin(p1)
    rho0 = EARTH_KM * np.sqrt(c - 2 * n * np.sin(p0)) / n
    rho = EARTH_KM * np.sqrt(c - 2 * n * np.sin(np.radians(lat))) / n
    theta = n * (np.radians(lon) - l0)
    return rho * np.sin(theta), rho0 - rho * np.cos(theta)


def _centres():
    """Longitude and latitude of every working-grid cell centre."""
    rows, cols, transform = _grid()
    lon = transform.c + (np.arange(cols) + 0.5) * transform.a
    lat = transform.f + (np.arange(rows) + 0.5) * transform.e
    return lon, lat


class Plane:
    """The working grid's cells placed on a flat 1 km grid.

    Each cell's light is dropped, whole, into the plane cell its centre falls
    in, so the plane holds exactly the light the satellite saw; the glow is
    then read back at each site by interpolating between plane cells.
    """

    def __init__(self) -> None:
        lon, lat = _centres()
        self.lon, self.lat = lon, lat
        x, y = albers(*np.meshgrid(lon, lat))
        self.x0, self.y1 = x.min() - PLANE_KM, y.max() + PLANE_KM
        self.shape = (int((self.y1 - y.min()) / PLANE_KM) + 2,
                      int((x.max() - self.x0) / PLANE_KM) + 2)
        # Plane coordinates of every cell centre, as fractional (row, col).
        self.row = ((self.y1 - y) / PLANE_KM).astype(np.float32)
        self.col = ((x - self.x0) / PLANE_KM).astype(np.float32)
        self.index = (np.floor(self.row).astype(np.int64) * self.shape[1]
                      + np.floor(self.col).astype(np.int64))
        # Ground area of each cell in km2: the satellite's map is radiance,
        # so a cell's emitted power is radiance x area.
        cell = np.radians(CELL)
        self.area = (EARTH_KM * cell) ** 2 * np.cos(np.radians(lat))[:, None]

    def power(self, lights: np.ndarray) -> np.ndarray:
        flat = np.bincount(self.index.ravel(), (lights * self.area).ravel(),
                           minlength=self.shape[0] * self.shape[1])
        return flat.reshape(self.shape)

    def at(self, field: np.ndarray, rows=None, cols=None) -> np.ndarray:
        """`field` read at cell centres (every one, or the ones given)."""
        r = self.row if rows is None else rows
        c = self.col if cols is None else cols
        # The plane cell [i, i+1) has its centre at i + 0.5.
        return map_coordinates(field, [r.ravel() - 0.5, c.ravel() - 0.5],
                               order=1, mode="constant").reshape(r.shape)


def ring_kernels(edges=RINGS_KM, step=PLANE_KM, sub=6) -> np.ndarray:
    """For each ring, how much of each plane cell around a site lies in it.

    Stacked (ring, row, col) with the site at the centre, in km2. Each cell
    is sampled `sub` x `sub` times, so the inner rings, only a few cells
    across, get their share of partly covered cells right.
    """
    radius = int(np.ceil(edges[-1] / step))
    offsets = (np.arange(sub) + 0.5) / sub - 0.5
    grid = np.arange(-radius, radius + 1) * step
    fine = (grid[:, None] + offsets[None, :] * step).ravel()
    which = np.digitize(np.hypot(fine[:, None], fine[None, :]), edges) - 1
    n = 2 * radius + 1
    kernels = np.zeros((len(edges) - 1, n, n))
    for k in range(len(edges) - 1):
        kernels[k] = (which == k).reshape(n, sub, n, sub).mean(axis=(1, 3)) * step * step
    return kernels


def convolve_all(power: np.ndarray, kernels: np.ndarray):
    """Yield `power` convolved with each kernel in turn, on the plane.

    In double precision: a dark site's glow is a few millionths of a city's,
    and single-precision FFT round-off from the cities would swamp it.
    """
    radius = kernels.shape[1] // 2
    shape = [fft.next_fast_len(s + 2 * radius, real=True) for s in power.shape]
    spectrum = fft.rfft2(power, shape, workers=-1)
    for kernel in kernels:
        full = fft.irfft2(spectrum * fft.rfft2(kernel, shape, workers=-1), shape, workers=-1)
        yield full[radius:radius + power.shape[0], radius:radius + power.shape[1]]


def _read(name: str) -> np.ndarray:
    with rasterio.open(WORK / name) as src:
        return src.read(1).astype(np.float64)


def _samples(atlas: np.ndarray, plane: Plane, per_band=25_000, seed=1):
    """Cells to compare: spread evenly over brightness, and at random.

    By area the lower 48 is mostly dark country, and a plain random sample
    would fit the kernel to that and little else. The fit takes the same
    number of cells from each band of brightness, which weighs a suburb as
    heavily as a wilderness; the random sample reports errors as a traveller
    across the map would meet them.
    """
    rng = np.random.default_rng(seed)
    inside = ((plane.lon[None, :] >= FIT_WEST) & (plane.lon[None, :] <= FIT_EAST)
              & (plane.lat[:, None] >= FIT_SOUTH) & (plane.lat[:, None] <= FIT_NORTH))
    band = np.digitize(np.log10(np.maximum(atlas, 1e-4)), np.arange(-3, 2, 0.4))
    chosen = []
    for b in np.unique(band[inside]):
        cells = np.flatnonzero(inside & (band == b))
        chosen.append(rng.choice(cells, min(per_band, cells.size), replace=False))
    return np.concatenate(chosen), rng.choice(np.flatnonzero(inside), 100_000, replace=False)


def _report(label: str, truth: np.ndarray, model: np.ndarray) -> dict:
    t, m = sqm(truth), sqm(model)
    err = m - t
    out = {"cells": int(t.size), "bias_mag": float(np.median(err)),
           "median_abs_mag": float(np.median(np.abs(err))),
           "p90_abs_mag": float(np.percentile(np.abs(err), 90))}
    print(f"  {label:<22} n={t.size:>7}  bias {out['bias_mag']:+.3f}  "
          f"median |err| {out['median_abs_mag']:.3f}  90% within {out['p90_abs_mag']:.3f} mag")
    by_class = {}
    for lo, hi in [(0, 18), (18, 19), (19, 20), (20, 21), (21, 21.5), (21.5, 22.1)]:
        sel = (t >= lo) & (t < hi)
        if sel.sum() < 50:
            continue
        e = err[sel]
        by_class[f"{lo}-{hi}"] = [float(np.median(e)), float(np.median(np.abs(e)))]
        print(f"      SQM {lo:>4}-{hi:<4} n={sel.sum():>7}  bias {np.median(e):+.3f}  "
              f"median |err| {np.median(np.abs(e)):.3f}")
    out["by_class"] = by_class
    return out


def fit() -> None:
    t0 = time.time()
    atlas = _read("atlas.tif")
    lights = _read("lights_2014.tif")
    height = _read("elevation.tif")
    plane = Plane()
    print(f"plane {plane.shape}, {time.time() - t0:.0f} s", flush=True)

    stratified, random = _samples(atlas, plane)
    picks = np.concatenate([stratified, random])
    rows, cols = plane.row.ravel()[picks], plane.col.ravel()[picks]
    columns = []
    for k, glow in enumerate(convolve_all(plane.power(lights), ring_kernels())):
        columns.append(plane.at(glow, rows, cols))
        print(f"  ring {RINGS_KM[k]:>5}-{RINGS_KM[k + 1]:<5} km  {time.time() - t0:.0f} s", flush=True)
    rings = np.stack(columns, axis=1)
    h = height.ravel()[picks]
    # One block of ring columns per layer, each dimmed by how much of that
    # layer lies below the site.
    design = np.hstack([rings * np.exp(-h / layer)[:, None] for layer in LAYERS_KM])
    truth = atlas.ravel()[picks]
    lon = np.broadcast_to(plane.lon[None, :], atlas.shape).ravel()[picks]
    is_strat = np.arange(picks.size) < stratified.size
    np.savez(WORK / "fit_samples.npz", rings=rings, height=h, truth=truth, lon=lon,
             is_strat=is_strat, picks=picks)

    def solve(use: np.ndarray) -> np.ndarray:
        # Weighted so a residual counts by its share of the whole sky, which
        # is close to an error in magnitudes -- what an observer notices.
        w = 1.0 / (truth[use] + NATURAL)
        scale = design[use].max(axis=0) + 1e-30
        weights, _ = optimize.nnls(design[use] * w[:, None] / scale, truth[use] * w)
        return weights / scale

    print("hold-out: fitted on one half of the country, tested on the other")
    for name, train in [("fit west, test east", lon < -96), ("fit east, test west", lon >= -96)]:
        test = ~is_strat & ~train
        _report(name, truth[test], design[test] @ solve(is_strat & train))

    weights = solve(is_strat)
    print("fitted on the whole country")
    summary = _report("random cells", truth[~is_strat], design[~is_strat] @ weights)
    _report("by brightness band", truth[is_strat], design[is_strat] @ weights)
    _by_height(truth, design @ weights, h)

    per_layer = weights.reshape(len(LAYERS_KM), -1)
    kernel = {"rings_km": RINGS_KM, "layers_km": list(LAYERS_KM),
              "weight_per_km2": per_layer.tolist(), "fit": summary}
    KERNEL.write_text(json.dumps(kernel, indent=1) + "\n")
    print("  ring km        " + "".join(f"H={layer:<5} km    " for layer in LAYERS_KM) + "total x d^2.5")
    for k in range(len(RINGS_KM) - 1):
        mid = (RINGS_KM[k] + RINGS_KM[k + 1]) / 2
        print(f"  {RINGS_KM[k]:>5}-{RINGS_KM[k + 1]:<5}  "
              + "".join(f"{w:.3e}/km2  " for w in per_layer[:, k])
              + f"{per_layer[:, k].sum() * mid ** 2.5:.3e}")
    print(f"done in {time.time() - t0:.0f} s")


def _by_height(truth: np.ndarray, model: np.ndarray, height: np.ndarray) -> None:
    """Median error by brightness and ground height: the check that a
    mountain town is not being treated like a town at sea level."""
    t, err = sqm(truth), sqm(model) - sqm(truth)
    bands = [(-1, 0.3), (0.3, 0.8), (0.8, 1.2), (1.2, 1.6), (1.6, 2.2), (2.2, 5)]
    print("  median error (mag) by ground height, km:   "
          + "  ".join(f"{a:>3.1f}-{b:<3.1f}" for a, b in bands))
    for lo, hi in [(16, 18.5), (18.5, 19.5), (19.5, 20.5), (20.5, 21.5)]:
        cells = []
        for a, b in bands:
            sel = (t >= lo) & (t < hi) & (height >= a) & (height < b)
            cells.append(f"{np.median(err[sel]):+7.2f}" if sel.sum() > 30 else "     --")
        print(f"      SQM {lo:>4}-{hi:<4}                          " + "  ".join(cells))


def _kernel() -> tuple[list[float], np.ndarray]:
    """The fitted scale heights, and one kernel per layer."""
    fitted = json.loads(KERNEL.read_text())
    weights = np.asarray(fitted["weight_per_km2"])
    return fitted["layers_km"], np.tensordot(weights, ring_kernels(fitted["rings_km"]), axes=1)


def model(year: str, fitted=None) -> np.ndarray:
    """Artificial sky brightness (mcd/m2) on the working grid, for a year."""
    plane = Plane()
    layers, kernels = _kernel() if fitted is None else fitted
    height = _read("elevation.tif")
    total = np.zeros(height.shape)
    for layer, glow in zip(layers, convolve_all(plane.power(_read(f"lights_{year}.tif")), kernels)):
        total += plane.at(glow) * np.exp(-height / layer)
    return np.maximum(total, 0.0)


def _quantised(values: np.ndarray) -> np.ndarray:
    """float32 with the low mantissa bits cleared: still 0.1% precise -- a
    thousandth of a magnitude -- and several times smaller once compressed."""
    bits = values.astype(np.float32).view(np.uint32) & np.uint32(0xFFFFE000)
    return bits.view(np.float32)


def apply() -> None:
    t0 = time.time()
    _, _, transform = _grid()
    fitted = _kernel()
    atlas = _read("atlas.tif")
    then = model("2014", fitted)
    _save(WORK / "model_2014.tif", then, transform)
    now = model(LATEST, fitted)
    _save(WORK / f"model_{LATEST}.tif", now, transform)
    print(f"modelled both years in {time.time() - t0:.0f} s")

    plane_lon, plane_lat = _centres()
    inside = ((plane_lon[None, :] >= FIT_WEST) & (plane_lon[None, :] <= FIT_EAST)
              & (plane_lat[:, None] >= FIT_SOUTH) & (plane_lat[:, None] <= FIT_NORTH))
    print("the 2014 model against the atlas, every cell in the lower-48 box:")
    _report("2014 model", atlas[inside], then[inside])
    _by_height(atlas[inside], then[inside], _read("elevation.tif")[inside])
    change = sqm(now[inside]) - sqm(then[inside])
    print(f"{LATEST} against 2014, same model (negative = brighter now):")
    for q in (5, 25, 50, 75, 95):
        print(f"  {q:>2}th percentile  {np.percentile(change, q):+.3f} mag")

    # Only the lower 48 ships. The margins were there to light it, and near
    # the crop's edge the model is missing whatever lies beyond.
    col0, row0 = (int(round(v)) for v in ~transform * (OUT_WEST, OUT_NORTH + CELL / 2))
    col1, row1 = (int(round(v)) for v in ~transform * (OUT_EAST, OUT_SOUTH + CELL / 2))
    shipped = now[row0:row1, col0:col1] * GROUND_CALIBRATION
    out = OUTPUT
    profile = dict(driver="GTiff", width=shipped.shape[1], height=shipped.shape[0], count=1,
                   dtype="float32", crs="EPSG:4326", nodata=-1.0,
                   transform=transform * rasterio.Affine.translation(col0, row0),
                   compress="deflate", predictor=3, zlevel=9, tiled=True,
                   blockxsize=256, blockysize=256)
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(_quantised(shipped), 1)
        dst.update_tags(
            # Read by engine/skybrightness.py and shown beside a site's class.
            LABEL=f"modelled from {LATEST} satellite data",
            UNITS="mcd/m2, artificial zenith sky brightness",
            SOURCE=f"EOG VIIRS annual VNL, {LATEST}, median_masked (CC BY 4.0)",
            MODEL=("two-layer radial kernel fitted to Falchi et al. 2016 on 2014 VNL v2.1, "
                   "ETOPO 2022 ground height; scripts/build_skyglow.py"),
            CALIBRATION=(f"artificial x{GROUND_CALIBRATION}, fitted to Globe at Night "
                         "2024-25 sky-meter readings"),
        )
    print(f"wrote {out.relative_to(ROOT)} ({out.stat().st_size / 2**20:.1f} MB)")


# --- map tiles --------------------------------------------------------------

OUTPUT = DATA / "skybrightness_us.tif"
TILES = DATA / "skybrightness_us_tiles"
TILE = 256
# At zoom 7 a tile pixel is about one ~1 km cell of the map, so it is as fine
# as the data goes: zoom 8 would only interpolate, at three times the size.
# Past 7 the browser enlarges what it has. Below 3 the country is a smudge.
TILE_ZOOMS = range(3, 8)

# The overlay's colours, by SQM. Clear where the sky is as dark as it gets, so
# the map underneath shows through untouched, then deepening from blue through
# green and yellow to red and white -- the familiar light-pollution-map ramp,
# which people already read without a key. The alpha rises with brightness,
# so faint glow tints the map and a city covers it.
LEGEND = [
    (22.0, (0, 0, 0, 0)),
    (21.8, (20, 30, 90, 90)),
    (21.5, (30, 70, 170, 150)),
    (21.0, (25, 140, 90, 175)),
    (20.4, (120, 180, 40, 190)),
    (19.8, (220, 200, 40, 205)),
    (19.1, (245, 140, 30, 215)),
    (18.4, (235, 60, 40, 225)),
    (17.6, (240, 110, 190, 235)),
    (16.8, (255, 255, 255, 245)),
]


def colour(sqm_values: np.ndarray) -> np.ndarray:
    """SQM -> RGBA bytes along LEGEND; NaN (no data) is transparent."""
    stops = np.array([s for s, _ in LEGEND])[::-1]         # ascending for interp
    rgba = np.array([c for _, c in LEGEND], dtype=float)[::-1]
    out = np.stack([np.interp(sqm_values, stops, rgba[:, i]) for i in range(4)], axis=-1)
    out[np.isnan(sqm_values)] = 0
    return np.round(out).astype(np.uint8)


def _tile_range(z: int, west: float, south: float, east: float, north: float):
    n = 2 ** z
    x0, x1 = int((west + 180) / 360 * n), int((east + 180) / 360 * n)
    def row(lat):
        return int((1 - np.arcsinh(np.tan(np.radians(lat))) / np.pi) / 2 * n)
    return range(x0, x1 + 1), range(row(north), row(south) + 1)


def tiles() -> None:
    """Pre-render the map as web-map tiles, once.

    The site picker overlays these. Rendering them here rather than on
    request keeps the server to handing out files: the map changes once a
    year, so there is nothing to compute per view.
    """
    import shutil
    from PIL import Image

    t0 = time.time()
    with rasterio.open(OUTPUT) as src:
        bright = src.read(1).astype(np.float64)
        west, south, east, north = src.bounds
        label = src.tags().get("LABEL", "")
    bright[bright < 0] = np.nan
    # Averages over 2x2, 4x4... cells, for zooms where a tile pixel spans many
    # cells; sampling the full grid there would alias towns into speckle.
    pyramid = [bright]
    while len(pyramid) < 6:
        b = pyramid[-1]
        h, w = b.shape[0] // 2 * 2, b.shape[1] // 2 * 2
        pyramid.append(np.nanmean(b[:h, :w].reshape(h // 2, 2, w // 2, 2), axis=(1, 3)))

    if TILES.exists():
        shutil.rmtree(TILES)
    written, size = 0, 0
    for z in TILE_ZOOMS:
        n = TILE * 2 ** z
        degrees_per_pixel = 360 / n
        level = max(0, min(len(pyramid) - 1, int(np.floor(np.log2(degrees_per_pixel / CELL)))))
        grid, cell = pyramid[level], CELL * 2 ** level
        xs, ys = _tile_range(z, west, south, east, north)
        for x in xs:
            lon = (x * TILE + np.arange(TILE) + 0.5) / n * 360 - 180
            cols = (lon - west) / cell - 0.5
            for y in ys:
                merc = np.pi * (1 - 2 * (y * TILE + np.arange(TILE) + 0.5) / n)
                lat = np.degrees(np.arctan(np.sinh(merc)))
                rows = (north - lat) / cell - 0.5
                r, c = np.meshgrid(rows, cols, indexing="ij")
                values = map_coordinates(grid, [r, c], order=1, mode="constant", cval=np.nan)
                rgba = colour(sqm(values))
                if not rgba[..., 3].any():
                    continue                     # all dark or outside: nothing to draw
                path = TILES / str(z) / str(x) / f"{y}.png"
                path.parent.mkdir(parents=True, exist_ok=True)
                # 256 colours with alpha is plenty for a smooth ramp, and a
                # quarter the size of full RGBA.
                image = Image.fromarray(rgba, "RGBA").quantize(256, method=Image.Quantize.FASTOCTREE)
                image.save(path, optimize=True)
                written += 1
                size += path.stat().st_size
        print(f"  zoom {z}: {written} tiles so far, {size / 2**20:.1f} MB", flush=True)
    meta = {"min_zoom": TILE_ZOOMS.start, "max_zoom": TILE_ZOOMS.stop - 1,
            "bounds": [west, south, east, north], "label": label,
            "legend": [[s, list(c)] for s, c in LEGEND]}
    (TILES / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    print(f"wrote {written} tiles to {TILES.relative_to(ROOT)} ({size / 2**20:.1f} MB) "
          f"in {time.time() - t0:.0f} s")


def install() -> None:
    """Put the built map and its tiles where engine/skybrightness.py looks."""
    import shutil

    config = ROOT / "config"
    shutil.copyfile(OUTPUT, config / "skybrightness.tif")
    target = config / "skybrightness_tiles"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(TILES, target)
    print(f"installed {OUTPUT.name} and its tiles into config/")


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("stage", choices=["crop", "fit", "apply", "tiles", "install"])
    args = parser.parse_args(argv)
    {"crop": crop, "fit": fit, "apply": apply, "tiles": tiles, "install": install}[args.stage]()


if __name__ == "__main__":
    main(sys.argv[1:])
