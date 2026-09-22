"""Star cards: what the chart says a clicked star is.

Checked against stars whose properties are well measured. The size is an
estimate, so the bounds are generous -- but wide of the mark by a factor of
two would mean a wrong temperature scale or bolometric correction, which is
what these are here to catch.
"""

from __future__ import annotations

import pytest

from engine.stars import (_rows, bolometric_correction, parse_spectral_type,
                          star_profile)


def by_name(name: str):
    index = next(i for i, row in enumerate(_rows()) if row[3] == name)
    return star_profile(index)


@pytest.mark.parametrize("spect, expected", [
    ("G2V", ("G", 2.0, "V")),
    ("K2IIIp", ("K", 2.0, "III")),
    ("M2Ib", ("M", 2.0, "I")),
    ("B8Ia", ("B", 8.0, "I")),
    ("F7:Ib-IIv SB", ("F", 7.0, "I")),
    ("F5IV-V", ("F", 5.0, "IV")),
    ("A0m...", ("A", 0.0, "V")),        # metallic-line: main sequence
    ("K0", ("K", 0.0, None)),
    ("DA2", ("D", 0.0, "D")),
])
def test_spectral_types_parse(spect, expected):
    assert parse_spectral_type(spect) == expected


def test_the_sun_needs_almost_no_bolometric_correction():
    assert bolometric_correction(5772) == pytest.approx(-0.08, abs=0.03)


@pytest.mark.parametrize("name, kind, radius", [
    ("Sirius", "White main-sequence star", (1.5, 2.1)),       # 1.71
    ("Vega", "White main-sequence star", (2.0, 3.2)),         # ~2.5
    ("Arcturus", "Orange giant", (18, 32)),                   # 25.4
    ("Aldebaran", "Orange giant", (33, 55)),                  # 44
    ("Pollux", "Orange giant", (7, 12)),                      # 9.1
    ("Betelgeuse", "Red supergiant", (450, 1100)),            # 640-900
    ("Rigel", "Blue-white supergiant", (50, 110)),            # ~79
])
def test_known_stars(name, kind, radius):
    star = by_name(name)
    assert star.kind == kind
    assert radius[0] <= star.radius_sun <= radius[1]


def test_distance_says_how_far_to_trust_it():
    assert by_name("Sirius").distance_ly == pytest.approx(8.6, abs=0.1)
    assert by_name("Sirius").distance_quality == "precise"
    assert by_name("Alnilam").distance_quality == "rough"


def test_age_only_where_it_can_be_known():
    assert by_name("Vega").age_basis == "published"
    # A hot main-sequence star with no published age: bounded, not guessed.
    alkaid = by_name("Alkaid")
    assert alkaid.age_basis == "upper limit" and "million" in alkaid.age
    # An ordinary giant: nothing a single star's light can say.
    assert by_name("Albireo").age is None


# Published values, as cited in each star's Wikipedia infobox (2026-09-21):
# distance ly, radius R_sun, temperature K, age in years. Gaia saturates on
# stars this bright, so these are the check for them. Tolerances: distance
# 10%, temperature 15%, radius 35% -- the card calls size an estimate, and a
# third either way is the honest reach of V magnitude plus a spectral type.
LITERATURE = [
    ("Sirius", 8.6, 1.71, None, 242e6),
    ("Arcturus", 36.7, 25.4, 4286, 7.1e9),
    ("Vega", 25.0, 2.73, 10070, 700e6),
    ("Capella", 42.9, 12.0, 4970, 590e6),
    ("Rigel", 848, 74.1, 12100, 8e6),
    ("Procyon", 11.5, 2.04, 6582, 1.87e9),
    ("Achernar", 139.4, 6.78, 12673, 63e6),
    ("Betelgeuse", None, 640, None, 14e6),
    # Altair spins fast enough to run from ~6,860 K at its equator to 8,620 K
    # at its poles; the midpoint, not the infobox's first (equatorial) figure.
    ("Altair", 16.7, 2.01, 7740, 88e6),
    ("Aldebaran", 66.6, 45.1, 3900, 6.4e9),
    # Antares was the audit's largest size miss when estimated -- 460 against
    # 680, a third low. Its interferometric diameter puts it at ~730.
    ("Antares", 553.7, 680, 3660, 15e6),
    ("Spica", 249.7, 7.47, 25300, 12.5e6),
    ("Fomalhaut", 25.1, 1.84, 8590, 440e6),
    ("Deneb", 1410, 117, 8525, 11.6e6),
    ("Canopus", 309.2, 73.3, 7400, 33e6),
    ("Bellatrix", 252.4, 6.2, 22017, 28e6),
    ("Mirfak", 506.5, 53.8, 6439, 41e6),
    ("Dubhe", 122.9, 27.3, 4810, 280e6),
    ("Hamal", 65.8, 15.2, 4553, 3.4e9),
    ("Alphard", 177.3, 57.6, 4117, 420e6),
    ("Kochab", 130.9, 44.1, 4008, 2.95e9),
    ("Eltanin", 154.3, 51.8, 3964, 1.3e9),
    ("Enif", 689.5, 183, 4100, 20e6),
    ("Mirach", 197.4, 86.4, 3762, None),
    ("Alnilam", None, 33.6, 25000, 4.47e6),
]

