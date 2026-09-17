"""The curated showpiece list, and the things that could silently empty it.

`engine/showpieces.py` is a hand-written list of identifiers. Two ways that
goes wrong without anyone noticing: the catalogue renames or drops an object,
so an entry matches nothing; or the exclusion set drifts and objects the
module's docstring says are absent quietly come back. Both look like a
working filter that returns a slightly different list, which is exactly the
kind of change nobody spots by eye.
"""

from __future__ import annotations

import pytest

from engine.catalog.loader import load_catalog
from engine.showpieces import (
    MESSIER_EXCLUDED,
    NON_MESSIER_SHOWPIECES,
    showpiece_ids,
)


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


@pytest.fixture(scope="module")
def ids(catalog):
    return showpiece_ids(catalog)


def test_every_hardcoded_identifier_still_resolves(catalog):
    """The load-bearing check.

    `showpiece_ids` intersects with what the catalogue holds, so a stale entry
    does not raise -- it just silently stops matching, and the list gets one
    object shorter. This is the test that notices.
    """
    present = {obj.name for obj in catalog}
    missing = sorted(NON_MESSIER_SHOWPIECES - present)
    assert not missing, (
        f"these showpiece ids are no longer in the catalogue: {missing}. "
        "Either the catalogue renamed them or the entry was wrong; do not "
        "just delete them without checking which."
    )


def test_the_objects_a_beginner_would_look_for_are_there(catalog, ids):
    """A sanity list in plain names.

    If someone opens "popular targets only" and cannot find the Ring Nebula,
    the feature is broken no matter what the count says.
    """
    wanted = {
        13: "Hercules Globular Cluster",
        27: "Dumbbell Nebula",
        31: "Andromeda Galaxy",
        42: "Orion Nebula",
        45: "Pleiades",
        51: "Whirlpool Galaxy",
        57: "Ring Nebula",
        7: "Ptolemy's Cluster",
        8: "Lagoon Nebula",
        16: "Eagle Nebula",
        17: "Swan Nebula",
        20: "Trifid Nebula",
        33: "Triangulum Galaxy",
        11: "Wild Duck Cluster",
        44: "Beehive Cluster",
    }
    by_messier = {obj.messier: obj for obj in catalog if obj.messier}
    for number, description in wanted.items():
        obj = by_messier.get(number)
        assert obj is not None, f"M{number} ({description}) is not in the catalogue"
        assert obj.name in ids, f"M{number} ({description}) is not a showpiece"


def test_the_documented_exclusions_are_actually_excluded(catalog, ids):
    """M40 is a double star and M73 is four unrelated stars.

    The module docstring says so; this asserts the code agrees with the
    docstring, which is the half that rots.
    """
    by_messier = {obj.messier: obj for obj in catalog if obj.messier}
    for number in MESSIER_EXCLUDED:
        obj = by_messier.get(number)
        if obj is None:
            continue                       # not in this catalogue release
        assert obj.name not in ids, f"M{number} should be excluded but is not"


def test_the_list_is_a_filter_not_a_catalogue(catalog, ids):
    """It has to be small enough to be worth checking the box for.

    The whole point is scrolling past twelve thousand objects to reach the
    Dumbbell. A list that grew to a thousand entries would still technically
    filter and would have stopped being useful.
    """
    assert 100 <= len(ids) <= 250, (
        f"{len(ids)} showpieces: small enough to browse is roughly 100-250"
    )
    assert len(ids) < len(catalog) / 20


def test_southern_showpieces_are_included(catalog, ids):
    """Messier observed from Paris, so the catalogue he left has a hole in it
    below about -35 deg declination. Omega Centauri and 47 Tucanae are the
    two finest globulars in the sky and neither is a Messier object."""
    by_name = {obj.name: obj for obj in catalog}
    for identifier in ("NGC5139", "NGC0104", "NGC3372"):
        assert identifier in ids
        assert by_name[identifier].dec_deg < -25.0


def test_photographic_only_objects_are_absent(catalog, ids):
    """The Horsehead is a household name and an H-beta-filter object.

    Putting it on a list headed "popular targets" would send a beginner
    hunting for something they will not see, which is the opposite of what
    the filter is for. It stays in the full catalogue.
    """
    horsehead = next((obj for obj in catalog
                      if "Horsehead" in " ".join(obj.common_names)), None)
    assert horsehead is not None, "the catalogue no longer has the Horsehead"
    assert horsehead.name not in ids


def test_resolution_is_independent_of_catalogue_ordering(catalog, ids):
    """Nothing here may depend on the order rows happen to load in."""
    assert showpiece_ids(list(reversed(catalog))) == ids
