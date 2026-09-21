/**
 * The sky chart's view geometry: pure functions, so they can be checked
 * without a browser (`npm run check:sky`).
 *
 * Everything works on unit vectors in a "working frame": the horizon frame
 * (x east, y north, z up) for the as-seen chart, or J2000 (x to RA 0h, z to
 * the north celestial pole) for north-up. Either way the frame's z axis is
 * "up" on screen, which is why one projection serves both.
 *
 * Stereographic, centred on the view direction `c`: screen x runs along
 * increasing azimuth (as seen) or decreasing right ascension (north-up,
 * east to the left), screen y towards the frame's pole. Both come from the
 * same construction, x = c x z normalised, y = x x c.
 */

export type Vec = [number, number, number];

export const DEG = Math.PI / 180;

export const unit = (raDeg: number, decDeg: number): Vec => {
  const r = raDeg * DEG, d = decDeg * DEG;
  return [Math.cos(d) * Math.cos(r), Math.cos(d) * Math.sin(r), Math.sin(d)];
};
export const dot = (a: Vec, b: Vec) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
export const cross = (a: Vec, b: Vec): Vec =>
  [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
export const norm = (a: Vec): Vec => {
  const l = Math.hypot(a[0], a[1], a[2]) || 1;
  return [a[0] / l, a[1] / l, a[2] / l];
};
/** M v, for a row-major 3x3. */
export const apply = (m: number[][], v: Vec): Vec =>
  [dot(m[0] as Vec, v), dot(m[1] as Vec, v), dot(m[2] as Vec, v)];
/** M^T v -- the inverse, for a rotation. */
export const applyT = (m: number[][], v: Vec): Vec => [
  m[0][0] * v[0] + m[1][0] * v[1] + m[2][0] * v[2],
  m[0][1] * v[0] + m[1][1] * v[1] + m[2][1] * v[2],
  m[0][2] * v[0] + m[1][2] * v[1] + m[2][2] * v[2],
];

export const FOV_MIN = 2;
export const FOV_MAX = 180;

/** Faintest star shown at a field width: magnitude 8 -- a finder scope's
 *  worth -- at 5 degrees and closer, just under a magnitude brighter for each
 *  doubling of the field, down to 3 on the whole sky: the ~170 stars that
 *  make the constellations' shapes. */
export const limitFor = (fov: number) =>
  Math.max(3, Math.min(8, 8 - 0.95 * Math.log2(fov / 5)));

export interface View {
  w: number;
  h: number;
  c: Vec;
  xa: Vec;
  ya: Vec;
  /** Pixels per unit of stereographic distance. */
  s: number;
  /** Working vector -> [x, y, cos of distance from centre], or null when
   *  too far behind the centre to draw. */
  project: (v: Vec) => [number, number, number] | null;
  /** Screen pixel -> working unit vector. */
  unproject: (px: number, py: number) => Vec;
}

/** The view for centre `center` (working frame), `fov` degrees wide. */
export function makeView(center: Vec, fov: number, w: number, h: number): View {
  const c = norm(center);
  // At the pole itself there is no "up"; any perpendicular will do.
  let xa = cross(c, [0, 0, 1]);
  if (Math.hypot(xa[0], xa[1], xa[2]) < 1e-6) xa = [1, 0, 0];
  xa = norm(xa);
  const ya = cross(xa, c);
  // The field's full width spans 4 tan(fov/4) of stereographic distance.
  const s = w / (4 * Math.tan((fov * DEG) / 4));
  const project = (v: Vec): [number, number, number] | null => {
    const d = dot(v, c);
    if (d < -0.3) return null;
    const k = 2 / (1 + d);
    return [w / 2 + dot(v, xa) * k * s, h / 2 - dot(v, ya) * k * s, d];
  };
  const unproject = (px: number, py: number): Vec => {
    const sx = (px - w / 2) / s, sy = (h / 2 - py) / s;
    const r2 = sx * sx + sy * sy;
    const a = (4 - r2) / (4 + r2), b = 4 / (4 + r2);
    return norm([a * c[0] + b * (sx * xa[0] + sy * ya[0]),
                 a * c[1] + b * (sx * xa[1] + sy * ya[1]),
                 a * c[2] + b * (sx * xa[2] + sy * ya[2])]);
  };
  return { w, h, c, xa, ya, s, project, unproject };
}

/**
 * The horizon on screen, exactly. A stereographic projection maps every
 * circle on the sky to a circle on the plane -- or a straight line, when it
 * passes through the point opposite the view centre -- so the horizon is one
 * circle (a line when the view centre sits on the horizon itself).
 *
 * Drawn from samples instead, points far behind the view had to be dropped,
 * and joining what was left bridged the gap with a straight chord across the
 * sky; filling that outline put "ground" where there was sky.
 *
 * Only meaningful in the horizon frame (as seen), where z is the zenith.
 */
export type Horizon =
  | { kind: "circle"; cx: number; cy: number; r: number; groundInside: boolean }
  /** Through (x, y) along (dx, dy); ground on the side (gx, gy) points to. */
  | { kind: "line"; x: number; y: number; dx: number; dy: number; gx: number; gy: number };

export function horizonOnScreen(view: View): Horizon {
  const { c, xa, ya, s, w, h } = view;
  // Stereographic without the "too far behind" cutoff; only ever given
  // points at least 90 degrees from the point opposite the centre.
  const raw = (v: Vec): [number, number] => {
    const k = 2 / (1 + dot(v, c));
    return [w / 2 + dot(v, xa) * k * s, h / 2 - dot(v, ya) * k * s];
  };
  // Three horizon points: the one under the view centre, and two a quarter
  // turn either side of it. None is behind the view.
  let h1: Vec = [c[0], c[1], 0];
  h1 = Math.hypot(h1[0], h1[1]) < 1e-9 ? [1, 0, 0] : norm(h1);
  const h2 = norm(cross([0, 0, 1], h1));
  const h3: Vec = [-h2[0], -h2[1], 0];
  const [a, b, q] = [raw(h1), raw(h2), raw(h3)];

  const d = 2 * (a[0] * (b[1] - q[1]) + b[0] * (q[1] - a[1]) + q[0] * (a[1] - b[1]));
  const lineFrom = (): Horizon => {
    const len = Math.hypot(q[0] - b[0], q[1] - b[1]) || 1;
    const dx = (q[0] - b[0]) / len, dy = (q[1] - b[1]) / len;
    // Which side is ground: a point just below the horizon, in front.
    const g = raw(norm([c[0], c[1], c[2] - 0.3]));
    const side = (g[0] - b[0]) * -dy + (g[1] - b[1]) * dx;
    return { kind: "line", x: b[0], y: b[1], dx, dy,
             gx: -dy * Math.sign(side || 1), gy: dx * Math.sign(side || 1) };
  };
  if (Math.abs(d) < 1e-9 * w * w) return lineFrom();

  const a2 = a[0] ** 2 + a[1] ** 2, b2 = b[0] ** 2 + b[1] ** 2, q2 = q[0] ** 2 + q[1] ** 2;
  const cx = (a2 * (b[1] - q[1]) + b2 * (q[1] - a[1]) + q2 * (a[1] - b[1])) / d;
  const cy = (a2 * (q[0] - b[0]) + b2 * (a[0] - q[0]) + q2 * (b[0] - a[0])) / d;
  const r = Math.hypot(a[0] - cx, a[1] - cy);
  // Within a hair of the horizon the circle is kilometres across; canvas
  // arcs lose precision there, and a line is indistinguishable.
  if (r > 200 * w) return lineFrom();
  // The screen centre is the view direction: sky if it is above the horizon.
  const centreInside = Math.hypot(w / 2 - cx, h / 2 - cy) < r;
  return { kind: "circle", cx, cy, r, groundInside: centreInside === c[2] < 0 };
}

/** Whether pixel (px, py) is below the horizon, by the shape above. */
export function isGround(hz: Horizon, px: number, py: number): boolean {
  if (hz.kind === "circle") return (Math.hypot(px - hz.cx, py - hz.cy) < hz.r) === hz.groundInside;
  return (px - hz.x) * hz.gx + (py - hz.y) * hz.gy > 0;
}

/** The centre after shifting the view by (dx, dy) pixels, in one step. Close
 *  for small shifts, but the screen's "up" turns as the centre moves, so for
 *  anything that must stay under the pointer use `placeAt`. */
export function panned(view: View, dx: number, dy: number): Vec {
  return view.unproject(view.w / 2 - dx, view.h / 2 - dy);
}

/** The centre that puts the sky point `anchor` at pixel (px, py). Solved by
 *  repeatedly shifting by what is left over, which converges in two or three
 *  passes; across the pole, where "up" flips, no centre may be exact and the
 *  nearest found is kept. */
export function placeAt(center: Vec, fov: number, anchor: Vec, px: number, py: number,
                        w: number, h: number): Vec {
  let best = center, bestErr = Infinity;
  let next = center;
  for (let i = 0; i < 8; i++) {
    const view = makeView(next, fov, w, h);
    const p = view.project(anchor);
    if (!p) break;
    const err = Math.hypot(px - p[0], py - p[1]);
    if (err < bestErr) { best = next; bestErr = err; }
    if (err < 0.01) break;
    next = panned(view, px - p[0], py - p[1]);
  }
  return best;
}

/** Centre and width after zooming by `factor` about the pixel (px, py),
 *  keeping the sky point under it where it is. */
export function zoomed(center: Vec, fov: number, factor: number, px: number,
                       py: number, w: number, h: number): { center: Vec; fov: number } {
  const anchor = makeView(center, fov, w, h).unproject(px, py);
  const nextFov = Math.max(FOV_MIN, Math.min(FOV_MAX, fov * factor));
  return { center: placeAt(center, nextFov, anchor, px, py, w, h), fov: nextFov };
}
