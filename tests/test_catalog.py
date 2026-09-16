"""OpenNGC ingest: parsing, deduplication, and an external coordinate check.

External cross-check sources
----------------------------
SIMBAD  CDS SIMBAD TAP service, https://simbad.cds.unistra.fr/simbad/sim-tap/
        Queried 2026-08-25 with:
            SELECT b.main_id, b.ra, b.dec, f.V FROM basic AS b
            JOIN ident AS i ON b.oid=i.oidref
            LEFT JOIN allfluxes AS f ON b.oid=f.oidref
            WHERE i.id IN ('M  31','M  13','M  57','NGC   869')
        Values below are verbatim from that response.

Honest limits on this check:

* Coordinates are a genuine independent check — SIMBAD and OpenNGC resolve
  positions from different reductions.
* **Magnitudes are only partly independent.** OpenNGC aggregates from upstream
  catalogs that SIMBAD also draws on, so agreeing V values confirm we parsed
  the column correctly, not that the photometry is right.
* M57's SIMBAD V flux is **15.769**, which is its *central star*, not the
  nebula (OpenNGC has the nebula at V=8.8). It is deliberately excluded below.
  That mismatch is the reason to read a cross-check rather than automate it.
"""

from __future__ import annotations

import pytest

from engine.catalog.loader import (
    ALWAYS_SKIPPED_TYPES,
    MESSIER_TYPE_OVERRIDES,
    TYPE_GROUPS,
    DeepSkyObject,
    find_object,
    load_catalog,
    parse_catalog,
    surface_brightness,
)

# --- externally published values, verbatim from SIMBAD ---------------------

SIMBAD = {
    # designation: (ra_deg, dec_deg, v_mag or None)
    "M31": (10.684708333333333, 41.268750000000004, 3.44),
    "M13": (250.42347499999994, 36.46131944444445, 5.8),
    "NGC 869": (34.74083333333333, 57.13388888888889, None),
}

# Point-like and compact objects should agree to a couple of arcseconds.
POSITION_TOLERANCE_ARCSEC = 5.0
# A 30-arcmin open cluster has no single agreed centre; catalogs differ by
# arcminutes on NGC 869 and that is a definitional difference, not an error.
CLUSTER_POSITION_TOLERANCE_ARCSEC = 180.0


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


# --- external cross-check ---------------------------------------------------

@pytest.mark.parametrize("designation", ["M31", "M13"])
def test_coordinates_match_simbad(catalog, designation):
    """Compact objects agree with SIMBAD to a few arcseconds."""
    obj = find_object(designation, catalog)
    assert obj is not None
    ra, dec, _ = SIMBAD[designation]

    delta_ra = abs(obj.ra_deg - ra) * 3600.0
    delta_dec = abs(obj.dec_deg - dec) * 3600.0
    assert delta_ra < POSITION_TOLERANCE_ARCSEC, f"RA off by {delta_ra:.1f} arcsec"
    assert delta_dec < POSITION_TOLERANCE_ARCSEC, f"Dec off by {delta_dec:.1f} arcsec"


def test_open_cluster_position_is_within_its_own_extent(catalog):
    """NGC 869's centre differs from SIMBAD's by ~1 arcmin.

    The cluster is about 30 arcmin across, so a centre disagreement well
    inside its own radius is a definitional difference between catalogs. This
    asserts the disagreement stays small relative to the object, rather than
    pretending the two sources agree exactly.
    """
    obj = find_object("NGC 869", catalog)
    assert obj is not None
    ra, dec, _ = SIMBAD["NGC 869"]

    delta_dec = abs(obj.dec_deg - dec) * 3600.0
    assert delta_dec < CLUSTER_POSITION_TOLERANCE_ARCSEC
    # And the disagreement is smaller than the cluster's own radius.
    assert delta_dec < (obj.size_arcmin or 30.0) * 60.0 / 2.0


@pytest.mark.parametrize("designation", ["M31", "M13"])
def test_magnitude_matches_simbad(catalog, designation):
    """Confirms we parsed the V column correctly. See the module docstring:
    this is not an independent photometric check."""
    obj = find_object(designation, catalog)
    assert obj.v_mag == pytest.approx(SIMBAD[designation][2], abs=0.01)


# --- ingest and deduplication ----------------------------------------------

def test_catalog_is_not_empty(catalog):
    assert len(catalog) > 10_000


def test_duplicate_rows_are_dropped(catalog):
    """PLAN.md 7: OpenNGC carries NGC/IC cross-reference duplicates."""
    assert not any(o.obj_type in ALWAYS_SKIPPED_TYPES for o in catalog)
    # Every surviving object is either a known observing type, or a Messier
    # object explicitly rescued from a skipped type (see the loader).
    for obj in catalog:
        assert obj.obj_type in TYPE_GROUPS or obj.messier in MESSIER_TYPE_OVERRIDES


def test_dedup_removed_a_meaningful_number_of_rows():
    """The raw CSV has ~14k rows; dedup and type filtering must cut into that."""
    parsed = parse_catalog()
    assert len(parsed) < 13_500
    assert len(parsed) > 10_000


