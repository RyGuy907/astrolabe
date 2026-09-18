"""Facts about objects that the observing maths cannot derive.

How far away M13 is, who found it and when, how big Jupiter is — none of this
follows from an ephemeris or a catalogue row, and none of it changes with the
date. It is reference material, so it lives here as data with its sources
named rather than being computed from something that only looks like it.

Why this is hand-curated
------------------------
The obvious shortcut is the catalogue's own columns, and it does not work.
OpenNGC carries `Pax` and `Redshift`, and both are traps for distance:

* **Parallax** is right for a planetary nebula, whose central star Gaia has
  measured — M27 comes out at 376 pc against an accepted 380 — and nonsense
  for a galaxy. M31's row says 6.0 mas, which is 167 parsecs. M31 is 780,000
  parsecs away. The column has evidently cross-matched a foreground star.
* **Redshift** works for distant galaxies and fails for everything nearby,
  where peculiar motion swamps the expansion. M31's is *negative* — it is
  approaching us — so Hubble's law returns a negative distance.

Printing either would put confidently wrong numbers next to a photograph, and
a number that is wrong by a factor of 4,600 is worse than no number at all. So
distances here are quoted values, and an object with no entry simply shows no
distance.

Scope, and what is deliberately missing
---------------------------------------
This covers the showpieces — roughly what `engine/showpieces.py` lists, which
is what anyone actually opens — plus the planets. The other twelve thousand
catalogue entries get what the catalogue genuinely knows: type, size,
magnitude, surface brightness, coordinates. The UI omits any row it has no
value for rather than printing a blank or a guess.

Sources
-------
Distances and discovery credits follow the standard references: the NASA/IPAC
Extragalactic Database and SEDS' Messier pages for the deep-sky objects,
NASA's planetary fact sheets for the planets. Discovery credit is genuinely
contested for a few objects — several Messier entries were seen earlier by
Hodierna or by Arab and Persian astronomers centuries before — and where the
record is disputed the earlier observer is named, because "Messier catalogued
it" and "Messier found it" are different claims.

Distances use the unit an observer would use: light years for anything inside
the Local Group, million light years beyond it. They are stored as light years
throughout and formatted for display in `web/src/format.ts`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeepSkyFacts:
    """Reference data for one deep-sky object. Every field may be absent."""

    #: Distance in light years. None where the accepted value is too uncertain
    #: to quote — several nebulae are known only to within a factor of two.
    distance_ly: float | None = None
    #: Who is credited with first recording it, not who catalogued it.
    discovered_by: str | None = None
    #: Year of that first record. Negative for BCE.
    discovered_year: int | None = None
    #: One line of what the object actually is, where the type label alone
    #: undersells it ("G" for the Andromeda Galaxy, say).
    note: str | None = None


@dataclass(frozen=True)
class PlanetFacts:
    """Reference data for one planet. These are constants, not ephemeris."""

    equatorial_diameter_km: float
    #: Sidereal rotation period in hours. Negative for retrograde rotation,
    #: which is Venus and Uranus.
    rotation_hours: float
    #: Orbital period in Earth years.
    year_earth_years: float
    moons: int
    discovered_by: str | None
    discovered_year: int | None
    note: str


#: The planets. Physical values from NASA's planetary fact sheets.
#:
#: Discovery is the one field where "unknown" is the correct answer rather
#: than a gap in the data: Mercury through Saturn are naked-eye objects that
#: every ancient culture recorded, so there is no discoverer to name. Uranus
#: and Neptune have exactly one each, and Neptune's is worth stating carefully
#: -- Le Verrier predicted where to look and Galle looked, which is why the
#: credit is usually shared.
PLANET_FACTS: dict[str, PlanetFacts] = {
    "mercury": PlanetFacts(
        equatorial_diameter_km=4879, rotation_hours=1407.6,
        year_earth_years=0.24, moons=0,
        discovered_by=None, discovered_year=None,
        note="Never far from the Sun; a twilight object or nothing.",
    ),
    "venus": PlanetFacts(
        equatorial_diameter_km=12104, rotation_hours=-5832.5,
        year_earth_years=0.62, moons=0,
        discovered_by=None, discovered_year=None,
        note="Shows phases like the Moon. Cloud tops only -- there is no "
             "surface detail to see.",
    ),
    "mars": PlanetFacts(
        equatorial_diameter_km=6792, rotation_hours=24.6,
        year_earth_years=1.88, moons=2,
        discovered_by=None, discovered_year=None,
        note="Worth the trouble only near opposition, every 26 months.",
    ),
    "jupiter": PlanetFacts(
        equatorial_diameter_km=142984, rotation_hours=9.9,
        year_earth_years=11.86, moons=95,
        discovered_by=None, discovered_year=None,
        note="The four Galilean moons shift visibly within an hour.",
    ),
    "saturn": PlanetFacts(
        equatorial_diameter_km=120536, rotation_hours=10.7,
        year_earth_years=29.45, moons=146,
        discovered_by=None, discovered_year=None,
        note="The rings close to an invisible edge every 15 years or so.",
    ),
    "uranus": PlanetFacts(
        equatorial_diameter_km=51118, rotation_hours=-17.2,
        year_earth_years=84.0, moons=28,
        discovered_by="William Herschel", discovered_year=1781,
        note="A small blue-green disc; the first planet found with a "
             "telescope.",
    ),
    "neptune": PlanetFacts(
        equatorial_diameter_km=49528, rotation_hours=16.1,
        year_earth_years=164.8, moons=16,
        discovered_by="Johann Galle, predicted by Urbain Le Verrier",
        discovered_year=1846,
        note="Found by calculation before it was found by looking.",
    ),
}


def planet_facts(name: str) -> PlanetFacts | None:
    """Reference data for a planet, or None for anything else (the Moon)."""
    return PLANET_FACTS.get(name.strip().lower())


#: Deep-sky reference data, keyed by Messier number.
#:
#: Discovery credit names the first recorded observer, which is often not
#: Messier: he was compiling a list of things that were *not* comets, and more
#: than half his catalogue had been seen before by Hodierna, de Cheseaux,
#: Lacaille or Mechain. Where the record reaches further back than European
#: telescopes -- al-Sufi's tenth-century description of the Andromeda Galaxy,
#: Ptolemy's of the cluster that carries his name -- that is the credit given.
#:
#: Distances are accepted modern values in light years, and some are known far
#: better than others: the Pleiades is settled to a percent or two, while
#: several emission nebulae are quoted to within a factor of two across
#: different sources. Entries left out are left out on purpose.
MESSIER_FACTS: dict[int, DeepSkyFacts] = {
    1: DeepSkyFacts(6500, "John Bevis", 1731,
                    "The wreck of a supernova Chinese astronomers recorded "
                    "in 1054."),
    2: DeepSkyFacts(37500, "Jean-Dominique Maraldi", 1746),
    3: DeepSkyFacts(33900, "Charles Messier", 1764),
    4: DeepSkyFacts(7200, "Philippe Loys de Cheseaux", 1746,
                    "One of the nearest globulars, and loose enough to resolve."),
    5: DeepSkyFacts(24500, "Gottfried Kirch", 1702),
    6: DeepSkyFacts(1600, "Giovanni Battista Hodierna", 1654),
    7: DeepSkyFacts(980, "Ptolemy", 130,
                    "Recorded in the Almagest; the oldest catalogued cluster."),
    8: DeepSkyFacts(4100, "Giovanni Battista Hodierna", 1654),
    9: DeepSkyFacts(25800, "Charles Messier", 1764),
    10: DeepSkyFacts(14300, "Charles Messier", 1764),
    11: DeepSkyFacts(6200, "Gottfried Kirch", 1681,
                     "The richest open cluster in the sky for a small scope."),
    12: DeepSkyFacts(15700, "Charles Messier", 1764),
    13: DeepSkyFacts(22200, "Edmond Halley", 1714,
                     "Several hundred thousand stars in a ball 145 ly across."),
    14: DeepSkyFacts(30300, "Charles Messier", 1764),
    15: DeepSkyFacts(33600, "Jean-Dominique Maraldi", 1746),
    16: DeepSkyFacts(5700, "Philippe Loys de Cheseaux", 1745),
    17: DeepSkyFacts(5500, "Philippe Loys de Cheseaux", 1745),
    18: DeepSkyFacts(4900, "Charles Messier", 1764),
    19: DeepSkyFacts(28700, "Charles Messier", 1764),
    20: DeepSkyFacts(5200, "Charles Messier", 1764),
    21: DeepSkyFacts(4250, "Charles Messier", 1764),
    22: DeepSkyFacts(10400, "Abraham Ihle", 1665,
                     "The first globular cluster recorded as one."),
    23: DeepSkyFacts(2050, "Charles Messier", 1764),
    24: DeepSkyFacts(10000, "Charles Messier", 1764,
                     "Not a cluster: a gap in the dust looking through to the "
                     "Sagittarius arm."),
    25: DeepSkyFacts(2000, "Philippe Loys de Cheseaux", 1745),
    26: DeepSkyFacts(5000, "Charles Messier", 1764),
    27: DeepSkyFacts(1360, "Charles Messier", 1764,
                     "The first planetary nebula ever found."),
    28: DeepSkyFacts(17900, "Charles Messier", 1764),
    29: DeepSkyFacts(4000, "Charles Messier", 1764),
    30: DeepSkyFacts(27100, "Charles Messier", 1764),
    31: DeepSkyFacts(2540000, "Abd al-Rahman al-Sufi", 964,
                     "Described as a little cloud six centuries before the "
                     "telescope."),
    32: DeepSkyFacts(2490000, "Guillaume Le Gentil", 1749),
    33: DeepSkyFacts(2730000, "Giovanni Battista Hodierna", 1654),
    34: DeepSkyFacts(1500, "Giovanni Battista Hodierna", 1654),
    35: DeepSkyFacts(2800, "Philippe Loys de Cheseaux", 1745),
    36: DeepSkyFacts(4100, "Giovanni Battista Hodierna", 1654),
    37: DeepSkyFacts(4500, "Giovanni Battista Hodierna", 1654),
    38: DeepSkyFacts(4200, "Giovanni Battista Hodierna", 1654),
    39: DeepSkyFacts(800, "Guillaume Le Gentil", 1750),
    41: DeepSkyFacts(2300, "Giovanni Battista Hodierna", 1654),
    42: DeepSkyFacts(1344, "Nicolas-Claude Fabri de Peiresc", 1610,
                     "A star nursery lit by the four stars of the Trapezium."),
    43: DeepSkyFacts(1600, "Jean-Jacques Dortous de Mairan", 1731),
    44: DeepSkyFacts(577, "Ptolemy", 130,
                     "Naked-eye since antiquity; Galileo first resolved it."),
    45: DeepSkyFacts(444, None, None,
                     "Named in the Iliad and in Job. Around 100 million years "
                     "old."),
    46: DeepSkyFacts(5400, "Charles Messier", 1771),
    47: DeepSkyFacts(1600, "Giovanni Battista Hodierna", 1654),
    48: DeepSkyFacts(1500, "Charles Messier", 1771),
    50: DeepSkyFacts(3200, "Charles Messier", 1772),
    51: DeepSkyFacts(23000000, "Charles Messier", 1773,
                     "The first object recognised as having spiral structure."),
    52: DeepSkyFacts(5000, "Charles Messier", 1774),
    53: DeepSkyFacts(58000, "Johann Elert Bode", 1775),
    54: DeepSkyFacts(87400, "Charles Messier", 1778,
                     "Not one of ours: it belongs to the Sagittarius dwarf "
                     "galaxy."),
    55: DeepSkyFacts(17600, "Nicolas-Louis de Lacaille", 1752),
    56: DeepSkyFacts(32900, "Charles Messier", 1779),
    57: DeepSkyFacts(2300, "Antoine Darquier de Pellepoix", 1779,
                     "A dying star's shed outer layers, seen down the barrel."),
    62: DeepSkyFacts(22500, "Charles Messier", 1771),
    63: DeepSkyFacts(29300000, "Pierre Mechain", 1779),
    64: DeepSkyFacts(17300000, "Edward Pigott", 1779,
                     "The dust lane is collision debris, and its gas orbits "
                     "backwards."),
    65: DeepSkyFacts(35000000, "Pierre Mechain", 1780),
    66: DeepSkyFacts(36000000, "Pierre Mechain", 1780),
    67: DeepSkyFacts(2700, "Johann Gottfried Koehler", 1779,
                     "One of the oldest open clusters known, at some four "
                     "billion years."),
    68: DeepSkyFacts(33600, "Charles Messier", 1780),
    69: DeepSkyFacts(29700, "Nicolas-Louis de Lacaille", 1752),
    70: DeepSkyFacts(29400, "Charles Messier", 1780),
    71: DeepSkyFacts(13000, "Philippe Loys de Cheseaux", 1745),
    72: DeepSkyFacts(55400, "Pierre Mechain", 1780),
    75: DeepSkyFacts(67500, "Pierre Mechain", 1780),
    77: DeepSkyFacts(47000000, "Pierre Mechain", 1780,
                     "A Seyfert galaxy: an active black hole at its centre."),
    78: DeepSkyFacts(1600, "Pierre Mechain", 1780),
    79: DeepSkyFacts(41000, "Pierre Mechain", 1780),
    80: DeepSkyFacts(32600, "Charles Messier", 1781),
    81: DeepSkyFacts(11800000, "Johann Elert Bode", 1774),
    82: DeepSkyFacts(11500000, "Johann Elert Bode", 1774,
                     "A starburst galaxy, stirred up by M81 passing close."),
    83: DeepSkyFacts(14700000, "Nicolas-Louis de Lacaille", 1752),
    87: DeepSkyFacts(53500000, "Charles Messier", 1781,
                     "Its black hole was the first ever photographed, in 2019."),
    92: DeepSkyFacts(26700, "Johann Elert Bode", 1777),
    93: DeepSkyFacts(3600, "Charles Messier", 1781),
    94: DeepSkyFacts(16000000, "Pierre Mechain", 1781),
    95: DeepSkyFacts(33000000, "Pierre Mechain", 1781),
    96: DeepSkyFacts(31000000, "Pierre Mechain", 1781),
    97: DeepSkyFacts(2030, "Pierre Mechain", 1781),
    101: DeepSkyFacts(20900000, "Pierre Mechain", 1781),
    103: DeepSkyFacts(8500, "Pierre Mechain", 1781),
    104: DeepSkyFacts(29300000, "Pierre Mechain", 1781),
    106: DeepSkyFacts(23500000, "Pierre Mechain", 1781),
    107: DeepSkyFacts(20900, "Pierre Mechain", 1782),
    108: DeepSkyFacts(46000000, "Pierre Mechain", 1781),
    109: DeepSkyFacts(83500000, "Pierre Mechain", 1781),
    110: DeepSkyFacts(2690000, "Charles Messier", 1773),
}


#: The same, for showpieces Messier never catalogued, keyed by OpenNGC
#: identifier. Mostly William and Caroline Herschel, who between them swept up
#: the bulk of what became the NGC, plus the southern sky that Messier could
#: not see from Paris.
NGC_FACTS: dict[str, DeepSkyFacts] = {
    "C014": DeepSkyFacts(7500, "Hipparchus", -130,
                         "Two clusters genuinely side by side, not a chance "
                         "alignment."),
    "NGC0869": DeepSkyFacts(7500, "Hipparchus", -130),
    "NGC0884": DeepSkyFacts(7600, "Hipparchus", -130),
    "Cl399": DeepSkyFacts(None, "Abd al-Rahman al-Sufi", 964,
                          "A chance alignment, not a cluster: the stars lie "
                          "at quite different distances."),
    "Mel111": DeepSkyFacts(280, "Ptolemy", 130),
    "NGC0457": DeepSkyFacts(7900, "William Herschel", 1787),
    "NGC0752": DeepSkyFacts(1500, "Caroline Herschel", 1783),
    "NGC0663": DeepSkyFacts(6900, "William Herschel", 1787),
    "NGC7789": DeepSkyFacts(7600, "Caroline Herschel", 1783),
    "NGC6231": DeepSkyFacts(5900, "Giovanni Battista Hodierna", 1654),
    "NGC2264": DeepSkyFacts(2600, "William Herschel", 1784),
    "NGC0104": DeepSkyFacts(13000, "Nicolas-Louis de Lacaille", 1751,
                            "Second only to Omega Centauri among globulars."),
    "NGC5139": DeepSkyFacts(17090, "Edmond Halley", 1677,
                            "Catalogued as a star by Ptolemy; probably the "
                            "core of a swallowed dwarf galaxy."),
    "NGC7000": DeepSkyFacts(2600, "William Herschel", 1786),
    "NGC6960": DeepSkyFacts(2400, "William Herschel", 1784,
                            "Part of a supernova remnant some 10,000 years old."),
    "NGC6992": DeepSkyFacts(2400, "William Herschel", 1784),
    "NGC7293": DeepSkyFacts(655, "Karl Ludwig Harding", 1824,
                            "The nearest bright planetary nebula."),
    "NGC2392": DeepSkyFacts(6500, "William Herschel", 1787),
    "NGC1499": DeepSkyFacts(1000, "Edward Emerson Barnard", 1884),
    # Discovery credit deliberately absent. The cluster inside the nebula and
    # the nebulosity around it were found separately and the NGC splits the
    # Rosette across four entries, so no single name and date is defensible.
    "NGC2238": DeepSkyFacts(5200, None, None),
    "NGC7635": DeepSkyFacts(7100, "William Herschel", 1787),
    "NGC6543": DeepSkyFacts(3300, "William Herschel", 1786,
                            "The first nebula whose spectrum was taken, in 1864."),
    "NGC6826": DeepSkyFacts(2000, "William Herschel", 1793,
                            "Look straight at it and it fades; look aside and "
                            "it returns."),
    "NGC6888": DeepSkyFacts(5000, "William Herschel", 1792),
    "NGC7009": DeepSkyFacts(5200, "William Herschel", 1782),
    "NGC7662": DeepSkyFacts(5600, "William Herschel", 1784),
    "NGC3242": DeepSkyFacts(4800, "William Herschel", 1785),
    "NGC6302": DeepSkyFacts(3400, "Edward Emerson Barnard", 1880),
    "NGC7023": DeepSkyFacts(1300, "William Herschel", 1794),
    "NGC1977": DeepSkyFacts(1500, "William Herschel", 1786),
    "NGC2261": DeepSkyFacts(2500, "William Herschel", 1783,
                            "It visibly changes shape over weeks."),
    "NGC2359": DeepSkyFacts(12000, "William Herschel", 1785),
    "NGC2070": DeepSkyFacts(160000, "Nicolas-Louis de Lacaille", 1751,
                            "In another galaxy, and still the largest star "
                            "nursery known."),
    "NGC3372": DeepSkyFacts(7500, "Nicolas-Louis de Lacaille", 1751),
    "NGC2438": DeepSkyFacts(2900, "William Herschel", 1786,
                            "Sits in front of M46 by chance; it is not a "
                            "member."),
    "NGC0253": DeepSkyFacts(11400000, "Caroline Herschel", 1783),
    "NGC5128": DeepSkyFacts(12000000, "James Dunlop", 1826,
                            "A giant elliptical that swallowed a spiral; the "
                            "dust lane is what is left."),
    "NGC4565": DeepSkyFacts(42000000, "William Herschel", 1785),
    "NGC4631": DeepSkyFacts(30000000, "William Herschel", 1787),
    "NGC4656": DeepSkyFacts(30000000, "William Herschel", 1787),
    "NGC0891": DeepSkyFacts(30000000, "William Herschel", 1784),
    "NGC2903": DeepSkyFacts(30000000, "William Herschel", 1784,
                            "Often called the best galaxy Messier missed."),
    "NGC3628": DeepSkyFacts(35000000, "William Herschel", 1784),
    "NGC7331": DeepSkyFacts(40000000, "William Herschel", 1784),
    "NGC6946": DeepSkyFacts(25200000, "William Herschel", 1798,
                            "Ten supernovae seen in it since 1917, more than "
                            "any other galaxy."),
    "NGC4038": DeepSkyFacts(45000000, "William Herschel", 1785,
                            "Two galaxies mid-collision."),
    "NGC0292": DeepSkyFacts(200000, None, None,
                            "Naked-eye from the southern hemisphere, and "
                            "known there long before Magellan."),
    "ESO056-115": DeepSkyFacts(163000, None, None,
                               "A satellite galaxy of our own, and the host "
                               "of the 1987 supernova."),
}


def deep_sky_facts(name: str, messier: int | None) -> DeepSkyFacts | None:
    """Reference data for a catalogue object, or None when there is none.

    Messier number first, since it is the stabler key: an object's OpenNGC
    identifier can change between releases while M13 stays M13.
    """
    if messier is not None and messier in MESSIER_FACTS:
        return MESSIER_FACTS[messier]
    return NGC_FACTS.get(name)
