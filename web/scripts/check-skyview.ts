/**
 * Checks for the sky chart's view geometry, run without a browser:
 *   npm run check:sky
 *
 * The chart is a canvas, so there is nothing to query in the DOM; what can
 * go wrong -- a mirrored axis, a pan that drifts, a zoom that slides off the
 * pointer -- is all in this arithmetic.
 */

import {
  DEG, dot, easeInOut, horizonOnScreen, isGround, limitFor, makeView, norm, placeAt, slerp, unit,
  zoomed,
  type Vec,
} from "../src/components/skyview";

let failures = 0;
function check(name: string, ok: boolean, detail = "") {
  if (!ok) failures++;
  console.log(`${ok ? "ok  " : "FAIL"} ${name}${detail ? `  (${detail})` : ""}`);
}
const close = (a: number, b: number, tol: number) => Math.abs(a - b) <= tol;
const angle = (a: Vec, b: Vec) => Math.acos(Math.max(-1, Math.min(1, dot(a, b)))) / DEG;

const W = 600, H = 600;
/** Horizon-frame direction from altitude and azimuth. */
const altaz = (alt: number, az: number): Vec =>
  [Math.cos(alt * DEG) * Math.sin(az * DEG), Math.cos(alt * DEG) * Math.cos(az * DEG),
   Math.sin(alt * DEG)];

// --- the centre is the middle of the screen -------------------------------
{
  const c = altaz(40, 250);
  const v = makeView(c, 20, W, H);
  const p = v.project(c)!;
  check("centre projects to the middle", close(p[0], W / 2, 1e-9) && close(p[1], H / 2, 1e-9));
}

// --- as seen: azimuth increases to the right, the zenith is up ------------
{
  const v = makeView(altaz(40, 250), 20, W, H);
  const right = v.project(altaz(40, 255))!;
  const up = v.project(altaz(45, 250))!;
  check("as seen: larger azimuth is to the right", right[0] > W / 2, `x=${right[0].toFixed(1)}`);
  check("as seen: higher altitude is up", up[1] < H / 2, `y=${up[1].toFixed(1)}`);
}

// --- north up: north is up and east (larger RA) is LEFT --------------------
{
  const v = makeView(unit(250, 36), 20, W, H);
  const east = v.project(unit(255, 36))!;
  const north = v.project(unit(250, 41))!;
  check("north up: east is to the left", east[0] < W / 2, `x=${east[0].toFixed(1)}`);
  check("north up: north is up", north[1] < H / 2, `y=${north[1].toFixed(1)}`);
}

// --- the field is as wide as it says ---------------------------------------
for (const fov of [2, 20, 90, 180]) {
  const v = makeView(altaz(40, 250), fov, W, H);
  const width = angle(v.unproject(0, H / 2), v.unproject(W, H / 2));
  check(`a ${fov} degree view spans ${fov} degrees`, close(width, fov, 1e-6),
        `${width.toFixed(6)}`);
}

// --- project and unproject are inverses ------------------------------------
{
  const v = makeView(altaz(62, 31), 35, W, H);
  let worst = 0;
  for (const [px, py] of [[10, 10], [300, 300], [590, 17], [123, 456], [600, 600]]) {
    const p = v.project(v.unproject(px, py))!;
    worst = Math.max(worst, Math.hypot(p[0] - px, p[1] - py));
  }
  check("unproject then project returns the same pixel", worst < 1e-6, `worst ${worst.toExponential(1)} px`);
}

// --- panning keeps the grabbed point under the pointer ----------------------
{
  const c0 = altaz(40, 250);
  const v0 = makeView(c0, 20, W, H);
  const grabbed = v0.unproject(200, 350);
  const c1 = placeAt(c0, 20, grabbed, 260, 325, W, H);
  const p = makeView(c1, 20, W, H).project(grabbed)!;
  check("dragging keeps the grabbed point under the pointer",
        Math.hypot(p[0] - 260, p[1] - 325) < 0.05, `lands at ${p[0].toFixed(3)}, ${p[1].toFixed(3)}`);
}