_UNITS = {"thousand": 1e3, "million": 1e6, "billion": 1e9}


def _age_span(text: str) -> tuple[float, float]:
    """(low, high) years from "about 8-14 million years" or "about 7 billion"."""
    import re
    unit = next(v for k, v in _UNITS.items() if k in text)
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", text)]
    return nums[0] * unit, nums[-1] * unit


@pytest.mark.parametrize("name, dist, radius, temp, age", LITERATURE,
                         ids=[row[0] for row in LITERATURE])
def test_cards_agree_with_published_values(name, dist, radius, temp, age):
    star = by_name(name)
    if dist:
        assert star.distance_ly == pytest.approx(dist, rel=0.10)
    if temp:
        assert star.temperature_k == pytest.approx(temp, rel=0.15)
    if radius:
        assert 1 / 1.35 <= star.radius_sun / radius <= 1.35
    if age and star.age:
        low, high = _age_span(star.age)
        if star.age_basis == "upper limit":
            assert age <= high                        # a bound must hold
        elif star.age.startswith("at least"):
            assert age >= low
        else:
            assert low / 1.25 <= age <= high * 1.25


def test_every_star_has_a_card():
    """No row in the vendored file makes the card fall over."""
    for i in range(0, len(_rows()), 7):
        star_profile(i)


# --- which source each figure comes from --------------------------------------

def test_a_measured_diameter_is_the_radius():
    """Antares' size from interferometry, not from its (dust-dimmed, variable)
    brightness: within 15% of the published 680 R_sun."""
    antares = by_name("Antares")
    assert antares.radius_source == "measured"
    assert antares.radius_sun == pytest.approx(680, rel=0.15)
    assert by_name("Sirius").radius_source == "measured"
    assert by_name("Sirius").radius_sun == pytest.approx(1.71, rel=0.10)


def test_a_measurement_blended_with_a_companion_is_not_used():
    # JMDC's one Capella entry is the pair's light ("Capella is binary").
    assert by_name("Capella").radius_source != "measured"


def test_fainter_stars_take_gaia_distances_and_sizes():
    from engine.stars import star_count
    sample = [star_profile(i) for i in range(20000, star_count(), 97)]
    gaia = [p for p in sample if p.distance_source == "gaia"]
    assert len(gaia) > 0.6 * len(sample)
    assert any(p.radius_source == "gaia" for p in sample)
    # Gaia's radii are only taken for the cool stars its temperatures suit.
    for p in sample:
        if p.radius_source == "gaia":
            assert p.spectral_type and p.spectral_type.lstrip("sd")[0] in "FGKM"


def test_without_the_details_file_every_card_still_works(monkeypatch):
    """The estimated path is the fallback for everything: with no Gaia or
    measured data at all, cards still come out, marked as estimates."""
    import engine.stars as stars
    monkeypatch.setattr(stars, "_details", lambda: {})
    betelgeuse = by_name("Betelgeuse")
    assert betelgeuse.radius_source == "estimated"
    assert betelgeuse.distance_source == "hipparcos"
    assert 450 <= betelgeuse.radius_sun <= 1100


# --- found by the wide audit (~300 named stars against published values) -----

def test_a_hot_type_on_an_orange_star_is_set_aside():
    """Almach is catalogued B8V -- its companion's type. Its colour (B-V 1.37)
    is an orange giant's, which is what the card must say."""
    almach = by_name("Almach")
    assert almach.kind == "Orange giant"
    assert almach.temperature_k == pytest.approx(4248, rel=0.15)
    assert almach.type_note and "B8V" in almach.type_note
    # Reddened supergiants are genuinely hot, and keep their type.
    assert by_name("Deneb").type_note is None


def test_a_lone_diameter_far_from_every_other_estimate_is_not_used():
    """gamma Lib's one JMDC diameter makes it 24 R_sun; published is 11."""
    zubenelhakrabi = by_name("Zubenelhakrabi")
    assert zubenelhakrabi.radius_source != "measured"
    assert zubenelhakrabi.radius_sun == pytest.approx(11.14, rel=0.35)


def test_names_follow_the_iau():
    """Each IAU name on one star only, in the IAU spelling: the audit's worst
    misses were the right numbers for a second star sharing a name."""
    from collections import Counter
    labels = Counter(row[3] for row in _rows() if row[3])
    for name in ("Terebellum", "Propus", "Beemim", "Markab", "Tarazed", "Menkar"):
        assert labels[name] == 1, name
    assert labels["Schedar"] == 1 and labels["Shedar"] == 0
    terebellum = next(row for row in _rows() if row[3] == "Terebellum")
    assert terebellum[4] == "98066"           # omega Sgr, per the WGSN
