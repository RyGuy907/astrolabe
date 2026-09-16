"""Scope math: limiting magnitude, extinction, airmass.

Driven by config/equipment.yaml. No datetimes cross this boundary, so there is
nothing here for the UTC rule to police, but the module stays inside the
engine's no-I/O-beyond-config contract.

**Eyepiece calculations are deliberately absent.** True field of view,
magnification, exit pupil and "which eyepiece frames this best" were removed
at the user's request as common knowledge not worth restating. What remains is
aperture-derived: limiting magnitude and extinction, which target filtering
genuinely depends on and which are not obvious by inspection.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
DEFAULT_EQUIPMENT_PATH = CONFIG_DIR / "equipment.yaml"

# Extinction coefficient, mag per airmass, at a decent site (PLAN.md 3.3).
DEFAULT_EXTINCTION_K = 0.2


@dataclass(frozen=True)
class Scope:
    key: str
    name: str
    aperture_mm: float
    focal_length_mm: float
    focal_ratio: float | None = None
    type: str | None = None

    def __post_init__(self) -> None:
        if self.aperture_mm <= 0 or self.focal_length_mm <= 0:
            raise ValueError(f"{self.key}: aperture and focal length must be positive")


@dataclass(frozen=True)
class Equipment:
    default_scope: str
    scopes: dict[str, Scope]
    eye_pupil_mm: float = 7.0

    def scope(self, key: str | None = None) -> Scope:
        resolved = key or self.default_scope
        if resolved not in self.scopes:
            known = ", ".join(sorted(self.scopes)) or "(none)"
            raise KeyError(f"unknown scope {resolved!r}; known: {known}")
        return self.scopes[resolved]


def load_equipment(path: Path | None = None) -> Equipment:
    """Parse equipment.yaml into an Equipment record.

    An `eyepieces:` section, if present, is ignored — see the module docstring.
    """
    path = path or DEFAULT_EQUIPMENT_PATH
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    scopes = {
        key: Scope(
            key=key,
            name=spec.get("name", key),
            aperture_mm=float(spec["aperture_mm"]),
            focal_length_mm=float(spec["focal_length_mm"]),
            focal_ratio=spec.get("focal_ratio"),
            type=spec.get("type"),
        )
        for key, spec in (raw.get("scopes") or {}).items()
    }

    observer = raw.get("observer") or {}
    return Equipment(
        default_scope=raw.get("default_scope") or next(iter(scopes), ""),
        scopes=scopes,
        eye_pupil_mm=float(observer.get("eye_pupil_mm", 7.0)),
    )


def naked_eye_limiting_mag(sqm: float) -> float:
    """Faintest naked-eye star for a sky of `sqm` mag/arcsec^2.

    Linear fit anchored on the conventional pairings: SQM 21.9 (Bortle 1) is
    about mag 7.8, SQM 18.0 (Bortle 8) about mag 4.3. Good enough for ranking;
    it is not a detection model.
    """
    return 4.3 + (sqm - 18.0) * (7.8 - 4.3) / (21.9 - 18.0)


def telescopic_gain(aperture_mm: float, eye_pupil_mm: float) -> float:
    """Magnitude gain of an aperture over the dark-adapted eye.

    5 * log10(D / pupil): 203 mm over a 6.5 mm pupil is about +7.5 mag. This is
    an aperture property — it does not depend on which eyepiece is in the focuser.
    """
    if aperture_mm <= 0 or eye_pupil_mm <= 0:
        raise ValueError("aperture and eye pupil must be positive")
    return 5.0 * math.log10(aperture_mm / eye_pupil_mm)


def telescopic_limiting_mag(scope: Scope, sqm: float,
                            eye_pupil_mm: float = 7.0) -> float:
    """Faintest star the scope reaches at zenith under a sky of `sqm`."""
    return naked_eye_limiting_mag(sqm) + telescopic_gain(scope.aperture_mm, eye_pupil_mm)


def airmass(altitude_deg: float) -> float:
    """Airmass at `altitude_deg`, via the Pickering (2002) relation.

    Stays finite near the horizon where the plain secant blows up. Below the
    horizon the concept is meaningless, so this raises.
    """
    if altitude_deg <= 0:
        raise ValueError(f"altitude {altitude_deg} is at or below the horizon")
    return 1.0 / math.sin(math.radians(altitude_deg + 244.0 / (165.0 + 47.0 * altitude_deg ** 1.1)))


def extinction_mag(altitude_deg: float, k: float = DEFAULT_EXTINCTION_K) -> float:
    """Atmospheric dimming in magnitudes: k * (airmass - 1)."""
    return k * (airmass(altitude_deg) - 1.0)


def limiting_mag_at_altitude(scope: Scope, sqm: float, altitude_deg: float,
                             eye_pupil_mm: float = 7.0,
                             k: float = DEFAULT_EXTINCTION_K) -> float:
    """Telescopic limiting magnitude corrected for extinction at an altitude."""
    return telescopic_limiting_mag(scope, sqm, eye_pupil_mm) - extinction_mag(altitude_deg, k)
