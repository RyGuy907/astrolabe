"""Obstruction horizons: azimuth -> minimum usable altitude.

PLAN.md §3.3 allows "an optional per-location horizon profile of az->min-alt
for tree/ridge obstruction". A flat altitude floor stays the default; this adds
an optional profile on top.

**On the built-in presets, plainly:** they are generic assumptions, not surveys
of anyone's site. A real horizon is measured — stand at the eyepiece, sweep the
azimuths, note where the ridge line sits. The presets exist so an obstructed
site is not modelled as a perfect ocean horizon, and anything using one is
flagged `is_generic` so the CLI and the UI can say so. Treat their numbers as
"probably obstructed below about here", never as ground truth.

**Why the numbers are what they are.** Each preset is derived from a stated
height and distance rather than picked to feel right, so the assumption is
auditable: the obstruction altitude is `atan(height / distance)`. The
geometry behind each one is in the comment beside it. This matters because the
effective floor is `max(min_altitude, horizon)` — a preset whose maximum sits
below the altitude floor cannot change a single result, which was true of
*every* preset here before these values were derived.

**Direction.** The obstruction in `ridge` and `valley` is defined centred on
azimuth 0 and then rotated to wherever it actually is, via `facing`. Which way
your view is blocked is the one thing about a horizon you can answer without
instruments — you know where the sun sets — so it is asked for rather than
assumed. `ridge` used to hardcode "assume the ridge lies north", which is
wrong three times in four.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass

# Generic presets. Each maps azimuth (degrees, 0 = north, clockwise) to the
# minimum altitude usable in that direction. Values between points are linearly
# interpolated, wrapping around 360.
#
# For the directional presets the obstruction is centred on azimuth 0 here and
# rotated into place by `facing`; see the module docstring.
PRESETS: dict[str, dict[int, float]] = {
    # An unobstructed site: sea horizon, desert playa, open plain.
    "flat": {0: 0.0},

    # Distant rolling terrain — a ridge line 60 m above the site a few hundred
    # metres off: atan(60/400) = 8.5 deg, atan(60/280) = 12 deg.
    #
    # NOTE: this tops out at 12 deg, well under the default 25 deg altitude
    # floor, so it cannot change which targets are listed. That is the correct
    # answer, not a bug — distant hills genuinely do not matter if you are not
    # observing below 25 deg anyway. It is offered so a site can be described
    # honestly, and the UI says outright that it will change nothing.
    "hilly": {0: 12.0, 90: 8.0, 180: 8.0, 270: 12.0},

    # Trees or buildings close on every side — a yard, or a clearing in
    # woodland. A 15 m canopy at 25 m is atan(15/25) = 31 deg; a two-storey
    # house 8 m high at 15 m is atan(8/15) = 28 deg. Taken as 30 deg all round.
    "trees": {0: 30.0},

    # One side blocked close in: a canyon mouth, a hillside, a building.
    # 150 m of rise at 200 m horizontal is atan(150/200) = 37 deg, taken as
    # 35 deg at the centre, falling away over roughly a 110 deg arc to an open
    # 5 deg on the far side. Rotate with `facing`.
    "ridge": {0: 35.0, 45: 28.0, 90: 12.0, 135: 6.0,
              180: 5.0, 225: 6.0, 270: 12.0, 315: 28.0},

    # Blocked on two opposing sides and open along the axis between them — a
    # valley floor or a street between tall buildings. Same 35 deg walls as
    # `ridge`. `facing` points at one of the two walls; the other is opposite.
    "valley": {0: 35.0, 45: 25.0, 90: 8.0, 135: 25.0,
               180: 35.0, 225: 25.0, 270: 8.0, 315: 25.0},
}

# Presets whose shape has a direction, so asking for a bearing is meaningful.
# The others are symmetric or all-round and a bearing would be noise.
DIRECTIONAL = frozenset({"ridge", "valley"})

DEFAULT_PRESET = "flat"


@dataclass(frozen=True)
class HorizonProfile:
    """Minimum usable altitude as a function of azimuth.

    Points are held as a sorted tuple of pairs rather than a dict so the
    profile stays hashable — `Location` embeds one and is itself frozen and
    cached, and a dict field would silently make it unhashable.
    """

    points: tuple[tuple[int, float], ...]
    name: str = "custom"
    is_generic: bool = False
    #: Bearing the preset's obstruction was rotated onto, or None if the
    #: profile was never rotated. Display and round-trip only — `points` is
    #: already in real azimuths, so nothing computing altitudes reads this.
    facing: int | None = None

    def __post_init__(self) -> None:
        if isinstance(self.points, dict):
            object.__setattr__(self, "points",
                               tuple(sorted((int(a), float(v))
                                            for a, v in self.points.items())))
        else:
            object.__setattr__(self, "points",
                               tuple(sorted((int(a), float(v))
                                            for a, v in self.points)))
        if not self.points:
            raise ValueError("a horizon profile needs at least one point")
        for azimuth, altitude in self.points:
            if not 0 <= azimuth < 360:
                raise ValueError(f"azimuth {azimuth} outside 0-360")
            if not -5.0 <= altitude <= 90.0:
                raise ValueError(f"altitude {altitude} outside -5..90")
        if self.facing is not None and not 0 <= self.facing < 360:
            raise ValueError(f"facing {self.facing} outside 0-360")

    @property
    def as_dict(self) -> dict[int, float]:
        return dict(self.points)

    @property
    def is_flat(self) -> bool:
        altitudes = {alt for _, alt in self.points}
        return altitudes == {0.0}

    @property
    def max_obstruction_deg(self) -> float:
        return max(alt for _, alt in self.points)

    def min_altitude_at(self, azimuth_deg: float) -> float:
        """Interpolated obstruction altitude for a given azimuth.

        Wraps at 360 so a profile with points at 315 and 0 interpolates across
        north rather than sweeping the long way round.
        """
        azimuth = azimuth_deg % 360.0
        table = self.as_dict
        azimuths = sorted(table)
        if len(azimuths) == 1:
            return table[azimuths[0]]

        # Bracket the azimuth, wrapping past the last point back to the first.
        for index, lower in enumerate(azimuths):
            upper = azimuths[(index + 1) % len(azimuths)]
            span = (upper - lower) % 360.0
            offset = (azimuth - lower) % 360.0
            if offset <= span:
                if span == 0:
                    return table[lower]
                fraction = offset / span
                low_alt, high_alt = table[lower], table[upper]
                return low_alt + (high_alt - low_alt) * fraction
        return table[azimuths[0]]

    def rotated(self, bearing: float) -> "HorizonProfile":
        """Swing the whole profile round so its azimuth 0 lands on `bearing`.

        Presets are authored with the obstruction facing north; this puts it
        where the obstruction actually is. Bearings are rounded to whole
        degrees so two points can never collide onto one azimuth.
        """
        offset = int(round(float(bearing))) % 360
        points = tuple(sorted(((azimuth + offset) % 360, altitude)
                              for azimuth, altitude in self.points))
        return dataclasses.replace(self, points=points, facing=offset)

    def clears(self, azimuth_deg: float, altitude_deg: float) -> bool:
        return altitude_deg >= self.min_altitude_at(azimuth_deg)


FLAT = HorizonProfile(points=((0, 0.0),), name="flat", is_generic=False)


def preset(name: str, facing: float | None = None) -> HorizonProfile:
    """A named generic profile. Everything but `flat` is flagged generic.

    `facing` rotates the profile so its obstruction points that way. It is
    accepted for any preset but only means anything for the ones in
    `DIRECTIONAL`; on a symmetric profile it is a harmless no-op.
    """
    key = name.strip().lower()
    if key not in PRESETS:
        known = ", ".join(sorted(PRESETS))
        raise KeyError(f"unknown horizon preset {name!r}; known: {known}")
    profile = HorizonProfile(points=tuple(sorted(PRESETS[key].items())),
                             name=key, is_generic=key != "flat")
    return profile if facing is None else profile.rotated(facing)


def parse_horizon(spec) -> HorizonProfile:
    """Build a profile from a config, database or request value.

    Accepts, in order of how they turn up:

    - `None` -> flat.
    - a preset name, optionally with a bearing: `hilly`, `ridge@250`.
    - a JSON object as a string, which is how a measured profile survives the
      round trip through the `horizon` TEXT column in SQLite.
    - an explicit azimuth->altitude mapping (`horizon: {0: 18, 90: 5}`), which
      is treated as **measured** and is *not* flagged generic.
    - a mapping naming a preset (`horizon: {preset: ridge, facing: 250}`),
      which is how YAML expresses a rotated preset readably.
    """
    if spec is None:
        return FLAT

    if isinstance(spec, str):
        text = spec.strip()
        if not text:
            return FLAT
        if text.startswith("{"):
            return parse_horizon(json.loads(text))
        if "@" in text:
            name, _, bearing = text.partition("@")
            try:
                return preset(name, facing=float(bearing))
            except ValueError:
                raise ValueError(f"{spec!r}: {bearing!r} is not a bearing")
        return preset(text)

    if isinstance(spec, dict):
        if "preset" in spec:
            facing = spec.get("facing")
            return preset(str(spec["preset"]),
                          facing=None if facing is None else float(facing))
        # A measured map. Keys arrive as ints from YAML and as strings from
        # JSON, so both are coerced.
        points = tuple(sorted((int(az) % 360, float(alt))
                              for az, alt in spec.items()))
        return HorizonProfile(points=points, name="custom", is_generic=False)

    raise TypeError(f"cannot read a horizon profile from {type(spec).__name__}")


def serialise(profile: HorizonProfile) -> str | None:
    """The inverse of `parse_horizon`, as a single string.

    Everything a profile needs has to fit one TEXT column, because `db/store`
    keeps runtime locations in one. Before this existed the store wrote only
    `profile.name`, so a measured profile — whose name is "custom" — was
    written as NULL and came back **flat and not flagged generic**, i.e.
    reading as a measured, unobstructed horizon. A fabricated value wearing
    the "measured" label is worse than a preset, so the round trip is now
    exercised by a test.
    """
    if profile.name in PRESETS:
        return (f"{profile.name}@{profile.facing}"
                if profile.facing is not None else profile.name)
    return json.dumps({str(az): alt for az, alt in profile.points})


def build(spec, facing: float | None = None) -> HorizonProfile:
    """`parse_horizon`, plus a bearing supplied separately.

    The HTTP layer carries the preset and its bearing as two fields, which is
    a nicer request shape than a packed string; this rejoins them so `api/`
    does no profile assembly of its own.
    """
    profile = parse_horizon(spec)
    return profile if facing is None else profile.rotated(facing)
