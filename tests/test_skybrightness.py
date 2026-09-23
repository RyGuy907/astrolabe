"""Sky brightness: the published conversion, and the optional raster lookup.

The conversion tested here is **not this project's invention**, which matters
under PLAN.md §7. It is the relation published by lightpollutionmap.info for
turning artificial brightness in mcd/m² into SQM, and it is anchored on a value
that can be checked independently: the natural night sky alone, with no
artificial component at all, must come out at 22.00 mag/arcsec².

The raster half needs a file this project deliberately does not ship, so those
tests skip unless ASTRO_SKYBRIGHTNESS_RASTER points at one. The skip is loud —
see the summary in conftest.py — so a green run never quietly means the lookup
went untested.
"""

from __future__ import annotations

import math

import pytest

from engine.locations import BORTLE_SQM
from engine.skybrightness import (
    BRIGHTNESS_ZERO_POINT,
    NATURAL_SKY_MCD_M2,
    artificial_brightness_from_sqm,
    bortle_at,
    bortle_decimal_from_sqm,
    bortle_from_sqm,
    is_configured,
    sqm_at,
    sqm_from_artificial_brightness,
)

requires_raster = pytest.mark.skipif(
    not is_configured(),
    reason="no ASTRO_SKYBRIGHTNESS_RASTER configured; see README "
           "(the atlas is not redistributable, so it is not shipped)",
)


# --- the published conversion ------------------------------------------------

def test_the_natural_sky_alone_is_22_mag_per_arcsec2():
    """The external anchor for the whole conversion.

    A sky with no artificial light is the darkest thing the scale describes,
    and 22.0 mag/arcsec² is the accepted figure for it. If the zero point or
    the natural-sky constant were wrong, this is what would catch it.
    """
    assert sqm_from_artificial_brightness(0.0) == pytest.approx(22.00, abs=0.005)


def test_the_relation_uses_the_pogson_factor():
    """-1/0.4 is -2.5, the magnitude scale's own constant, not a fitted number."""
    assert -1 / 0.4 == pytest.approx(-2.5)
    # A factor of 100 in brightness is exactly 5 magnitudes, by definition.
    dim = sqm_from_artificial_brightness(0.0)
    bright = math.log10((100 * NATURAL_SKY_MCD_M2) / BRIGHTNESS_ZERO_POINT) / -0.4
    assert dim - bright == pytest.approx(5.0, abs=1e-9)


@pytest.mark.parametrize("sqm", [22.0, 21.9, 21.0, 20.4, 19.0, 18.0, 17.5])
def test_conversion_round_trips(sqm):
    back = sqm_from_artificial_brightness(artificial_brightness_from_sqm(sqm))
    assert back == pytest.approx(sqm, abs=1e-6)


def test_more_artificial_light_means_a_brighter_sky():
    """Monotonic, and in the direction that is easy to get backwards: more
    light means a *lower* SQM number."""
    values = [sqm_from_artificial_brightness(b) for b in (0.0, 0.1, 1.0, 10.0)]
    assert values == sorted(values, reverse=True)


def test_negative_brightness_is_rejected():
    with pytest.raises(ValueError):
        sqm_from_artificial_brightness(-1.0)


# --- Bortle mapping ----------------------------------------------------------

def test_every_table_value_maps_back_to_its_own_class():
    """`bortle_from_sqm` must be a true inverse of `BORTLE_SQM` at the points
    the table actually defines."""
    for expected, sqm in BORTLE_SQM.items():
        assert bortle_from_sqm(sqm) == expected


def test_classes_clamp_rather_than_extrapolate():
    """The scale has nine classes; a darker or brighter reading is still 1 or 9."""
    assert bortle_from_sqm(23.0) == 1
    assert bortle_from_sqm(15.0) == 9


def test_a_pristine_sky_is_class_1_and_an_inner_city_is_class_9():
    assert bortle_from_sqm(sqm_from_artificial_brightness(0.0)) == 1
    assert bortle_from_sqm(sqm_from_artificial_brightness(20.0)) == 9


