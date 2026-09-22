"""Audit the sky chart's star cards against Gaia DR3.

Development-time only: it reads the network (the Gaia archive's TAP
service); the engine never does. For the bright named stars Gaia saturates
on, `tests/test_stars.py` checks published values instead.

Result on 2026-09-21, 1,500 random stars of magnitude 5-8 (ours / Gaia,
share within 25%): distance 96% for those the card calls precise;
temperature 93%; radius 73%. Before the fixes that audit led to -- giants
recognised by brightness when the type doesn't say, B-V temperatures for
F and G stars, per-subtype temperatures -- radius was 64% and temperature 90%.

Since then most cards take Gaia's own distance and (for F-M stars) radius
where Gaia has them -- see `scripts/fetch_star_details.py` -- so for those
this compares Gaia with itself. What it still measures is the estimated
path, the fallback for the ~27,600 stars with neither a Gaia radius nor a
measured diameter.

Usage:
    python scripts/audit_star_cards.py
"""
import csv, io, math, random, statistics, sys, urllib.parse, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine.stars import _rows, star_profile

rows = _rows()
random.seed(7)
pool = [i for i, r in enumerate(rows) if r[4] and 5.0 <= float(r[2]) <= 8.0]
sample = random.sample(pool, 1500)
hips = {int(rows[i][4]): i for i in sample}

query = f"""
SELECT h.original_ext_source_id AS hip, g.parallax, g.parallax_over_error,
       g.phot_g_mean_mag, a.teff_gspphot, a.teff_gspspec, a.radius_flame,
       a.radius_gspphot, a.lum_flame, a.age_flame, a.evolstage_flame
FROM gaiadr3.hipparcos2_best_neighbour AS h
JOIN gaiadr3.gaia_source AS g ON g.source_id = h.source_id
LEFT JOIN gaiadr3.astrophysical_parameters AS a ON a.source_id = h.source_id
WHERE h.original_ext_source_id IN ({",".join(str(h) for h in hips)})
"""
data = urllib.parse.urlencode({"REQUEST": "doQuery", "LANG": "ADQL",
                               "FORMAT": "csv", "QUERY": query}).encode()
with urllib.request.urlopen("https://gea.esac.esa.int/tap-server/tap/sync",
                            data=data, timeout=300) as resp:
    gaia = list(csv.DictReader(io.StringIO(resp.read().decode())))
print(f"sample {len(hips)}, matched in Gaia {len(gaia)}")

def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None

dist, teff, rad, lum, kinds = [], [], [], [], []
worst = []
for g in gaia:
    p = star_profile(hips[int(g["hip"])])
    plx, poe = f(g["parallax"]), f(g["parallax_over_error"])
    if p.distance_ly and plx and poe and poe > 10:
        gd = 3261.56 / plx
        dist.append((p.distance_ly / gd, p.distance_quality, gd))
    t = f(g["teff_gspspec"]) or f(g["teff_gspphot"])
    if p.temperature_k and t:
        teff.append(p.temperature_k / t)
    r = f(g["radius_flame"])
    if p.radius_sun and r:
        rad.append(p.radius_sun / r)
        worst.append((abs(math.log(p.radius_sun / r)), p.name or f"HIP {p.hip}",
                      p.spectral_type, p.kind, p.radius_sun, r, p.temperature_k, t))
    l = f(g["lum_flame"])
    if p.luminosity_sun and l:
        lum.append(p.luminosity_sun / l)

def summary(label, ratios):
    logs = [math.log10(x) for x in ratios]
    med = 10 ** statistics.median(logs)
    within = lambda k: sum(1 for x in ratios if 1 / k <= x <= k) / len(ratios)
    print(f"{label:12s} n={len(ratios):3d}  median ratio {med:.3f}  "
          f"within 10% {within(1.10):.0%}  within 25% {within(1.25):.0%}  "
          f"within 2x {within(2):.0%}")

print("\nOurs / Gaia DR3:")
summary("distance", [d[0] for d in dist])
for q in ("precise", "approximate", "rough"):
    sub = [d[0] for d in dist if d[1] == q]
    if sub:
        summary(f"  {q}", sub)
summary("temperature", teff)
summary("radius", rad)
summary("luminosity", lum)
print("\nWorst radius disagreements:")
for w in sorted(worst, reverse=True)[:12]:
    print(f"  {w[1]:14s} {w[2] or '-':12s} {w[3]:28s} ours {w[4]:>7} gaia {w[5]:7.2f}  T ours {w[6]} gaia {w[7]}")
