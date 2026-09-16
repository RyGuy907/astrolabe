"""Constellation names and centroids.

Two jobs:

* Map OpenNGC's abbreviation to a readable name. These are the IAU three-letter
  designations, plus OpenNGC's `Se1`/`Se2` split of Serpens into Caput and
  Cauda — Serpens is the one constellation in two disjoint pieces, so a single
  centroid for it would land in Ophiuchus, between the halves.

* Give a constellation a single position to plot. That is a real
  simplification: a constellation is an area of sky, not a point, and the ones
  with large extent (Hydra spans over 100 degrees of RA) rise and set over
  hours. The centroid curve says "roughly when this part of the sky is up",
  which is what choosing where to point is about, and the extent is reported
  alongside so a wide constellation is not read as precise.

The centroid is a **vector mean of unit vectors**, not an arithmetic mean of
RA and Dec. Averaging RA numerically puts anything straddling 0h in completely
the wrong place — Pegasus and Pisces both do.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

# IAU three-letter designations. `Se1`/`Se2` are OpenNGC's split of Serpens.
CONSTELLATION_NAMES: dict[str, str] = {
    "And": "Andromeda", "Ant": "Antlia", "Aps": "Apus", "Aql": "Aquila",
    "Aqr": "Aquarius", "Ara": "Ara", "Ari": "Aries", "Aur": "Auriga",
    "Boo": "Bootes", "Cae": "Caelum", "Cam": "Camelopardalis", "Cnc": "Cancer",
    "CVn": "Canes Venatici", "CMa": "Canis Major", "CMi": "Canis Minor",
    "Cap": "Capricornus", "Car": "Carina", "Cas": "Cassiopeia",
    "Cen": "Centaurus", "Cep": "Cepheus", "Cet": "Cetus",
    "Cha": "Chamaeleon", "Cir": "Circinus", "Col": "Columba",
    "Com": "Coma Berenices", "CrA": "Corona Australis",
    "CrB": "Corona Borealis", "Crv": "Corvus", "Crt": "Crater", "Cru": "Crux",
    "Cyg": "Cygnus", "Del": "Delphinus", "Dor": "Dorado", "Dra": "Draco",
    "Equ": "Equuleus", "Eri": "Eridanus", "For": "Fornax", "Gem": "Gemini",
    "Gru": "Grus", "Her": "Hercules", "Hor": "Horologium", "Hya": "Hydra",
    "Hyi": "Hydrus", "Ind": "Indus", "Lac": "Lacerta", "Leo": "Leo",
    "LMi": "Leo Minor", "Lep": "Lepus", "Lib": "Libra", "Lup": "Lupus",
    "Lyn": "Lynx", "Lyr": "Lyra", "Men": "Mensa", "Mic": "Microscopium",
    "Mon": "Monoceros", "Mus": "Musca", "Nor": "Norma", "Oct": "Octans",
    "Oph": "Ophiuchus", "Ori": "Orion", "Pav": "Pavo", "Peg": "Pegasus",
    "Per": "Perseus", "Phe": "Phoenix", "Pic": "Pictor", "Psc": "Pisces",
    "PsA": "Piscis Austrinus", "Pup": "Puppis", "Pyx": "Pyxis",
    "Ret": "Reticulum", "Sge": "Sagitta", "Sgr": "Sagittarius",
    "Sco": "Scorpius", "Scl": "Sculptor", "Sct": "Scutum",
    "Se1": "Serpens Caput", "Se2": "Serpens Cauda", "Sex": "Sextans",
    "Tau": "Taurus", "Tel": "Telescopium", "Tri": "Triangulum",
    "TrA": "Triangulum Australe", "Tuc": "Tucana", "UMa": "Ursa Major",
    "UMi": "Ursa Minor", "Vel": "Vela", "Vir": "Virgo", "Vol": "Volans",
    "Vul": "Vulpecula",
}


def constellation_name(abbreviation: str) -> str:
    """Readable name, falling back to the abbreviation if it is unknown."""
    return CONSTELLATION_NAMES.get(abbreviation.strip(), abbreviation.strip())


@dataclass(frozen=True)
class ConstellationPosition:
    """A constellation reduced to one point, with its spread reported."""

    abbreviation: str
    name: str
    ra_deg: float
    dec_deg: float
    member_count: int
    spread_deg: float          # greatest angular distance from the centroid

    @property
    def is_wide(self) -> bool:
        """True when a single point is a poor summary of the whole area."""
        return self.spread_deg > 20.0


def _to_vector(ra_deg: float, dec_deg: float) -> tuple[float, float, float]:
    ra, dec = math.radians(ra_deg), math.radians(dec_deg)
    return (math.cos(dec) * math.cos(ra),
            math.cos(dec) * math.sin(ra),
            math.sin(dec))


def _angular_separation(a: tuple[float, float, float],
                        b: tuple[float, float, float]) -> float:
    dot = max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b))))
    return math.degrees(math.acos(dot))


def centroid(abbreviation: str, catalog) -> ConstellationPosition | None:
    """Mean position of the catalog's members of this constellation.

    Vector mean, so constellations straddling RA 0h (Pegasus, Pisces, Cassiopeia)
    land where they belong rather than halfway round the sky.
    """
    members = [o for o in catalog
               if o.constellation.strip().lower() == abbreviation.strip().lower()]
    if not members:
        return None

    vectors = [_to_vector(o.ra_deg, o.dec_deg) for o in members]
    x = sum(v[0] for v in vectors) / len(vectors)
    y = sum(v[1] for v in vectors) / len(vectors)
    z = sum(v[2] for v in vectors) / len(vectors)

    length = math.sqrt(x * x + y * y + z * z)
    if length == 0:
        return None                      # antipodal spread; no meaningful mean
    unit = (x / length, y / length, z / length)

    ra = math.degrees(math.atan2(unit[1], unit[0])) % 360.0
    dec = math.degrees(math.asin(max(-1.0, min(1.0, unit[2]))))
    spread = max(_angular_separation(unit, v) for v in vectors)

    return ConstellationPosition(
        abbreviation=members[0].constellation.strip(),
        name=constellation_name(members[0].constellation),
        ra_deg=ra, dec_deg=dec,
        member_count=len(members),
        spread_deg=spread,
    )


# --- when is a constellation actually up -------------------------------------

VISIBLE_TONIGHT = "tonight"
VISIBLE_LATE = "late"
NOT_VISIBLE = "none"


@dataclass(frozen=True)
class ConstellationVisibility:
    """When a constellation is usable tonight, judged on its centroid."""

    abbreviation: str
    status: str                      # tonight | late | none
    start_utc: datetime | None       # first moment above the floor
    end_utc: datetime | None         # last moment above it
    hours_up: float
    peak_altitude_deg: float

    @property
    def has_gap(self) -> bool:
        """True when it dips below the floor and comes back.

        Happens for high-declination constellations whose lower culmination
        falls inside the night: Cassiopeia can be up at dusk, sink under the
        floor near lower transit, and climb back before dawn. Reporting one
        span end-to-end would overstate the time available.
        """
        if self.start_utc is None or self.end_utc is None:
            return False
        envelope = (self.end_utc - self.start_utc).total_seconds() / 3600.0
        return envelope - self.hours_up > 0.5


def assess_constellations(positions: list[ConstellationPosition],
                          location,
                          start: datetime,
                          end: datetime,
                          midnight: datetime,
                          min_altitude_deg: float = 25.0,
                          step: timedelta = timedelta(minutes=10),
                          ) -> dict[str, ConstellationVisibility]:
    """Viewing window per constellation, keyed by abbreviation.

    Judged on the centroid, so a wide constellation is approximated by its
    middle — the same caveat that applies to charting it as one curve. Good
    enough to answer "is Orion up yet, and until when", which is the point.
    """
    from skyfield.api import Star

    from .ephem import _observer, load_ephemeris

    if not positions:
        return {}

    eph = load_ephemeris()
    observer = _observer(eph, location)

    stamps: list[datetime] = []
    cursor = start
    while cursor <= end:
        stamps.append(cursor)
        cursor += step
    if len(stamps) < 2:
        return {
            p.abbreviation: ConstellationVisibility(
                p.abbreviation, NOT_VISIBLE, None, None, 0.0, -90.0)
            for p in positions
        }

    stars = Star(
        ra_hours=[p.ra_deg / 15.0 for p in positions],
        dec_degrees=[p.dec_deg for p in positions],
    )

    times = eph.timescale.from_datetimes(stamps)
    # above[slot] is the per-constellation mask across the night.
    above: list[list[bool]] = [[] for _ in positions]
    peaks = [-90.0] * len(positions)

    for index in range(len(stamps)):
        altitudes = observer.at(times[index]).observe(stars).apparent().altaz()[0].degrees
        for slot, altitude in enumerate(altitudes):
            value = float(altitude)
            above[slot].append(value >= min_altitude_deg)
            peaks[slot] = max(peaks[slot], value)

    hours_per_sample = step.total_seconds() / 3600.0
    out: dict[str, ConstellationVisibility] = {}

    for slot, position in enumerate(positions):
        mask = above[slot]
        indices = [i for i, up in enumerate(mask) if up]
        if not indices:
            out[position.abbreviation] = ConstellationVisibility(
                position.abbreviation, NOT_VISIBLE, None, None, 0.0, peaks[slot])
            continue

        first, last = stamps[indices[0]], stamps[indices[-1]]
        # End is the last sample plus one step, clipped to the window: the
        # constellation stays up until roughly the next sample would have been.
        window_end = min(last + step, end)
        status = VISIBLE_TONIGHT if first < midnight else VISIBLE_LATE

        out[position.abbreviation] = ConstellationVisibility(
            abbreviation=position.abbreviation,
            status=status,
            start_utc=first,
            end_utc=window_end,
            hours_up=len(indices) * hours_per_sample,
            peak_altitude_deg=peaks[slot],
        )
    return out


def classify_visibility(positions, location, start, end, midnight,
                        min_altitude_deg: float = 25.0,
                        step: timedelta = timedelta(minutes=20)) -> dict[str, str]:
    """Status only, for callers that do not need the window."""
    return {
        key: value.status
        for key, value in assess_constellations(
            positions, location, start, end, midnight,
            min_altitude_deg=min_altitude_deg, step=step).items()
    }