// --- a long drag in many small steps doesn't drift ---------------------------
{
  // How the chart drags: the grab is remembered, and each move re-solves
  // from where the view is now, so steps can't accumulate error.
  let c = altaz(30, 100);
  const grabbed = makeView(c, 40, W, H).unproject(100, 300);
  let x = 100, y = 300;
  for (let i = 0; i < 200; i++) {
    x += 2; y -= 0.7;
    c = placeAt(c, 40, grabbed, x, y, W, H);
  }
  const p = makeView(c, 40, W, H).project(grabbed)!;
  check("a 400 px drag in 200 steps ends under the pointer",
        Math.hypot(p[0] - x, p[1] - y) < 0.05, `off by ${Math.hypot(p[0] - x, p[1] - y).toFixed(4)} px`);
}

// --- zooming keeps the point under the pointer -----------------------------
for (const factor of [0.5, 1.8]) {
  const c0 = altaz(40, 250);
  const anchor = makeView(c0, 30, W, H).unproject(450, 150);
  const { center, fov } = zoomed(c0, 30, factor, 450, 150, W, H);
  const p = makeView(center, fov, W, H).project(anchor)!;
  check(`zooming x${factor} keeps the point under the pointer`,
        Math.hypot(p[0] - 450, p[1] - 150) < 0.05, `off by ${Math.hypot(p[0] - 450, p[1] - 150).toFixed(3)} px`);
}

// --- the pole has no "up", but must still draw -----------------------------
{
  const v = makeView([0, 0, 1], 30, W, H);
  const p = v.project(norm([0.05, 0, 1]));
  check("a view centred on the zenith still projects", p !== null && Number.isFinite(p[0]));
}

// --- the horizon: ground exactly where the sky is below it -----------------
// Every pixel of a grid is checked against the truth -- unproject it and see
// whether it points below the horizon -- across views that broke the old
// sampled outline: wide fields, looking up, looking down, and exactly level.
for (const [alt, az, fov] of [[20, 180, 90], [40, 250, 180], [89, 30, 120], [90, 0, 180],
                              [0, 90, 60], [0.001, 90, 60], [-15, 300, 100], [5, 10, 20],
                              [60, 120, 180], [-60, 45, 150]]) {
  const v = makeView(altaz(alt, az), fov, W, H);
  const hz = horizonOnScreen(v);
  let wrong = 0, n = 0;
  for (let py = 3; py < H; py += 12) for (let px = 3; px < W; px += 12) {
    const truth = v.unproject(px, py)[2];
    if (Math.abs(truth) < 0.004) continue;          // on the line itself
    n++;
    if (isGround(hz, px, py) !== truth < 0) wrong++;
  }
  check(`horizon at alt ${alt}, ${fov} degrees wide: ground exactly below it (${hz.kind})`,
        wrong === 0, `${wrong} of ${n} pixels wrong`);
}

// --- gliding to a target ---------------------------------------------------
{
  const a = unit(10, 40), b = unit(250, 36);
  const total = angle(a, b);
  const mid = slerp(a, b, 0.5);
  check("a glide passes halfway along the great circle",
        close(angle(a, mid), total / 2, 1e-6) && close(angle(mid, b), total / 2, 1e-6));
  check("a glide starts and ends where it should",
        angle(slerp(a, b, 0), a) < 1e-3 && angle(slerp(a, b, 1), b) < 1e-3);
  const opp: Vec = [-a[0], -a[1], -a[2]];
  const half = slerp(a, opp, 0.5);
  check("a glide to the opposite point still goes somewhere sensible",
        close(angle(a, half), 90, 1e-6) && angle(slerp(a, opp, 1), opp) < 1e-3);
  check("easing starts at rest and ends at rest",
        easeInOut(0) === 0 && easeInOut(1) === 1 && easeInOut(0.05) < 0.01 && easeInOut(0.95) > 0.99);
}

// --- detail follows zoom ----------------------------------------------------
check("finder field shows to magnitude 8", limitFor(5) === 8 && limitFor(2) === 8);
check("each doubling of the field drops about a magnitude",
      close(limitFor(10), 7.05, 1e-9) && close(limitFor(20), 6.1, 1e-9));
check("the whole sky shows only the major stars", close(limitFor(180), 3.09, 0.01));
check("detail only ever grows as you zoom in",
      [180, 120, 60, 30, 10, 5, 2].every((f, i, a) => i === 0 || limitFor(f) >= limitFor(a[i - 1])));

console.log(failures ? `\n${failures} failed` : "\nall passed");
process.exit(failures ? 1 : 0);
