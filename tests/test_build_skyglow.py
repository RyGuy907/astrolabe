"""The geometry under scripts/build_skyglow.py.

The fit itself needs gigabytes of source data and is checked by its own
hold-out report. What can be pinned here is everything it stands on: that a
kilometre on the flat grid is a kilometre on the ground, that no light is
lost or gained putting it there, and that a lamp at a known distance lands
in the ring for that distance and no other. A mistake in any of these would
not crash the fit; it would quietly fit the wrong kernel.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("rasterio")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_skyglow as b  # noqa: E402

from engine.skybrightness import sqm_from_artificial_brightness  # noqa: E402


def _ground_km(lon1, lat1, lon2, lat2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = (np.sin((p2 - p1) / 2) ** 2
         + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2)
    return 2 * b.EARTH_KM * np.arcsin(np.sqrt(a))


def test_distances_on_the_plane_match_the_ground_across_the_lower_48():
    """The kernel is a function of distance, so the plane has to keep
    distance: within 1.5% for 100 km hops anywhere the fit looks."""
    rng = np.random.default_rng(0)
    lon = rng.uniform(b.FIT_WEST, b.FIT_EAST, 2000)
    lat = rng.uniform(b.FIT_SOUTH, b.FIT_NORTH, 2000)
    bearing = rng.uniform(0, 2 * np.pi, 2000)
    step = np.degrees(100 / b.EARTH_KM)
    lat2 = lat + step * np.cos(bearing)
    lon2 = lon + step * np.sin(bearing) / np.cos(np.radians(lat))
    x1, y1 = b.albers(lon, lat)
    x2, y2 = b.albers(lon2, lat2)
    ratio = np.hypot(x2 - x1, y2 - y1) / _ground_km(lon, lat, lon2, lat2)
    assert np.all(np.abs(ratio - 1) < 0.015)


@pytest.mark.parametrize("lat", [24, 37, 49, 52])
def test_the_plane_keeps_area_so_no_light_is_lost(lat):
    xs, ys = b.albers(np.array([-100.0, -99, -99, -100]), np.array([lat, lat, lat + 1.0, lat + 1]))
    plane = 0.5 * abs(np.dot(xs, np.roll(ys, 1)) - np.dot(ys, np.roll(xs, 1)))
    sphere = b.EARTH_KM ** 2 * np.radians(1) * (np.sin(np.radians(lat + 1)) - np.sin(np.radians(lat)))
    assert plane == pytest.approx(sphere, rel=1e-3)


def test_each_ring_covers_its_own_area():
    kernels = b.ring_kernels()
    for k, (inner, outer) in enumerate(zip(b.RINGS_KM, b.RINGS_KM[1:])):
        exact = np.pi * (outer ** 2 - inner ** 2)
        # The innermost rings are a few cells across and pixelate.
        tolerance = 0.1 if outer < 5 else 0.01
        assert kernels[k].sum() == pytest.approx(exact, rel=tolerance), (inner, outer)


@pytest.mark.parametrize("distance_km, ring_start", [(3, 2.5), (20, 18), (60, 50), (150, 140)])
def test_a_lamp_lands_in_the_ring_for_its_distance_and_no_other(distance_km, ring_start):
    lamp = np.zeros((2 * 160 + 1, 2 * 160 + 1))
    lamp[160, 160] = 1.0
    seen = [glow[160 - distance_km, 160] for glow in b.convolve_all(lamp, b.ring_kernels())]
    lit = [b.RINGS_KM[k] for k, value in enumerate(seen) if abs(value) > 1e-9]
    assert lit == [ring_start]
    assert max(seen) == pytest.approx(1.0)


def test_the_overlay_is_clear_where_the_sky_is_pristine_or_unknown():
    rgba = b.colour(np.array([22.0, 22.3, np.nan]))
    assert (rgba[:, 3] == 0).all()


def test_the_overlay_grows_more_opaque_as_the_sky_brightens():
    alpha = b.colour(np.linspace(22.0, 16.8, 60))[:, 3].astype(int)
    assert (np.diff(alpha) >= 0).all() and alpha[-1] > 200


def test_the_overlay_hits_each_legend_colour_exactly():
    for sqm_value, rgba in b.LEGEND:
        assert list(b.colour(np.array([sqm_value]))[0]) == list(rgba)


def test_haze_is_measured_from_the_valley_floor_and_molecules_from_the_sea():
    """A city on a high valley floor keeps its haze; a peak above the valley
    still stands above it. The molecules thin from sea level regardless."""
    height = np.full((120, 120), 1.4)          # a valley floor 1.4 km up
    height[50:70, 50:70] = 2.6                 # a mountain rising from it
    molecules, haze = b.layer_heights(height)
    assert molecules[10, 10] == pytest.approx(1.4)
    assert haze[10, 10] == pytest.approx(0.0, abs=1e-9)
    assert haze[60, 60] > 1.0


def test_only_a_bortle_1_sky_is_left_clear():
    """The faint glow that makes a place Bortle 2 has to show on the map; an
    even ramp once left it all but clear, and the glow seemed to stop short."""
    from engine.locations import BORTLE_SQM_LOWER

    assert b.colour(np.array([BORTLE_SQM_LOWER[1]]))[0, 3] == 0
    assert b.colour(np.array([BORTLE_SQM_LOWER[1] - 0.01]))[0, 3] >= 100


def test_the_key_lists_every_class_with_its_range():
    from engine.locations import BORTLE_SQM_LOWER

    key = b._class_key()
    assert [c["bortle"] for c in key] == list(range(1, 10))
    assert all(c["sqm_min"] == BORTLE_SQM_LOWER.get(c["bortle"]) for c in key)
    assert key[0]["rgba"][3] == 0 and all(c["rgba"][3] > 0 for c in key[1:])


def test_the_script_and_the_engine_convert_brightness_identically():
    for mcd in (0.0, 0.05, 0.5, 5.0):
        assert float(b.sqm(np.array(mcd))) == pytest.approx(sqm_from_artificial_brightness(mcd))
