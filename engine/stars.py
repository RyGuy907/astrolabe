"""What a star is: the card the sky chart shows when you click one.

Each figure comes from the best source that reaches the star, and the card
says which:

* **Measured**: an angular diameter from interferometry or a lunar
  occultation (JMDC), which with a distance *is* the radius. ~1,000 stars,
  mostly the bright ones people click.
* **Gaia DR3**: parallax (when its fit is sound -- RUWE < 1.4 -- and the
  parallax is five times its error or better), spectroscopic temperature,
  and for F-M stars FLAME radius and luminosity. Both vendored into
  `star_details.csv` by `scripts/fetch_star_details.py`.
* **Estimated**, the fallback, from four HYG numbers per star -- distance,
  spectral type, B-V colour, absolute magnitude, in `stars.csv` -- as below.

Plus a short curated table for famous stars' ages. Nothing is fetched here
and nothing depends on the site or the night, so a profile is a pure
function of the star.

The rest of this note is about the estimated path.

How far each number can be trusted differs, and the card says so rather than
printing every figure with the same confidence:

* **Distance** is measured (Hipparcos parallax). Good to a few per cent
  within ~150 pc; by 500 pc the parallax is a couple of milliarcseconds and
  its error a sizeable fraction of it, so beyond that it is marked rough.
* **Type and temperature** come from the spectral type, which is measured.
  For F and G stars short of supergiants the B-V colour (Ballesteros 2012)
  gives the temperature instead: checked against Gaia DR3 and PASTEL's
  spectroscopic temperatures it is the closer of the two there. Not for A
  stars: colour looked better against PASTEL's (mostly faint, distant) A
  stars only because dust reddening cancelled the colour fit's ~6% hot bias;
  on bright, unreddened A stars the type wins, 74% within 10% of published
  values against 56%. The type doesn't depend on the dust. For K stars and
  anything hotter than F the type wins too (colour saturates for hot stars).
  About a third of catalogue types carry no luminosity class
  ("K0"); a G, K or M star of that kind shining more than two magnitudes
  above the main sequence for its type is taken to be a giant. Treating
  those as dwarfs made them too hot and a third too small against Gaia.
  One within a magnitude of the main sequence is taken to be a dwarf; see
  `_infer_class`.
* **Size** is estimated: bolometric luminosity (absolute magnitude plus a
  temperature-dependent bolometric correction, Torres 2010) and temperature
  give the radius through L = 4 pi R^2 sigma T^4. Rounded, and shown as an
  estimate -- a few tens of per cent either way is typical.
* **Age** cannot be read off a single star's light. Two honest cases remain:
  a published age for a handful of famous stars, and an upper bound for hot
  main-sequence stars and supergiants, which cannot be old because stars that
  massive do not last. Everything else says it is not known.
"""

from __future__ import annotations

import csv
import functools
import math
import re
from dataclasses import dataclass
from pathlib import Path

DATA = Path(__file__).resolve().parent / "catalog" / "data"

LY_PER_PC = 3.26156
SUN_TEMP_K = 5772
SUN_MBOL = 4.74

#: Main-sequence effective temperature by type (Pecaut & Mamajek 2013,
#: rounded), as (position along O0..M9, kelvin). Point by point: interpolating
#: a whole class from its ends put late-B stars 1,500-2,500 K too hot, which
#: the audit against published temperatures caught (Regulus, B7V, at 13,800 K
#: against a measured ~12,000).
_MS_TEMPS = [(3, 44900), (5, 41400), (7, 36100), (10, 31400), (11, 26000),
             (12, 20600), (13, 17000), (15, 15700), (16, 14500), (17, 14000),
             (18, 12300), (19, 10700), (20, 9700), (21, 9300), (22, 8800),
             (23, 8600), (25, 8100), (27, 7650), (30, 7220), (32, 6810),
             (35, 6510), (38, 6170), (40, 5920), (42, 5770), (45, 5660),
             (48, 5490), (50, 5280), (52, 4990), (53, 4830), (55, 4450),
             (57, 4050), (60, 3850), (62, 3560), (63, 3430), (64, 3210),
             (65, 3060), (66, 2810), (67, 2680), (68, 2570), (69, 2380)]

