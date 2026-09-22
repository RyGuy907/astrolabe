/**
 * Checks for "Suggest a target", run without a browser:
 *   npm run check:suggest
 */

import type { TargetModel } from "../src/api";
import { isGood, suggestTarget } from "../src/suggest";

let failures = 0;
function check(name: string, ok: boolean, detail = "") {
  if (!ok) failures++;
  console.log(`${ok ? "ok  " : "FAIL"} ${name}${detail ? `  (${detail})` : ""}`);
}

const T = (name: string, ra: number, dec: number, score: number | null,
           extra: Partial<TargetModel> = {}): TargetModel => ({
  name, display_name: name, group: "g", object_type: "Galaxy", messier: null,
  constellation: null, ra_deg: ra, dec_deg: dec, magnitude: 8, size_arcmin: 10,
  surface_brightness: 13, score, peak_altitude_deg: 60, peak_time: "2026-09-22T04:00:00Z",
  hours_above_floor: 5,
  best_window: { start: "2026-09-22T02:00:00Z", end: "2026-09-22T06:00:00Z" },
  moon_separation_deg: 90, contrast_margin: 1, visible_tonight: true, visible_late: false,
  too_faint: false, showpiece: false, distance_ly: null, diameter_ly: null, aliases: [],
  separation_arcsec: null, component_mags: null, discovered_by: null, discovered_year: null,
  about: null, notes: [], ...extra,
});

// Some good targets, and some that must never be offered.
const m31 = T("M31", 10.68, 41.27, 90, { showpiece: true });
const m32 = T("M32", 10.67, 40.87, 60);
const m33 = T("M33", 23.46, 30.66, 70, { showpiece: true });
const m13 = T("M13", 250.42, 36.46, 95, { showpiece: true });
const m92 = T("M92", 259.28, 43.14, 80);
const faint = T("Faint", 11, 41, 99, { too_faint: true });
const late = T("Late", 12, 42, 99, { visible_late: true });
const unscored = T("None", 11.5, 41.5, null);
const poor = T("Poor", 10.8, 41.4, 20);                      // right next to M31
const all = [m31, m32, m33, m13, m92, faint, late, unscored, poor];

check("too faint, visible late and unscored targets are never good",
      !isGood(faint) && !isGood(late) && !isGood(unscored));

// Many presses: only good ones, mostly the best.
const counts: Record<string, number> = {};
let seed = 1;
const rnd = () => ((seed = (seed * 16807) % 2147483647) - 1) / 2147483646;
for (let k = 0; k < 2000; k++) {
  const t = suggestTarget({ targets: all, exclude: new Set(), random: rnd })!;
  counts[t.name] = (counts[t.name] ?? 0) + 1;
}
check("a first suggestion is never faint, late, unscored or poor",
      !counts.Faint && !counts.Late && !counts.None && !counts.Poor, JSON.stringify(counts));
check("the best are suggested most", (counts.M13 ?? 0) > (counts.M32 ?? 0), JSON.stringify(counts));

// Pressing again gives something new until the good ones run out.
const seen = new Set<string>();
for (let k = 0; k < 5; k++) {
  const t = suggestTarget({ targets: all, exclude: seen, random: rnd });
  if (t) seen.add(t.name);
}
check("repeat presses don't repeat", seen.size === 5, [...seen].join(", "));
check("with every good one suggested, there is nothing left",
      suggestTarget({ targets: all, exclude: seen }) === null);

// During the night, what is up now comes first.
const early = T("Early", 100, 20, 85, { best_window: { start: "2026-09-22T01:00:00Z", end: "2026-09-22T02:30:00Z" } });
const nowPick = suggestTarget({ targets: [early, m13, m31], exclude: new Set(),
                                now: "2026-09-22T01:30:00Z", random: () => 0.99 });
check("during the session, a target well placed now wins", nowPick?.name === "Early", nowPick?.name);

// "Popular objects only": showpieces alone, still only good ones.
const popular = new Set<string>();
for (let k = 0; k < 200; k++) {
  const t = suggestTarget({ targets: all, exclude: new Set(), popularOnly: true, random: rnd });
  if (t) popular.add(t.name);
}
check("with popular objects only, only showpieces are suggested",
      [...popular].every((n) => ["M31", "M33", "M13"].includes(n)) && popular.size === 3,
      [...popular].join(", "));
check("with popular objects only and none good, nothing",
      suggestTarget({ targets: [m32, m92, faint], exclude: new Set(), popularOnly: true }) === null);

// The one open now is never offered again straight away.
const again = suggestTarget({ targets: [m13, m92], exclude: new Set(["M13"]) });
check("the target open now isn't suggested again", again?.name === "M92", again?.name);

console.log(failures ? `\n${failures} failed` : "\nall passed");
process.exit(failures ? 1 : 0);
