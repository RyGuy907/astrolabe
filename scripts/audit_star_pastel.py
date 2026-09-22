"""Audit the star cards' temperatures and giant/dwarf calls against PASTEL.

Development-time only: it downloads PASTEL (Soubiran et al. 2016, VizieR
B/pastel) -- temperatures and surface gravities from high-resolution
spectra, sharing nothing with the data the cards use -- and matches it to
the chart's stars by position (10") and V magnitude (0.4).

Result on 2026-09-21, 14,604 stars matched: temperature within 10% for 92%
(Gaia spectroscopic 97%, B-V colour 98%, spectral type 76%). Giants called
giants 89%; dwarfs called dwarfs 80% (was 50%, before stars were placed on
or off the main sequence by brightness when their type gives no class).

Usage:
    python scripts/audit_star_pastel.py
"""

import collections, math, os, statistics, sys, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine.stars import _rows, star_profile

import tempfile
PATH = os.path.join(tempfile.gettempdir(), "astrolabe-pastel.tsv")
if not os.path.exists(PATH):
    url = ("https://vizier.cds.unistra.fr/viz-bin/asu-tsv?-source=B/pastel/pastel"
           "&-out.max=unlimited&-out=ID,RAdeg,DEdeg,Vmag,Teff,logg")
    urllib.request.urlretrieve(url, PATH)
per = collections.defaultdict(lambda: {"t": [], "g": [], "v": None, "ra": None, "de": None})
for line in open(PATH, encoding="utf-8"):
    if line.startswith("#") or not line.strip():
        continue
    c = [x.strip() for x in line.rstrip("\n").split("\t")]
    if len(c) < 6 or not c[1] or c[1] in ("deg",) or c[1].startswith("-"):
        continue
    try:
        ra, de = float(c[1]), float(c[2])
    except ValueError:
        continue
    e = per[c[0]]
    e["ra"], e["de"] = ra, de
    if c[3]: e["v"] = float(c[3])
    if c[4]: e["t"].append(float(c[4]))
    if c[5]: e["g"].append(float(c[5]))
stars = [(k, e) for k, e in per.items() if e["t"]]
print(f"PASTEL stars with a temperature: {len(stars)}")

rows = _rows()
cell = collections.defaultdict(list)
for i, r in enumerate(rows):
    cell[(int(float(r[0])), int(float(r[1])))].append(i)
pairs = []
for key, e in stars:
    best, sep_best = None, 10 / 3600
    for a in (-1, 0, 1):
        for b in (-1, 0, 1):
            for i in cell.get((int(e["ra"]) + a, int(e["de"]) + b), []):
                r = rows[i]
                sep = math.hypot((float(r[0]) - e["ra"]) * math.cos(math.radians(e["de"])), float(r[1]) - e["de"])
                if sep < sep_best and (e["v"] is None or abs(float(r[2]) - e["v"]) < 0.4):
                    best, sep_best = i, sep
    if best is not None:
        pairs.append((best, statistics.median(e["t"]), statistics.median(e["g"]) if e["g"] else None))
print(f"matched to chart stars: {len(pairs)}\n")

by_src = collections.defaultdict(list)
giant_calls = collections.Counter()
for i, teff, logg in pairs:
    p = star_profile(i)
    if p.temperature_k:
        by_src[p.temperature_source].append(p.temperature_k / teff)
    if logg is not None and p.kind:
        truth = "giant" if logg < 3.5 else "dwarf"
        says = ("giant" if any(w in p.kind for w in ("giant", "supergiant")) else
                "dwarf" if any(w in p.kind for w in ("dwarf", "main-sequence")) else "unsaid")
        giant_calls[(truth, says)] += 1

print("TEMPERATURE (ours / PASTEL)")
for src, xs in sorted(by_src.items(), key=lambda kv: -len(kv[1])):
    w = lambda k: sum(1 for x in xs if 1 / k <= x <= k) / len(xs)
    print(f"  {src:9s} n={len(xs):5d}  median {statistics.median(xs):.3f}  within 5% {w(1.05):4.0%}  10% {w(1.10):4.0%}  25% {w(1.25):4.0%}")
allx = [x for xs in by_src.values() for x in xs]
w = lambda k: sum(1 for x in allx if 1 / k <= x <= k) / len(allx)
print(f"  {'all':9s} n={len(allx):5d}  median {statistics.median(allx):.3f}  within 5% {w(1.05):4.0%}  10% {w(1.10):4.0%}  25% {w(1.25):4.0%}")

print("\nGIANT OR DWARF (PASTEL log g < 3.5 is a giant) -> what the card says")
for truth in ("giant", "dwarf"):
    tot = sum(v for (t, _), v in giant_calls.items() if t == truth)
    parts = ", ".join(f"{says} {giant_calls[(truth, says)] / tot:.0%}" for says in ("giant", "dwarf", "unsaid"))
    print(f"  {truth}s (n={tot}): {parts}")

print("\nLUMINOSITY CLASS, three ways (PASTEL log g: <3.5 giant, 3.5-3.9 subgiant, >=3.9 dwarf)")
from engine.stars import parse_spectral_type, temperature_from_colour
calls = collections.Counter(); wrong_giant = collections.Counter()
by_cls = collections.defaultdict(list)
for i, teff, logg in pairs:
    p = star_profile(i)
    r = rows[i]
    if p.temperature_source == "type" and p.temperature_k:
        parsed = parse_spectral_type(p.spectral_type or "")
        c = parsed[0] if parsed else "?"
        alt = temperature_from_colour(float(r[7])) if r[7] else None
        by_cls[c].append((p.temperature_k / teff, alt / teff if alt else None))
    if logg is None:
        continue
    truth = "giant" if logg < 3.5 else "subgiant" if logg < 3.9 else "dwarf"
    k = p.kind
    says = ("subgiant" if "subgiant" in k else "giant" if "giant" in k else
            "dwarf" if ("dwarf" in k or "main-sequence" in k) else "unsaid")
    calls[(truth, says)] += 1
    if truth == "dwarf" and says == "giant":
        parsed = parse_spectral_type(r[6] or "")
        wrong_giant["catalogue class" if parsed and parsed[2] else "inferred from brightness"] += 1
for truth in ("giant", "subgiant", "dwarf"):
    tot = sum(v for (t, _), v in calls.items() if t == truth)
    print(f"  {truth:8s} n={tot:5d}: " + ", ".join(f"{s} {calls[(truth, s)] / tot:.0%}" for s in ("giant", "subgiant", "dwarf", "unsaid")))
print("  dwarfs the card calls giants, by why:", dict(wrong_giant))
print("\nTEMPERATURE FROM TYPE, by class: type within 10% | colour would be")
for c, xs in sorted(by_cls.items()):
    t = [a for a, _ in xs]; cc = [b for _, b in xs if b]
    w = lambda ys: sum(1 for y in ys if 1/1.1 <= y <= 1.1) / len(ys) if ys else float("nan")
    print(f"  {c}: n={len(xs):4d}  type {w(t):4.0%}   colour {w(cc):4.0%}")
