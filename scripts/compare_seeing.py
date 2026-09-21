"""Compare engine/seeing.py and 7Timer against Meteoblue's seeing forecast.

Development-time only: it reads the network directly and the engine never
imports it.

The reference is Meteoblue's astronomy seeing product, which integrates
turbulent layers through a high-resolution profile -- a far fuller model than
either candidate here, and the one this estimator is trying to approximate
from Open-Meteo's coarse, free data. Meteoblue's forecasts are theirs to
publish, so they are not redistributed with this repository: the script reads
them from a directory of one file per site, each a `;`-separated list of
`date,hour,low,mid,high,arcsec,index1,index2,jet_ms` rows in the site's local
time, as their seeing page lists them.

**The test is leave-one-site-out.** Both candidates get the same treatment:
calibration fitted on six sites and scored on the seventh, which it has
never seen, rotated through all seven. Hours within one site are strongly
autocorrelated, so a random split of hours would leak and flatter everyone.

Two questions are scored separately, because they are different questions:

* **Across sites** -- does it rank Miami's air worse than Flagstaff's?
* **Within a site, over time** -- does it tell this site's good nights from
  its bad ones? This is the one a planner at a fixed site actually asks, and
  a model can do well on the first and have no skill at all on the second.

Result, 2026-09-21 -- and why the planner does not use this model
----------------------------------------------------------------
497 hourly Meteoblue forecasts from 7 sites (Salt Lake City, Flagstaff,
Seattle, Miami, Chicago, Boston, London), leave-one-site-out:

                          MAE    r across   r within   agreement with
                                  sites      a site    Meteoblue index
    layer model (v1)      0.66    0.21       0.13         -0.60
    7Timer, recalibrated  0.46    0.64       0.03         -0.17
    always-the-mean       0.68      --       0.00            --
    jet speed alone        --     0.01        --             --

(The index runs 5 = excellent, so strong agreement is a large *negative*.)

* Neither candidate tracks Meteoblue's hour-to-hour changes at a fixed
  site. Within-site r is 0.13 for the model and 0.03 for 7Timer -- no
  usable skill either way, at the one question a planner asks.
* 7Timer is better across sites: it knows dry western air is steadier than
  humid eastern air, which is also why its error is lower.
* The model agrees with Meteoblue's layer-integrated *index* far better than
  7Timer does (-0.60 against -0.17), which is the part of their product
  computed the same way. But Meteoblue's index and its own arcsecond figure
  agree only at r = -0.47 pooled, and within a site from -0.49 to +0.27 --
  sometimes with the wrong sign. The reference does not agree with itself
  hour to hour, which caps what any comparison against it can show.
* The jet-stream rule of thumb ("over 20 m/s means bad seeing") has no
  predictive value at all against this reference: r = 0.01.

Two revisions were tried and are reported rather than dropped. Adding the
model's 2-180 m near-surface levels to the same stack (v2) produced
turbulence values thousands of times the free atmosphere's -- the Dewan fit
is for the free atmosphere, not a sunlit surface layer -- and the calibration
zeroed the profile entirely. Giving the surface layer its own weight (v3,
this script's default) fitted that weight to zero at every held-out site: the
near-surface fields carry nothing that tracks the reference.

So the planner keeps 7Timer, on the arcsecond scale in engine/weather.py,
and this model stays here as a documented negative result. The honest
conclusion is about the problem rather than either model: from free data,
seeing is forecastable as a site's general character, not as tonight versus
tomorrow.

Usage:
    python scripts/compare_seeing.py <meteoblue_dir> [--no-surface]
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine.locations import Location  # noqa: E402
from seeing_model import (Level, seeing_from_integral,  # noqa: E402
                          turbulence_integral)
from engine.weather import SEEING_CLASS_ARCSEC, fetch_7timer  # noqa: E402

UTC = timezone.utc
LEVELS = [1000, 975, 950, 925, 900, 850, 800, 700, 600, 500, 400, 300,
          250, 200, 150, 100, 70, 50]
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"

#: The coordinates Meteoblue's GeoNames entries resolve to, not the
#: planner's saved sites: the comparison has to be at the same point.
SITES = {
    "slc":       ("Salt Lake City", 40.7608, -111.8911, 1288, "America/Denver"),
    "flagstaff": ("Flagstaff",      35.1981, -111.6513, 2106, "America/Phoenix"),
    "seattle":   ("Seattle",        47.6062, -122.3321,   56, "America/Los_Angeles"),
    "miami":     ("Miami",          25.7743,  -80.1937,    2, "America/New_York"),
    "chicago":   ("Chicago",        41.8500,  -87.6500,  180, "America/Chicago"),
    "boston":    ("Boston",         42.3584,  -71.0598,   14, "America/New_York"),
    "london":    ("London",         51.5085,   -0.1257,   25, "Europe/London"),
}

#: Hours counted as night for the night-only scores, local time.
NIGHT_HOURS = set(range(20, 24)) | set(range(0, 6))


def read_meteoblue(path: Path, tz: str) -> dict[datetime, dict]:
    out = {}
    zone = ZoneInfo(tz)
    for row in path.read_text().strip().split(";"):
        d, h, low, mid, high, arcsec, i1, i2, jet = row.split(",")
        local = datetime.fromisoformat(d).replace(hour=int(h), tzinfo=zone)
        out[local.astimezone(UTC)] = {
            "arcsec": float(arcsec), "index1": int(i1), "index2": int(i2),
            "jet": float(jet), "local_hour": int(h),
        }
    return out


#: Near-surface fields: (temperature field, wind height), metres above ground.
#: The 2 m temperature is paired with the 10 m wind -- the model gives no
#: 2 m wind -- and placed at 10 m, which is close enough for a layer whose
#: upper edge is at 80 m.
SURFACE = [("temperature_2m", 10), ("temperature_80m", 80),
           ("temperature_120m", 120), ("temperature_180m", 180)]
SURFACE_WIND = {10: "10m", 80: "80m", 120: "120m", 180: "180m"}


def fetch_profiles(lat: float, lon: float, cache: Path, elev: float
                   ) -> dict[datetime, tuple[list[Level], list[Level]]]:
    """Per hour: (pressure levels, near-surface levels), kept apart.

    Apart because they are different regimes. The Dewan outer-scale fit was
    made for the free atmosphere; over a 70 m surface layer on a sunny
    afternoon the same arithmetic yields turbulence thousands of times larger
    and mostly noise. Lumped together, the surface terms swamped the profile
    and the calibration zeroed the lot (see the module docstring's results).
    """
    if cache.exists():
        payload = json.loads(cache.read_text())
    else:
        fields = [f"{var}_{p}hPa" for p in LEVELS
                  for var in ("temperature", "wind_speed", "wind_direction",
                              "geopotential_height")]
        fields += [t for t, _ in SURFACE] + ["surface_pressure"]
        fields += [f"wind_{kind}_{h}" for h in SURFACE_WIND.values()
                   for kind in ("speed", "direction")]
        query = urllib.parse.urlencode({
            "latitude": lat, "longitude": lon, "hourly": ",".join(fields),
            "wind_speed_unit": "ms", "timezone": "UTC",
            "past_days": 2, "forecast_days": 4,
        })
        with urllib.request.urlopen(f"{OPEN_METEO}?{query}", timeout=60) as r:
            payload = json.load(r)
        cache.write_text(json.dumps(payload))

    hourly = payload["hourly"]
    out = {}
    for i, stamp in enumerate(hourly["time"]):
        when = datetime.fromisoformat(stamp).replace(tzinfo=UTC)
        levels, surface = [], []
        for p in LEVELS:
            vals = [hourly[f"{v}_{p}hPa"][i] for v in
                    ("geopotential_height", "temperature", "wind_speed", "wind_direction")]
            if None in vals:
                continue
            levels.append(Level(pressure_hpa=p, height_m=vals[0],
                                temperature_c=vals[1], wind_speed_ms=vals[2],
                                wind_direction_deg=vals[3]))
        if hourly.get("surface_pressure", [None])[i] is not None:
            ps = hourly["surface_pressure"][i]
            for temp_field, z in SURFACE:
                h = SURFACE_WIND[z]
                vals = (hourly.get(temp_field, [None] * (i + 1))[i],
                        hourly.get(f"wind_speed_{h}", [None] * (i + 1))[i],
                        hourly.get(f"wind_direction_{h}", [None] * (i + 1))[i])
                if None in vals:
                    continue
                # Hypsometric, 8.4 km scale height: exact enough over 180 m.
                surface.append(Level(pressure_hpa=ps * np.exp(-z / 8400.0),
                                    height_m=elev + z, temperature_c=vals[0],
                                    wind_speed_ms=vals[1], wind_direction_deg=vals[2]))
        out[when] = (levels, surface)
    return out


def seventimer_arcsec(lat, lon, elev, tz) -> dict[datetime, float]:
    site = Location(key="cmp", name="cmp", lat=lat, lon=lon, elevation_m=elev, tz=tz)
    raw = fetch_7timer(site, use_cache=False)
    if not raw:
        return {}
    init = datetime.strptime(raw["init"], "%Y%m%d%H").replace(tzinfo=UTC)
    out = {}
    for entry in raw["dataseries"]:
        t = init + timedelta(hours=entry["timepoint"])
        arcsec = SEEING_CLASS_ARCSEC[int(entry["seeing"]) - 1]
        # 7Timer is 3-hourly; each value stands for the hours around it.
        for dh in (-1, 0, 1):
            out[t + timedelta(hours=dh)] = arcsec
    return out


def nnls(A: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Non-negative least squares by exhausting the active sets.

    Three columns means eight subsets; checking them all is exact and needs
    nothing beyond numpy.
    """
    n = A.shape[1]
    best, best_err = np.zeros(n), float(np.sum(y ** 2))
    for mask in range(1, 2 ** n):
        idx = [j for j in range(n) if mask >> j & 1]
        sol, *_ = np.linalg.lstsq(A[:, idx], y, rcond=None)
        if np.any(sol < 0):
            continue
        full = np.zeros(n); full[idx] = sol
        err = float(np.sum((A @ full - y) ** 2))
        if err < best_err:
            best, best_err = full, err
    return best


def pearson(a, b) -> float:
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 3 or a.std() == 0 or b.std() == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def spearman(a, b) -> float:
    rank = lambda x: np.argsort(np.argsort(np.asarray(x, float)))
    return pearson(rank(a), rank(b))


def main(meteoblue_dir: Path, with_surface: bool) -> int:
    print("profile:", "pressure levels + near-surface (2-180 m)" if with_surface
          else "pressure levels only")
    cache_dir = meteoblue_dir / "_openmeteo_cache_v2"
    cache_dir.mkdir(exist_ok=True)

    rows = []   # one per matched hour
    for key, (name, lat, lon, elev, tz) in SITES.items():
        mb = read_meteoblue(meteoblue_dir / f"{key}.txt", tz)
        profiles = fetch_profiles(lat, lon, cache_dir / f"{key}.json", elev)
        timer = seventimer_arcsec(lat, lon, elev, tz)
        for when, ref in mb.items():
            if when not in profiles:
                continue
            levels, surface = profiles[when]
            integral = turbulence_integral(levels, elev)
            if integral is None:
                continue
            surf = turbulence_integral(surface, elev) if with_surface else None
            rows.append({
                "site": key, "when": when, "ref": ref["arcsec"],
                "index1": ref["index1"], "jet": ref["jet"],
                "night": ref["local_hour"] in NIGHT_HOURS,
                "free": seeing_from_integral(integral),
                "surf": seeing_from_integral(surf) if surf else 0.0,
                "timer": timer.get(when),
            })

    print(f"{len(rows)} matched hours across {len(SITES)} sites\n")

    # --- leave-one-site-out ------------------------------------------------
    preds = {"model": {}, "7timer": {}, "baseline": {}}
    fitted = {}
    for held in SITES:
        train = [r for r in rows if r["site"] != held]
        test = [r for r in rows if r["site"] == held]

        # Model: ref^(5/3) = a free^(5/3) + b surf^(5/3) + c, all >= 0.
        # Seeing adds in Cn^2, i.e. in the 5/3 power, so the fit is linear
        # there. Each layer regime gets its own weight.
        cols = lambda r: [r["free"] ** (5 / 3), r["surf"] ** (5 / 3), 1.0]
        A = np.array([cols(r) for r in train])
        Y = np.array([r["ref"] ** (5 / 3) for r in train])
        coef = nnls(A, Y)
        fitted[held] = tuple(coef)
        for r in test:
            preds["model"][id(r)] = max(float(np.dot(cols(r), coef)), 0.0) ** 0.6

        # 7Timer: the same fair chance -- a linear recalibration, fitted on
        # the same six sites.
        tt = [r for r in train if r["timer"] is not None]
        x = np.array([r["timer"] for r in tt]); y = np.array([r["ref"] for r in tt])
        b, a = np.polyfit(x, y, 1)
        for r in test:
            if r["timer"] is not None:
                preds["7timer"][id(r)] = a + b * r["timer"]

        # No skill: always the training mean.
        mean = float(np.mean([r["ref"] for r in train]))
        for r in test:
            preds["baseline"][id(r)] = mean

    def score(subset, label):
        print(f"--- {label} ({len(subset)} hours) ---")
        print(f"{'':10} {'MAE':>6} {'r pooled':>9} {'r within site':>14} {'rank vs index1':>15}")
        for name, p in preds.items():
            have = [r for r in subset if id(r) in p]
            if not have:
                continue
            pr = [p[id(r)] for r in have]
            ref = [r["ref"] for r in have]
            mae = float(np.mean(np.abs(np.array(pr) - np.array(ref))))
            within = [pearson([p[id(r)] for r in have if r["site"] == s],
                              [r["ref"] for r in have if r["site"] == s])
                      for s in SITES]
            within = [w for w in within if not np.isnan(w)]
            # Meteoblue's index is 5 = excellent, so good agreement is negative.
            idx = spearman(pr, [r["index1"] for r in have])
            print(f"{name:10} {mae:6.2f} {pearson(pr, ref):9.2f} "
                  f"{(np.mean(within) if within else float('nan')):14.2f} {idx:15.2f}")
        print()

    score(rows, "all hours")
    score([r for r in rows if r["night"]], "night hours only")

    print("--- per held-out site, night hours: mean arcsec and within-site r ---")
    print(f"{'site':10} {'meteoblue':>9} {'model':>6} {'7timer':>7} {'r model':>8} {'r 7timer':>9}  fit (free, surface, const)")
    for s in SITES:
        sub = [r for r in rows if r["site"] == s and r["night"]]
        m = [preds["model"][id(r)] for r in sub]
        t_sub = [r for r in sub if id(r) in preds["7timer"]]
        t = [preds["7timer"][id(r)] for r in t_sub]
        print(f"{s:10} {np.mean([r['ref'] for r in sub]):9.2f} {np.mean(m):6.2f} "
              f"{(np.mean(t) if t else float('nan')):7.2f} "
              f"{pearson(m, [r['ref'] for r in sub]):8.2f} "
              f"{pearson(t, [r['ref'] for r in t_sub]):9.2f}  "
              f"({fitted[s][0]:.3g}, {fitted[s][1]:.3g}, {fitted[s][2]:.3g})")

    print("\n--- sanity: jet speed alone as a predictor (the rule of thumb) ---")
    print(f"r(jet, meteoblue arcsec) pooled = {pearson([r['jet'] for r in rows], [r['ref'] for r in rows]):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]), "--no-surface" not in sys.argv))