#: Cool giants and supergiants are 500-900 K cooler than dwarfs of the same
#: type, enough to matter for the radius, which goes as 1/T^2. Kelvin at
#: points along G0..M8, as (classes past G0, temperature); from interferometric
#: giant temperatures (van Belle et al. 1999, Alonso et al. 1999), rounded.
_GIANT_TEMPS = [(0, 5600), (5, 5100), (10, 4750), (12, 4400), (13, 4250),
                (15, 3950), (20, 3850), (22, 3650), (25, 3350), (28, 2900)]

#: Main-sequence absolute visual magnitude along the types (Pecaut & Mamajek
#: 2013, rounded), for telling a giant from a dwarf when the type doesn't say.
_MS_MV = [("O", 5, -5.0), ("B", 0, -3.5), ("B", 5, -1.1), ("A", 0, 1.1), ("A", 5, 1.9),
          ("F", 0, 2.5), ("F", 5, 3.4), ("G", 0, 4.4), ("G", 5, 5.1), ("K", 0, 5.9),
          ("K", 5, 7.4), ("M", 0, 8.9), ("M", 5, 12.3)]
_ORDER = "OBAFGKM"

_COLOURS = {"O": "Blue", "B": "Blue-white", "A": "White", "F": "Yellow-white",
            "G": "Yellow", "K": "Orange", "M": "Red"}

#: Famous stars whose age has been published, by Hipparcos number. Ages of
#: single stars come from models and disagree between studies, so these are
#: given as the round figure or range most often quoted. `spect` overrides a
#: catalogue type that is wrong or unusable (HYG has Capella as "M1: comp").
_KNOWN: dict[int, dict[str, str]] = {
    32349: {"age": "about 230–250 million years", "spect": "A1V"},        # Sirius
    91262: {"age": "about 450–700 million years"},                        # Vega
    27989: {"age": "about 8–14 million years"},                           # Betelgeuse
    24436: {"age": "about 8 million years"},                              # Rigel
    80763: {"age": "about 11–15 million years"},                          # Antares
    69673: {"age": "about 7 billion years"},                              # Arcturus
    21421: {"age": "about 6.5 billion years"},                            # Aldebaran
    11767: {"age": "roughly 45–70 million years"},                        # Polaris
    24608: {"age": "about 590 million years", "spect": "G3III"},          # Capella
    37279: {"age": "about 1.9 billion years"},                            # Procyon
    97649: {"age": "about 100 million years"},                            # Altair
    65474: {"age": "about 12 million years"},                             # Spica
    113368: {"age": "about 440 million years"},                           # Fomalhaut
    71683: {"age": "about 5–6 billion years"},                            # Rigil Kentaurus
    71681: {"age": "about 5–6 billion years"},                            # Toliman
    37826: {"age": "about 720 million years"},                            # Pollux
    65378: {"age": "about 400 million years, with the Ursa Major group"}, # Mizar
    8102: {"age": "several billion years, likely older than the Sun"},    # Tau Ceti
    16537: {"age": "about 400–800 million years"},                        # Ran (eps Eri)
    # Added from the audit against published values (see tests/test_stars.py).
    54061: {"age": "about 280 million years", "spect": "K0III"},          # Dubhe; HYG has its companion's F7V
    7588: {"age": "about 60 million years", "spect": "B6Vpe"},            # Achernar; HYG's B3 runs 7,000 K hot
    62956: {"age": "about 300 million years"},                            # Alioth
    9884: {"age": "about 3.4 billion years"},                             # Hamal
    46390: {"age": "about 420 million years"},                            # Alphard
    72607: {"age": "about 3 billion years"},                              # Kochab
    86032: {"age": "about 770 million years"},                            # Rasalhague
    87833: {"age": "about 1.3 billion years"},                            # Eltanin
    25336: {"age": "about 25–30 million years"},                          # Bellatrix
    15863: {"age": "about 40 million years"},                             # Mirfak
    102098: {"age": "about 12 million years"},                            # Deneb
    30438: {"age": "about 30 million years"},                             # Canopus
    107315: {"age": "about 20 million years"},                            # Enif
    36850: {"age": "about 290 million years"},                            # Castor
    14576: {"age": "about 570 million years"},                            # Algol
    # Older than a star its mass should live: it took mass from a companion,
    # now a white dwarf. No single-star bound would hold.
    49669: {"age": "at least 1 billion years, rejuvenated by a companion"},  # Regulus
}


