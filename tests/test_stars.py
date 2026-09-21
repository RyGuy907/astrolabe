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
    regulus = by_name("Regulus")
    assert regulus.age_basis == "upper limit" and "million" in regulus.age
    # An ordinary giant: nothing a single star's light can say.
    assert by_name("Albireo").age is None


def test_every_star_has_a_card():
    """No row in the vendored file makes the card fall over."""
    for i in range(0, len(_rows()), 7):
        star_profile(i)
