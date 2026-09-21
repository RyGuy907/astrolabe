"""What a star is: the card the sky chart shows when you click one.

Everything here is derived from four catalogue numbers per star -- distance,
spectral type, B-V colour and absolute magnitude, vendored from HYG into
`stars.csv` by `scripts/fetch_star_chart_data.py` -- plus a short curated
table for famous stars. Nothing is fetched and nothing depends on the site or
the night, so a profile is a pure function of the star.

How far each number can be trusted differs, and the card says so rather than
printing every figure with the same confidence:

* **Distance** is measured (Hipparcos parallax). Good to a few per cent
  within ~150 pc; by 500 pc the parallax is a couple of milliarcseconds and
  its error a sizeable fraction of it, so beyond that it is marked rough.
* **Type and temperature** come from the spectral type, which is measured.
  Where the type is missing or unparseable, temperature falls back to the
  B-V colour (Ballesteros 2012), which is fine for cool stars and saturates
  for hot ones.
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

#: Main-sequence effective temperature at subclass 0 of each spectral class
#: and the next class's 0, for interpolating by subclass (Pecaut & Mamajek
#: 2013, rounded).
_CLASS_TEMPS = {"O": (44900, 31400), "B": (31400, 9700), "A": (9700, 7220),
                "F": (7220, 5920), "G": (5920, 5280), "K": (5280, 3850),
                "M": (3850, 2400)}

#: Cool giants and supergiants are 500-900 K cooler than dwarfs of the same
#: type, enough to matter for the radius, which goes as 1/T^2. Kelvin at
#: points along G0..M8, as (classes past G0, temperature); from interferometric
#: giant temperatures (van Belle et al. 1999, Alonso et al. 1999), rounded.
_GIANT_TEMPS = [(0, 5600), (5, 5100), (10, 4750), (12, 4400), (13, 4250),
                (15, 3950), (20, 3850), (22, 3650), (25, 3350), (28, 2900)]

_COLOURS = {"O": "Blue", "B": "Blue-white", "A": "White", "F": "Yellow-white",
            "G": "Yellow", "K": "Orange", "M": "Red"}

#: Famous stars whose age has been published, by Hipparcos number. Ages of
#: single stars come from models and disagree between studies, so these are
#: given as the round figure or range most often quoted. `spect` overrides a
#: catalogue type that is wrong or unusable (HYG has Capella as "M1: comp").
_KNOWN: dict[int, dict[str, str]] = {
    32349: {"age": "about 230 million years", "spect": "A1V"},            # Sirius
    91262: {"age": "about 450 million years"},                            # Vega
    27989: {"age": "about 8–10 million years"},                           # Betelgeuse
    24436: {"age": "about 8 million years"},                              # Rigel
    80763: {"age": "about 11 million years"},                             # Antares
    69673: {"age": "about 7 billion years"},                              # Arcturus
    21421: {"age": "about 6.5 billion years"},                            # Aldebaran
    11767: {"age": "roughly 50–70 million years"},                        # Polaris
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


@functools.lru_cache(maxsize=1)
def _rows() -> tuple[tuple[str, ...], ...]:
    """The star file's rows, in file (= chart index) order."""
    with open(DATA / "stars.csv", encoding="utf-8") as fh:
        reader = csv.reader(fh, delimiter=";")
        next(reader)
        return tuple(tuple(r) for r in reader)


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
    hot, cool = _CLASS_TEMPS[cls]
    # Interpolate in log T: the scale is closer to geometric than linear.
    return math.exp(math.log(hot) + (math.log(cool) - math.log(hot)) * sub / 10)


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

    parsed = parse_spectral_type(spect) if spect else None
    cls, sub, lum = parsed if parsed else (None, None, None)
    if cls in ("G", "K", "M") and lum in ("I", "II", "III"):
        temp = giant_temperature(cls, sub)
    elif cls in _CLASS_TEMPS:
        temp = temperature_from_type(cls, sub)
    elif lum == "D":
        temp = None                      # white-dwarf temperatures need the subtype
    elif ci:
        temp = temperature_from_colour(float(ci))
    else:
        temp = None

    distance_ly = quality = None
    if dist_pc:
        pc = float(dist_pc)
        distance_ly = _round_sig(pc * LY_PER_PC, 3 if pc < 150 else 2)
        quality = "precise" if pc < 150 else "approximate" if pc < 500 else "rough"

    luminosity = radius = None
    if absmag and temp and lum != "D":
        mbol = float(absmag) + bolometric_correction(temp)
        luminosity = 10 ** (-0.4 * (mbol - SUN_MBOL))
        radius = math.sqrt(luminosity) * (SUN_TEMP_K / temp) ** 2

    age = basis = None
    if known and "age" in known:
        age, basis = known["age"], "published"
    elif lum == "I":
        # Supergiants start at 8-10 solar masses and live tens of millions of years.
        age, basis = "under about 50 million years", "upper limit"
    elif lum in ("V", "IV") and luminosity and cls in ("O", "B", "A"):
        # Main-sequence lifetime ~ 10 Gyr (M/Msun)^-2.5 and L ~ M^3.5, so
        # ~ 10 Gyr L^-0.71. Whatever its age, it is less than that.
        life = 10e9 * luminosity ** (-2.5 / 3.5)
        if life < 3e9:
            age, basis = f"under about {_years(life)}", "upper limit"

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
    )


def _years(y: float) -> str:
    if y >= 1e9:
        return f"{_round_sig(y / 1e9, 1):g} billion years"
    return f"{_round_sig(y / 1e6, 1):g} million years"