@dataclass(frozen=True)
class StarProfile:
    index: int
    hip: int | None             # Hipparcos number
    name: str | None            # proper name or Bayer designation
    magnitude: float
    spectral_type: str | None
    kind: str                   # "Red supergiant", "Yellow dwarf", ...
    colour: str | None
    temperature_k: int | None
    distance_ly: float | None
    distance_quality: str | None    # "precise" / "approximate" / "rough"
    luminosity_sun: float | None    # bolometric, estimated
    radius_sun: float | None        # estimated
    age: str | None
    age_basis: str | None           # "published" / "upper limit" / None
    # Where each figure came from: "measured", "gaia", "hipparcos" or
    # "estimated" (temperature: "spectrum", "type" or "colour").
    distance_source: str | None = None
    radius_source: str | None = None
    temperature_source: str | None = None
    luminosity_source: str | None = None
    # Set when the catalogue's spectral type was set aside (see below).
    type_note: str | None = None


@functools.lru_cache(maxsize=1)
def _rows() -> tuple[tuple[str, ...], ...]:
    """The star file's rows, in file (= chart index) order."""
    with open(DATA / "stars.csv", encoding="utf-8") as fh:
        reader = csv.reader(fh, delimiter=";")
        next(reader)
        return tuple(tuple(r) for r in reader)


@functools.lru_cache(maxsize=1)
def _details() -> dict[int, dict[str, str]]:
    """Gaia DR3 and measured diameters by Hipparcos number, where there are any."""
    path = DATA / "star_details.csv"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as fh:
        return {int(r["hip"]): r for r in csv.DictReader(fh, delimiter=";")}


#: Solar radii per (AU): an angle in arcseconds times a distance in parsecs
#: is a length in AU.
RSUN_PER_AU = 215.032
#: Gaia's astrometric fit is sound below this renormalised unit weight error.
RUWE_LIMIT = 1.4


def star_count() -> int:
    return len(_rows())


_SPECT = re.compile(
    r"^\s*(?:sd)?(?P<cls>[OBAFGKM])(?P<sub>\d(?:\.\d+)?)?[:\s]*"
    r"(?P<lum>0-Ia|Ia0|Ia\+|Iab|Ia|Ib|III|II|IV|VI|V|I)?")


def parse_spectral_type(spect: str) -> tuple[str, float, str | None] | None:
    """(class letter, subclass, luminosity class) from a type like "K2IIIp".

    The luminosity class is folded to I, II, III, IV, V or D. White dwarfs
    ("DA2") and types this does not read return what they can, or None.
    """
    spect = spect.strip()
    if spect.startswith("D") and len(spect) > 1 and spect[1] in "ABOQZCX":
        return ("D", 0.0, "D")
    m = _SPECT.match(spect)
    if not m:
        return None
    sub = float(m["sub"]) if m["sub"] else 5.0
    lum = m["lum"]
    if lum:
        lum = "I" if lum.startswith(("0", "I")) and lum not in ("II", "III", "IV") else lum
        lum = "V" if lum == "VI" else lum
    elif re.match(r"^[AF]\d?(\.\d+)?m", spect):
        lum = "V"       # metallic-line A and F stars are main-sequence
    return (m["cls"], sub, lum)


def temperature_from_type(cls: str, sub: float) -> float:
    """Main-sequence effective temperature for a type like ("B", 7)."""
    x = "OBAFGKM".index(cls) * 10 + sub
    return _interpolate(_MS_TEMPS, x)


def _interpolate(points: list[tuple[float, float]], x: float) -> float:
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