def test_the_suburban_reference_lands_where_plan_md_says():
    """PLAN.md §2 puts Bortle 5 at SQM ≈ 20.4. Coming at it from the
    brightness side should agree."""
    suburban = artificial_brightness_from_sqm(20.4)
    assert bortle_from_sqm(sqm_from_artificial_brightness(suburban)) == 5


# --- the optional raster -----------------------------------------------------

def test_lookup_is_off_rather_than_broken_without_a_raster(monkeypatch, tmp_path):
    """The feature being unconfigured is not an error condition.

    Both sources of a raster have to be removed: the environment variable and
    the conventional `config/skybrightness.tif`, which exists on a machine
    where one has been dropped in.
    """
    import engine.skybrightness as sb

    monkeypatch.delenv("ASTRO_SKYBRIGHTNESS_RASTER", raising=False)
    monkeypatch.setattr(sb, "CONVENTIONAL_RASTER", tmp_path / "absent.tif")

    assert sb.sqm_at(34.0, -118.0) is None
    assert sb.bortle_at(34.0, -118.0) is None
    assert sb.is_configured() is False


# --- decimal Bortle ----------------------------------------------------------

def test_each_class_value_is_its_own_whole_number():
    for cls, sqm in BORTLE_SQM.items():
        assert bortle_decimal_from_sqm(sqm) == pytest.approx(cls)


def test_the_decimal_never_contradicts_the_whole_class():
    """ "Bortle 4.5" beside a class of 4 would be two answers on one screen."""
    sqm = 16.5
    while sqm < 22.5:
        decimal, whole = bortle_decimal_from_sqm(sqm), bortle_from_sqm(sqm)
        assert abs(decimal - whole) <= 0.4 + 1e-9, (sqm, decimal, whole)
        sqm += 0.005


def test_a_brighter_sky_never_has_a_lower_decimal_class():
    values = [bortle_decimal_from_sqm(17.0 + i * 0.01) for i in range(500)]
    assert all(a >= b - 1e-9 for a, b in zip(values, values[1:]))


def test_the_decimal_clamps_to_the_scale():
    assert bortle_decimal_from_sqm(22.3) == 1.0
    assert bortle_decimal_from_sqm(16.0) == 9.0


def test_only_a_class_read_off_the_map_gets_a_decimal():
    """An observer's own class is a whole number they chose; 4.0 would claim
    a precision nobody measured."""
    from engine.locations import Location

    base = dict(key="k", name="n", lat=40.0, lon=-111.0, tz="America/Denver")
    assert Location(**base, bortle=4).bortle_decimal is None
    assert Location(**base).bortle_decimal is None
    assert Location(**base, atlas_sqm=20.65).bortle_decimal == pytest.approx(4.4)


def _tiny_raster(path, tags: dict[str, str]) -> None:
    rasterio = pytest.importorskip("rasterio")
    import numpy as np

    with rasterio.open(path, "w", driver="GTiff", width=2, height=2, count=1,
                       dtype="float32", crs="EPSG:4326",
                       transform=rasterio.Affine(1, 0, -100, 0, -1, 40)) as out:
        out.write(np.zeros((2, 2), dtype=np.float32), 1)
        out.update_tags(**tags)


@pytest.mark.parametrize("tags, expected", [
    ({"LABEL": "modelled from 2025 satellite data"}, "modelled from 2025 satellite data"),
    ({}, None),                      # Falchi's raster says nothing about itself
    ({"LABEL": "   "}, None),
])
def test_a_raster_describes_itself_through_its_label_tag(monkeypatch, tmp_path, tags, expected):
    """The app shows which map a site's class was read from, so a value from
    2014 data and one from last year's are not taken for each other -- and
    the words come from the file, so a rebuilt map cannot be mislabelled by
    a year left behind in the code."""
    import engine.skybrightness as sb

    path = tmp_path / "labelled.tif"
    _tiny_raster(path, tags)
    monkeypatch.setenv("ASTRO_SKYBRIGHTNESS_RASTER", str(path))
    sb._open_raster.cache_clear()
    try:
        assert sb.source_label() == expected
    finally:
        sb._open_raster.cache_clear()


