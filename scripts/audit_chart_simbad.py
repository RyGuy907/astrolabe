"""Audit the chart's stars against SIMBAD: position, magnitude, name,
spectral class and distance, for ~3,000 random stars plus every named one.

Development-time only: it queries SIMBAD's TAP service (CDS, Strasbourg).

Result on 2026-09-22, 3,597 stars: positions within 0.2" for 95% (none
off by an arcminute); magnitudes within 0.1 for 96% (the larger differences
are double stars, where the chart rightly draws the combined light
Hipparcos measured and SIMBAD quotes one component, and variables); the
card's spectral class the same letter as SIMBAD's for 95%; Gaia distances
within 5% for 99%; Hipparcos distances the card calls precise within 10%
for 86% (SIMBAD's own figure for those is often a Gaia parallax whose fit
Gaia flags as unreliable). Names are checked against the WGSN's list in
`fetch_star_chart_data.py` rather than here: SIMBAD lags the WGSN by years.

Usage:
    python scripts/audit_chart_simbad.py
"""

import collections, csv, io, json, math, os, random, re, statistics, sys, time, urllib.parse, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine.stars import _rows, star_profile, parse_spectral_type

import tempfile
CACHE = os.path.join(tempfile.gettempdir(), "astrolabe-simbad-sample.json")
TAP = "https://simbad.cds.unistra.fr/simbad/sim-tap/sync"

rows = _rows()
random.seed(11)
with_hip = [i for i, r in enumerate(rows) if r[4]]
named = [i for i in with_hip if rows[i][3] and not re.search(r" [A-Za-z0-9]{3}$", rows[i][3])]
sample = sorted(set(random.sample(with_hip, 3000)) | set(named))
print(f"sample: {len(sample)} stars ({len(named)} with proper names)")

def tap(query):
    body = urllib.parse.urlencode({"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": query}).encode()
    for attempt in range(5):
        try:
            with urllib.request.urlopen(TAP, data=body, timeout=300) as r:
                return list(csv.DictReader(io.StringIO(r.read().decode("utf-8"))))
        except Exception as e:
            print("  retry:", e); time.sleep(10 * (attempt + 1))
    raise SystemExit("SIMBAD unreachable")

data = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
todo = [rows[i][4] for i in sample if rows[i][4] not in data]
for k in range(0, len(todo), 400):
    ids = ",".join(f"'HIP {h}'" for h in todo[k:k + 400])
    basic = tap(f"""SELECT i.id AS hip, b.oid, b.main_id, b.ra, b.dec, b.sp_type, b.plx_value, b.plx_err
                    FROM ident AS i JOIN basic AS b ON b.oid = i.oidref WHERE i.id IN ({ids})""")
    oids = ",".join(r["oid"] for r in basic) or "0"
    flux = {r["oidref"]: r for r in tap(f"SELECT oidref, V, B FROM allfluxes WHERE oidref IN ({oids})")}
    names = collections.defaultdict(list)
    for r in tap(f"SELECT oidref, id FROM ident WHERE oidref IN ({oids}) AND id LIKE 'NAME%'"):
        names[r["oidref"]].append(re.sub(r'^NAME(-IAU)?\s*', '', r["id"]).strip())
    for r in basic:
        f = flux.get(r["oid"], {})
        data[r["hip"][4:].strip()] = dict(main=r["main_id"], ra=r["ra"], dec=r["dec"], sp=r["sp_type"],
                                          plx=r["plx_value"], plx_err=r["plx_err"], V=f.get("V"), B=f.get("B"),
                                          names=names.get(r["oid"], []))
    json.dump(data, open(CACHE, "w"))
    print(f"  simbad: {min(k + 400, len(todo))}/{len(todo)}")