def type_from_temperature(temp: float) -> tuple[str, float]:
    """The main-sequence type whose temperature this is: (class, subclass)."""
    pts = _MS_TEMPS
    if temp >= pts[0][1]:
        x = pts[0][0]
    elif temp <= pts[-1][1]:
        x = pts[-1][0]
    else:
        x = next(x0 + (x1 - x0) * (t0 - temp) / (t0 - t1)
                 for (x0, t0), (x1, t1) in zip(pts, pts[1:]) if t1 <= temp <= t0)
    return "OBAFGKM"[min(6, int(x // 10))], round(x % 10, 1)


def giant_temperature(cls: str, sub: float) -> float:
    """Effective temperature of a G, K or M giant or supergiant by type."""
    x = {"G": 0, "K": 10, "M": 20}[cls] + sub
    pts = _GIANT_TEMPS
    if x <= pts[0][0]:
        return pts[0][1]
    for (x0, t0), (x1, t1) in zip(pts, pts[1:]):
        if x <= x1:
            return t0 + (t1 - t0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


def main_sequence_mv(cls: str, sub: float) -> float:
    """Absolute V of a main-sequence star of this type, interpolated."""
    x = _ORDER.index(cls) * 10 + sub
    pts = [(_ORDER.index(c) * 10 + s, m) for c, s, m in _MS_MV]
    if x <= pts[0][0]:
        return pts[0][1]
    for (x0, m0), (x1, m1) in zip(pts, pts[1:]):
        if x <= x1:
            return m0 + (m1 - m0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


#: Main-sequence lifetime by type, as (position along O0..A9, years): the
#: lifetime for the type's mass (Pecaut & Mamajek 2013 masses; Ekstrom et al.
#: 2012 tracks), padded by half again, since type and mass scatter -- and
#: for mid and late B by a further third, since fast rotators, common among
#: them, live about a quarter longer (Merope, B6IV, is published at 212 Myr
#: against an unpadded ~150).
_MS_LIFETIME = [(5, 15e6), (10, 20e6), (12, 60e6), (15, 200e6), (18, 500e6),
                (20, 1e9), (25, 2e9)]


def main_sequence_lifetime(cls: str, sub: float) -> float:
    """Upper bound on a hot main-sequence star's age, by type, in years."""
    x = "OBA".index(cls) * 10 + sub
    # Geometric: lifetimes span two orders of magnitude across these types.
    logs = [(px, math.log(y)) for px, y in _MS_LIFETIME]
    return math.exp(_interpolate(logs, x))


def _infer_class(cls: str | None, sub: float | None, lum: str | None,
                 absmag: str | None) -> str | None:
    """A luminosity class for a type that doesn't give one, from how bright
    the star really is against the main sequence for its type.

    Two magnitudes or more above it is a giant (G, K and M only: hotter
    stars' giants and dwarfs are too close in brightness to tell apart this
    way). Within a magnitude of it is a main-sequence star -- the case that
    left a third of real dwarfs, per PASTEL's surface gravities, described
    as just "a yellow star". In between, subgiants and binaries, it stays
    unsaid.
    """
    if lum is not None or not cls or cls not in _ORDER or not absmag:
        return lum
    above = main_sequence_mv(cls, sub) - float(absmag)
    if above >= 2.0 and cls in ("G", "K", "M"):
        return "III"
    if abs(above) <= 1.0 and cls in ("A", "F", "G", "K", "M"):
        return "V"
    return None


def temperature_from_colour(bv: float) -> float:
    """Ballesteros (2012): black-body fit to B-V, good for ~3,000-10,000 K."""
    bv = max(-0.4, min(2.0, bv))
    return 4600 * (1 / (0.92 * bv + 1.7) + 1 / (0.92 * bv + 0.62))


def bolometric_correction(temp_k: float) -> float:
    """BC_V from effective temperature: Flower (1996), as corrected by Torres
    (2010). Negative everywhere but near 6,500 K: hot stars put their light
    into the ultraviolet, cool ones into the infrared, both unseen in V."""
    lt = math.log10(temp_k)
    if lt < 3.70:
        c = (-0.190537291496456e5, 0.155144866764412e5, -0.421278819301717e4,
             0.381476328422343e3)
    elif lt < 3.90:
        c = (-0.370510203809015e5, 0.385672629965804e5, -0.150651486316025e5,
             0.261724637119416e4, -0.170623810323864e3)
    else:
        c = (-0.118115450538963e6, 0.137145973583929e6, -0.636233812100225e5,
             0.147412923562646e5, -0.170587278406872e4, 0.788731721804990e2)
    return sum(k * lt ** i for i, k in enumerate(c))


def _kind(cls: str | None, lum: str | None) -> str:
    if lum == "D":
        return "White dwarf"
    colour = _COLOURS.get(cls or "")
    if colour is None:
        return "Star"
    noun = {
        "I": "supergiant", "II": "bright giant", "III": "giant", "IV": "subgiant",
        "V": {"G": "dwarf, like the Sun", "K": "dwarf", "M": "dwarf"}.get(cls or "",
                                                                      "main-sequence star"),
    }.get(lum or "", "star")
    return f"{colour} {noun}"


def _round_sig(x: float, sig: int = 2) -> float:
    if x <= 0:
        return x
    return round(x, sig - 1 - int(math.floor(math.log10(x))))


def star_profile(index: int) -> StarProfile:
    """The card for the star at `index` in the chart's catalogue."""
    rows = _rows()
    if not 0 <= index < len(rows):
        raise IndexError(index)
    ra, dec, mag, label, hip, dist_pc, spect, ci, absmag = (list(rows[index]) + [""] * 9)[:9]
    known = _KNOWN.get(int(hip)) if hip else None
    spect = (known or {}).get("spect", spect) or ""

    extra = _details().get(int(hip), {}) if hip else {}
    num = lambda k: float(extra[k]) if extra.get(k) else None

    # Distance: Gaia where its astrometry is sound, else Hipparcos.
    pc = quality = dist_source = None
    plx, plx_err, ruwe = num("plx"), num("plx_err"), num("ruwe")
    if plx and plx_err and plx > 0 and ruwe is not None and ruwe < RUWE_LIMIT \
            and plx / plx_err >= 5:
        pc, dist_source = 1000 / plx, "gaia"
        quality = "precise" if plx / plx_err >= 20 else "approximate"
    elif dist_pc:
        pc, dist_source = float(dist_pc), "hipparcos"
        quality = "precise" if pc < 150 else "approximate" if pc < 500 else "rough"
    distance_ly = _round_sig(pc * LY_PER_PC, 3 if pc < 150 else 2) if pc else None
    # Absolute magnitude from the distance actually used.
    if pc and mag:
        absmag = str(float(mag) - 5 * math.log10(pc / 10))

    parsed = parse_spectral_type(spect) if spect else None
    cls, sub, lum = parsed if parsed else (None, None, None)
    lum = _infer_class(cls, sub, lum, absmag)
    # A hot type on a plainly orange or red star: the type is a companion's.
    # Double stars are catalogued with one type, and sometimes it is the
    # fainter, hotter partner's -- Almach, an orange giant with B-V 1.37,
    # is listed B8V. The colour is the light actually seen, so it wins, and
    # the type is re-derived from it. Not for supergiants and bright giants,
    # which are distant enough that dust reddens genuinely hot stars.
    type_note = None
    if cls in ("O", "B", "A") and lum not in ("I", "II") and ci and float(ci) > 0.7:
        type_note = (f"The catalogue's type, {spect}, doesn't match this star's "
                     "colour; it likely belongs to a companion.")
        cls, sub = type_from_temperature(temperature_from_colour(float(ci)))
        lum = _infer_class(cls, sub, None, absmag)
    temp_source = "type"
    if cls in ("F", "G") and lum != "I" and ci:
        temp, temp_source = temperature_from_colour(float(ci)), "colour"
    elif cls in ("G", "K", "M") and lum in ("I", "II", "III"):
        temp = giant_temperature(cls, sub)
    elif cls in _COLOURS:
        temp = temperature_from_type(cls, sub)
        if lum == "I" and cls == "O":
            temp *= 0.85
        elif lum == "I" and cls == "B":
            # Hot supergiants run cooler than dwarfs of their type: ~18% at
            # B0 (Alnilam, B0Ia, is ~26,000 K, not 31,400), fading to nothing
            # by late B (Rigel, B8Ia, matches the dwarf scale).
            temp *= 0.82 + 0.18 * min(1.0, sub / 8)
    elif lum == "D":
        temp = None                      # white-dwarf temperatures need the subtype
    elif ci:
        temp, temp_source = temperature_from_colour(float(ci)), "colour"
    else:
        temp = None

    # Gaia's spectroscopic temperature, for the cool stars it covers well
    # (its hot-star temperatures run thousands of kelvin low).
    if num("teff_spec") and cls not in ("O", "B", "A"):
        temp, temp_source = num("teff_spec"), "spectrum"


    est_radius = est_lum = None
    if absmag and temp and lum != "D":
        mbol = float(absmag) + bolometric_correction(temp)
        est_lum = 10 ** (-0.4 * (mbol - SUN_MBOL))
        est_radius = math.sqrt(est_lum) * (SUN_TEMP_K / temp) ** 2
    gaia_radius = num("radius_flame") if cls in ("F", "G", "K", "M") and \
        dist_source == "gaia" else None

    luminosity = radius = None
    radius_source = lum_source = None
    measured = None
    if extra.get("diam_mas") and pc:
        # Half the angle (arcsec) times the distance (pc) is a length in AU.
        measured = num("diam_mas") / 2 / 1000 * pc * RSUN_PER_AU
        # One measurement alone, far from every other route to the size, is
        # more likely the odd one out: against Gaia, single diameters agree
        # within 25% for 81% of stars, repeated ones for 92%; the wide audit
        # found gamma Lib and Alcyone twice their published size on one each.
        reference = gaia_radius or est_radius
        if extra.get("diam_n") == "1" and reference and \
                not 1 / 1.6 <= measured / reference <= 1.6:
            measured = None
    if measured:
        radius, radius_source = measured, "measured"
        if temp:
            luminosity = radius ** 2 * (temp / SUN_TEMP_K) ** 4
            lum_source = "measured"
    elif gaia_radius:
        radius, radius_source = gaia_radius, "gaia"
        if num("lum_flame"):
            luminosity, lum_source = num("lum_flame"), "gaia"
    elif est_radius:
        radius, luminosity = est_radius, est_lum
        radius_source = lum_source = "estimated"

    age = basis = None
    if known and "age" in known:
        age, basis = known["age"], "published"
    elif lum == "I":
        # Supergiants start at 8-10 solar masses and live tens of millions of
        # years; published ages run 5-45 Myr (Mirfak, the oldest checked, 41).
        age, basis = "likely under about 60 million years", "upper limit"
    elif lum in ("V", "IV") and cls in ("O", "B", "A") and \
            "OBA".index(cls) * 10 + sub <= 25:
        # A hot main-sequence star can be no older than its type's lifetime.
        # Taken from the type, which is measured, rather than the luminosity:
        # a first version went through L and the mass-luminosity relation,
        # which overestimates the mass of the most massive stars and put
        # bounds under real ages (Achernar, Shaula). Stars that gained mass
        # from a companion (Algol, Regulus) are older still; hence "likely".
        life = main_sequence_lifetime(cls, sub)
        age, basis = f"likely under about {_years(life)}", "upper limit"

    return StarProfile(
        index=index, hip=int(hip) if hip else None, name=label or None,
        magnitude=float(mag),
        spectral_type=spect or None, kind=_kind(cls, lum),
        colour=_COLOURS.get(cls or ""),
        temperature_k=int(round(temp, -2)) if temp else None,
        distance_ly=distance_ly, distance_quality=quality,
        luminosity_sun=_round_sig(luminosity) if luminosity else None,
        radius_sun=_round_sig(radius) if radius else None,
        age=age, age_basis=basis,
        distance_source=dist_source if distance_ly else None,
        radius_source=radius_source if radius else None,
        temperature_source=temp_source if temp else None,
        luminosity_source=lum_source if luminosity else None,
        type_note=type_note,
    )


def _years(y: float) -> str:
    if y >= 1e9:
        return f"{_round_sig(y / 1e9, 1):g} billion years"
    return f"{_round_sig(y / 1e6, 1):g} million years"