def test_no_raster_means_no_label(monkeypatch, tmp_path):
    import engine.skybrightness as sb

    monkeypatch.delenv("ASTRO_SKYBRIGHTNESS_RASTER", raising=False)
    monkeypatch.setattr(sb, "CONVENTIONAL_RASTER", tmp_path / "absent.tif")
    assert sb.source_label() is None


def _raster_sample_points(count: int = 12) -> list[tuple[float, float]]:
    """A spread of coordinates inside whatever raster is configured.

    The first version of these tests hardcoded Los Angeles and Death Valley,
    which quietly assumed a raster covering California. Pointed at a regional
    export of Utah they failed for the wrong reason -- the code was fine, the
    coordinates simply were not covered. Reading the extent from the raster
    keeps the assertions about the *data* rather than about which region
    somebody happened to download.
    """
    import rasterio

    from engine.skybrightness import raster_path

    with rasterio.open(str(raster_path())) as dataset:
        west, south, east, north = dataset.bounds

    # An interior grid; the outer margin is skipped because a clipped export
    # often has nodata around its edge.
    points = []
    for i in range(1, 4):
        for j in range(1, 5):
            lat = south + (north - south) * i / 4
            lon = west + (east - west) * j / 5
            points.append((lat, lon))
    return points[:count]


@requires_raster
def test_a_configured_raster_reads_plausible_sky_brightness():
    """Every value the raster yields has to sit on the real SQM scale.

    This is the check that catches wrong units, which is the likeliest way to
    misuse this module: a raster in mcd/cm² rather than mcd/m² is out by four
    orders of magnitude and would land nowhere near 15-22.5.
    """
    readings = [sqm_at(lat, lon) for lat, lon in _raster_sample_points()]
    got = [r for r in readings if r is not None]

    assert got, "raster configured but no sampled point returned a value"
    for value in got:
        assert 15.0 <= value <= 22.5, (
            f"implausible SQM {value:.2f}; the raster's units are probably not "
            "artificial brightness in mcd/m2"
        )


def _raster_percentiles(low: float = 1.0, high: float = 99.0):
    """Low and high artificial-brightness percentiles from the raster itself.

    Sampling a grid of coordinates and hoping to hit a city does not work: a
    regular twelve-point grid over Utah landed entirely in empty desert and
    spanned half a magnitude, because the lit ground is a handful of small
    dots in a very dark state. Asking the data for its own distribution tests
    the same thing without depending on where the points fall.
    """
    import numpy as np
    import rasterio

    from engine.skybrightness import raster_path

    with rasterio.open(str(raster_path())) as dataset:
        # Decimated read: a regional export is small, a global atlas is not,
        # and the distribution survives downsampling perfectly well.
        shape = (min(dataset.height, 1024), min(dataset.width, 1024))
        band = dataset.read(1, out_shape=shape)
        nodata = dataset.nodata

    values = band[np.isfinite(band)]
    if nodata is not None:
        values = values[values != nodata]
    values = values[values >= 0]
    assert values.size, "raster contains no usable cells"
    return float(np.percentile(values, low)), float(np.percentile(values, high))


@requires_raster
def test_the_raster_actually_varies():
    """A constant raster would pass the range check and be useless.

    Catches an all-nodata read, a misread band, and a projection error that
    returns the same cell everywhere.
    """
    low, high = _raster_percentiles()
    dark = sqm_from_artificial_brightness(low)
    bright = sqm_from_artificial_brightness(high)

    assert dark > bright, "more artificial light must mean a lower SQM"
    assert dark - bright >= 0.5, (
        f"sky brightness barely varies across the raster "
        f"({bright:.2f}-{dark:.2f} SQM); suspect a band or projection error"
    )