num = lambda x: float(x) if x not in (None, "") else None
pos_off, mag_off, far, bad_mag = [], [], [], []
type_agree, type_total, dist_ratio = 0, 0, []
name_ok, name_bad = 0, []
for i in sample:
    r = rows[i]; s = data.get(r[4])
    if not s:
        continue
    ra, dec = num(s["ra"]), num(s["dec"])
    if ra is not None:
        sep = math.hypot((float(r[0]) - ra) * math.cos(math.radians(dec)), float(r[1]) - dec) * 3600
        pos_off.append(sep)
        if sep > 60: far.append((r[3] or f"HIP {r[4]}", round(sep), r[2]))
    V = num(s["V"])
    if V is not None:
        d = float(r[2]) - V
        mag_off.append(d)
        if abs(d) > 0.5: bad_mag.append((r[3] or f"HIP {r[4]}", r[2], V))
    ours = parse_spectral_type(rows[i][6] or "")
    theirs = parse_spectral_type(s["sp"] or "")
    if ours and theirs:
        type_total += 1
        p = star_profile(i)
        # The card's type (after any companion correction) against SIMBAD's class letter.
        card_cls = parse_spectral_type(p.spectral_type or "")[0] if not p.type_note else None
        if p.type_note:
            card_cls = {"Blue": "O", "Blue-white": "B", "White": "A", "Yellow-white": "F",
                        "Yellow": "G", "Orange": "K", "Red": "M"}.get(p.kind.split()[0])
        type_agree += card_cls == theirs[0]
    plx, err = num(s["plx"]), num(s["plx_err"])
    p = star_profile(i)
    if plx and err and plx / err > 10 and p.distance_ly:
        dist_ratio.append((p.distance_ly / (3261.56 / plx), p.distance_source))
    if r[3] and not re.search(r" [A-Za-z0-9]{3}$", r[3]):
        norm = lambda x: re.sub(r"[^a-z]", "", x.lower().encode("ascii", "ignore").decode())
        if any(norm(n) == norm(r[3]) for n in s["names"]):
            name_ok += 1
        else:
            name_bad.append((r[3], s["main"], s["names"][:3]))

pos_off.sort()
print(f"\nPOSITION vs SIMBAD (n={len(pos_off)}): median {statistics.median(pos_off):.1f}\"  "
      f"95th pct {pos_off[int(0.95 * len(pos_off))]:.1f}\"  over 1': {len(far)}")
for f in sorted(far, key=lambda x: -x[1])[:10]: print("   ", f)
mag_abs = sorted(abs(x) for x in mag_off)
print(f"MAGNITUDE vs SIMBAD V (n={len(mag_off)}): median offset {statistics.median(mag_off):+.3f}  "
      f"within 0.1 {sum(1 for x in mag_abs if x <= 0.1) / len(mag_abs):.0%}  "
      f"within 0.3 {sum(1 for x in mag_abs if x <= 0.3) / len(mag_abs):.0%}  off by >0.5: {len(bad_mag)}")
for b in bad_mag[:10]: print("   ", b)
print(f"SPECTRAL CLASS on the card vs SIMBAD: {type_agree}/{type_total} = {type_agree / type_total:.1%} same letter")
for src in ("gaia", "hipparcos"):
    xs = [x for x, s in dist_ratio if s == src]
    if xs:
        w = lambda k: sum(1 for x in xs if 1 / k <= x <= k) / len(xs)
        print(f"DISTANCE ({src}) vs SIMBAD parallax, n={len(xs)}: median {statistics.median(xs):.3f}  within 5% {w(1.05):.0%}  10% {w(1.10):.0%}")
print(f"NAMES: {name_ok} of {name_ok + len(name_bad)} proper names are among SIMBAD's names for that star")
for n in name_bad[:40]: print("   ", n)

# IAU names SIMBAD knows that the chart doesn't show (a newer list?).
iau_simbad = []
for i in sample:
    s = data.get(rows[i][4])
    if s and rows[i][3] and re.search(r" [A-Za-z0-9]{3}$", rows[i][3]) and s["names"]:
        iau_simbad.append((rows[i][3], s["names"]))
print(f"\nstars shown with only a Bayer letter that SIMBAD names: {len(iau_simbad)}")
for x in iau_simbad[:15]: print("   ", x)

# Hipparcos-distance stars: by the quality the card claims.
print("\nHIPPARCOS DISTANCES vs SIMBAD, by the card's stated quality")
for q in ("precise", "approximate", "rough"):
    xs = []
    for i in sample:
        s = data.get(rows[i][4]); p = star_profile(i)
        plx, err = num(s["plx"]) if s else None, num(s["plx_err"]) if s else None
        if p.distance_source == "hipparcos" and p.distance_quality == q and plx and err and plx / err > 10:
            xs.append(p.distance_ly / (3261.56 / plx))
    if xs:
        w = lambda k: sum(1 for x in xs if 1 / k <= x <= k) / len(xs)
        print(f"  {q:12s} n={len(xs):4d}  within 5% {w(1.05):.0%}  10% {w(1.10):.0%}  25% {w(1.25):.0%}")
