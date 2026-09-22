/**
 * "Suggest a target": which deep-sky object to look at next.
 *
 * Only a choice among what the engine has already scored -- the astronomy
 * (visibility, altitude, sky brightness, the score itself) is all upstream.
 * What this adds is judgement about which of the good ones to hand over:
 *
 * * Nothing open: one of the best few tonight, weighted by score and a
 *   little towards the well-known showpieces, and not one already suggested,
 *   so pressing again gives a different good answer rather than the same one.
 *   During the observing session, what is well placed now comes first.
 * * A target open: the nearest target still good tonight -- a short hop
 *   across the sky, not a trip -- best-scoring among the closest few.
 *
 * Pure, so `npm run check:suggest` can test it without a browser.
 */

import type { TargetModel } from "./api";

export interface SuggestOptions {
  /** Targets to choose among (every group's rows). */
  targets: TargetModel[];
  /** Ids suggested already this night, not to be offered again. */
  exclude: ReadonlySet<string>;
  /** The target open now, if any: suggestions are then nearby ones. */
  near?: { id: string; ra: number; dec: number } | null;
  /** Now, as ISO; within a best window means "well placed now". */
  now?: string;
  /** Random number in [0, 1), injectable for testing. */
  random?: () => number;
}

/** How far a nearby suggestion may be before it isn't "nearby", degrees. */
export const NEARBY_DEG = 30;
/** How many of the best to choose among when nothing is open. */
const TOP = 8;

/** Angular separation of two RA/Dec positions, in degrees. */
export function separationDeg(ra1: number, dec1: number, ra2: number, dec2: number): number {
  const r = Math.PI / 180;
  const a = Math.sin(((dec2 - dec1) * r) / 2) ** 2 +
    Math.cos(dec1 * r) * Math.cos(dec2 * r) * Math.sin(((ra2 - ra1) * r) / 2) ** 2;
  return (2 * Math.asin(Math.min(1, Math.sqrt(a)))) / r;
}

/** Worth suggesting at all: up tonight in the session, bright enough for
 *  this sky, and scored. "Visible late" ones only come up after the session. */
export function isGood(t: TargetModel): boolean {
  return t.visible_tonight && !t.too_faint && !t.visible_late && t.score !== null;
}

const upAt = (t: TargetModel, now: string) =>
  !!t.best_window && t.best_window.start <= now && now <= t.best_window.end;

export function suggestTarget(opts: SuggestOptions): TargetModel | null {
  const random = opts.random ?? Math.random;
  const good = opts.targets.filter((t) => isGood(t) && !opts.exclude.has(t.name));
  if (!good.length) return null;
  // Never below this share of the night's best: a "nearby" suggestion must
  // still be worth the hop.
  const best = Math.max(...opts.targets.filter(isGood).map((t) => t.score!));
  // With every worthy one already suggested, nothing: the caller starts the
  // round again rather than being handed a poor target.
  const pool = good.filter((t) => t.score! >= best * 0.5);
  if (!pool.length) return null;

  if (opts.near) {
    const { id, ra, dec } = opts.near;
    const byDistance = pool
      .filter((t) => t.name !== id)
      .map((t) => ({ t, sep: separationDeg(ra, dec, t.ra_deg, t.dec_deg) }))
      .sort((a, b) => a.sep - b.sep);
    if (!byDistance.length) return null;
    const close = byDistance.filter((x) => x.sep <= NEARBY_DEG).slice(0, 3);
    // Among the closest few, the best; with none close, simply the nearest.
    const pick = close.length
      ? close.reduce((a, b) => (b.t.score! > a.t.score! ? b : a))
      : byDistance[0];
    return pick.t;
  }

  // Well placed now, if we are in the night and anything is.
  const now = opts.now;
  const current = now ? pool.filter((t) => upAt(t, now)) : [];
  const ranked = (current.length ? current : pool)
    .slice().sort((a, b) => b.score! - a.score!).slice(0, TOP);
  // Weighted by score, squared so the best are favoured, and showpieces
  // half as likely again: a first suggestion should usually be a crowd-pleaser.
  const weights = ranked.map((t) => t.score! ** 2 * (t.showpiece ? 1.5 : 1));
  let r = random() * weights.reduce((a, b) => a + b, 0);
  for (let i = 0; i < ranked.length; i++) {
    r -= weights[i];
    if (r < 0) return ranked[i];
  }
  return ranked[ranked.length - 1];
}