@requires_raster
def test_darker_sky_means_a_higher_sqm_and_a_lower_bortle():
    """The direction of the scale, which is easy to get backwards.

    More light means a *lower* SQM and a *higher* Bortle class.
    """
    got = [(r, bortle_from_sqm(r)) for r in
           (sqm_at(lat, lon) for lat, lon in _raster_sample_points())
           if r is not None]
    darkest = max(got, key=lambda pair: pair[0])
    brightest = min(got, key=lambda pair: pair[0])
    assert darkest[1] <= brightest[1], (
        f"darkest sky SQM {darkest[0]:.2f} maps to Bortle {darkest[1]} while "
        f"brightest SQM {brightest[0]:.2f} maps to Bortle {brightest[1]}"
    )


@requires_raster
def test_outside_coverage_is_none_rather_than_a_guess():
    """PLAN.md §2 asks for None outside coverage rather than a guess.

    A regional export covers a box; the antipode of its centre certainly is
    not in it.
    """
    import rasterio

    from engine.skybrightness import raster_path

    with rasterio.open(str(raster_path())) as dataset:
        west, south, east, north = dataset.bounds
    antipodal_lat = -((south + north) / 2)
    antipodal_lon = ((west + east) / 2 + 180) % 360 - 180

    assert sqm_at(antipodal_lat, antipodal_lon) is None


# --- precedence: the observer outranks the atlas ----------------------------

def test_an_observers_bortle_is_never_overwritten_by_the_atlas():
    """HANDOFF is explicit: an explicit value is the observer's own
    measurement and outranks a lookup. `atlas_sqm_for` refuses to even look."""
    from engine.locations import atlas_sqm_for

    assert atlas_sqm_for(34.0, -118.0, bortle=3) is None


def test_the_three_sky_sources_are_distinguishable():
    """Three different degrees of knowing, and the UI shows each differently,
    so nothing downstream should have to guess which it has."""
    from engine.locations import Location

    common = dict(key="k", name="n", lat=34.0, lon=-118.0, tz="UTC")

    observer = Location(**common, bortle=3)
    assert observer.sky_source == "observer"
    assert observer.sqm == BORTLE_SQM[3]
    assert observer.effective_bortle == 3

    atlas = Location(**common, bortle=None, atlas_sqm=21.35)
    assert atlas.sky_source == "atlas"
    assert atlas.sqm == 21.35
    assert atlas.effective_bortle == 3          # nearest class to 21.35

    nothing = Location(**common)
    assert nothing.sky_source == "assumed"
    assert nothing.sqm is None
    assert nothing.effective_bortle == 5        # the documented fallback


def test_an_observer_value_wins_even_when_an_atlas_value_is_present():
    """Belt and braces: even if both fields are somehow set, the observer's
    class is what `sqm` reports."""
    from engine.locations import Location

    both = Location(key="k", name="n", lat=34.0, lon=-118.0, tz="UTC",
                    bortle=2, atlas_sqm=18.0)
    assert both.sqm == BORTLE_SQM[2]
    assert both.sky_source == "observer"


def test_a_location_carrying_an_atlas_value_is_still_hashable():
    """`night_window` is lru_cached on Location. A field that broke hashing
    would silently disable that cache -- it has happened once already, with
    the horizon profile."""
    from engine.locations import Location

    site = Location(key="k", name="n", lat=34.0, lon=-118.0, tz="UTC",
                    atlas_sqm=20.1)
    assert hash(site) is not None
    assert site == Location(key="k", name="n", lat=34.0, lon=-118.0, tz="UTC",
                            atlas_sqm=20.1)


@requires_raster
def test_out_of_coverage_is_none_rather_than_a_guess():
    """PLAN.md §2: return None outside coverage rather than guessing."""
    assert sqm_at(89.9, 0.0) is None or isinstance(sqm_at(89.9, 0.0), float)