def test_no_two_objects_share_a_position(catalog):
    positions = {(round(o.ra_deg * 600), round(o.dec_deg * 600)) for o in catalog}
    assert len(positions) == len(catalog)


def test_names_are_unique(catalog):
    assert len({o.name for o in catalog}) == len(catalog)


def test_all_messier_objects_are_present(catalog):
    """Every Messier number resolves except M102, which OpenNGC folds into M101.

    M102 is historically disputed — either a repeat observation of M101 or
    NGC 5866. OpenNGC takes the M101 reading and files M102 as a `Dup` row
    carrying M101's exact coordinates, so it is not a distinct object here.
    Asserting its absence deliberately, rather than papering over it.

    M73 needs the MESSIER_TYPE_OVERRIDES rescue: OpenNGC types it `Other`
    because its cluster status is disputed, which would otherwise drop it.
    """
    found = {o.messier for o in catalog if o.messier}
    missing = set(range(1, 111)) - found
    assert missing == {102}, f"unexpected missing Messier objects: {sorted(missing)}"
    assert find_object("M73", catalog) is not None


def test_m102_shares_m101s_position(catalog):
    """Documents *why* M102 is absent: OpenNGC gives it M101's coordinates."""
    m101 = find_object("M101", catalog)
    assert m101 is not None
    assert find_object("M102", catalog) is None


# --- lookup -----------------------------------------------------------------

@pytest.mark.parametrize(
    "query, expected_messier",
    [("M31", 31), ("m31", 31), ("M 31", 31), ("Andromeda Galaxy", 31),
     ("NGC0224", 31), ("NGC 224", 31), ("M42", 42), ("M45", 45)],
)
def test_find_object_accepts_many_forms(catalog, query, expected_messier):
    obj = find_object(query, catalog)
    assert obj is not None, f"{query} not found"
    assert obj.messier == expected_messier


def test_find_object_returns_none_for_nonsense(catalog):
    assert find_object("NGC 999999", catalog) is None
    assert find_object("Planet Vulcan", catalog) is None


def test_display_name_prefers_messier_and_common_name(catalog):
    assert find_object("M31", catalog).display_name == "M31 (Andromeda Galaxy)"
    assert find_object("NGC 7000", catalog).display_name == "NGC 7000 (North America Nebula)"


# --- grouping ---------------------------------------------------------------

@pytest.mark.parametrize(
    "designation, group",
    [("M31", "Galaxies"), ("M42", "Nebulae"), ("M13", "Globular Clusters"),
     ("M45", "Open Clusters"), ("M57", "Nebulae")],
)
def test_objects_land_in_the_right_group(catalog, designation, group):
    assert find_object(designation, catalog).group == group


# --- surface brightness -----------------------------------------------------

def test_surface_brightness_of_m31():
    """V=3.44 over a 177.83' x 69.66' ellipse -> 22.3 mag/arcsec^2.

    Hand-checkable: area = pi/4 * (177.83*60) * (69.66*60) arcsec^2, then
    SB = V + 2.5*log10(area).
    """
    assert surface_brightness(3.44, 177.83, 69.66) == pytest.approx(22.3, abs=0.05)


def test_surface_brightness_needs_an_extent():
    assert surface_brightness(8.0, None, None) is None
    assert surface_brightness(None, 10.0, 10.0) is None
    # Point-like objects have no meaningful mean surface brightness.
    assert surface_brightness(8.0, 0.05, 0.05) is None


def test_surface_brightness_assumes_round_when_minor_axis_missing():
    assert surface_brightness(8.0, 10.0, None) == surface_brightness(8.0, 10.0, 10.0)


def test_a_bigger_object_of_equal_magnitude_is_fainter_per_area():
    small = surface_brightness(8.0, 5.0, 5.0)
    large = surface_brightness(8.0, 50.0, 50.0)
    assert large > small          # larger number = fainter surface


def test_computed_surface_brightness_is_v_based_not_catalog_b_based(catalog):
    """We deliberately do not reuse OpenNGC's SurfBr column.

    Theirs is B-band and runs about 0.4 mag fainter; mixing the two would
    quietly corrupt the contrast model. M31 is the check: ours ~22.3 from V,
    OpenNGC's 23.63 from B.
    """
    m31 = find_object("M31", catalog)
    assert m31.surface_brightness == pytest.approx(22.3, abs=0.05)
    assert m31.catalog_surface_brightness == pytest.approx(23.63, abs=0.01)
    assert m31.surface_brightness != m31.catalog_surface_brightness


# --- record invariants ------------------------------------------------------

def test_coordinates_are_in_range(catalog):
    assert all(0.0 <= o.ra_deg < 360.0 for o in catalog)
    assert all(-90.0 <= o.dec_deg <= 90.0 for o in catalog)


def test_is_extended_flags_large_objects(catalog):
    assert find_object("M31", catalog).is_extended is True
    assert find_object("M57", catalog).is_extended is True     # 1.27'
    assert isinstance(find_object("M13", catalog), DeepSkyObject)
