/**
 * "Suggest a target": which deep-sky object to look at next.
 *
 * Only a choice among what the engine has already scored -- the astronomy
 * (visibility, altitude, sky brightness, the score itself) is all upstream.
 * What this adds is judgement about which of the good ones to hand over:
 * one of the best few tonight, weighted by score and a little towards the
 * well-known showpieces, and not one already suggested, so pressing again
 * gives a different good answer rather than the same one. During the
 * observing session, what is well placed now comes first. With "popular
 * objects only" on, only showpieces are offered.
 *
 * Pure, so `npm run check:suggest` can test it without a browser.
 */

import type { TargetModel } from "./api";

export interface SuggestOptions {
  /** Targets to choose among (every group's rows). */
  targets: TargetModel[];
  /** Ids suggested already this night, or open now, not to be offered. */
  exclude: ReadonlySet<string>;
  /** Only the well-known showpieces, as the Deep Sky list's filter. */
  popularOnly?: boolean;
  /** Now, as ISO; within a best window means "well placed now". */
  now?: string;
  /** Random number in [0, 1), injectable for testing. */
  random?: () => number;
}

/** How many of the best to choose among. */
const TOP = 8;

/** Worth suggesting at all: up tonight in the session, bright enough for
 *  this sky, and scored. "Visible late" ones only come up after the session. */
export function isGood(t: TargetModel): boolean {
  return t.visible_tonight && !t.too_faint && !t.visible_late && t.score !== null;
}

const upAt = (t: TargetModel, now: string) =>
  !!t.best_window && t.best_window.start <= now && now <= t.best_window.end;

export function suggestTarget(opts: SuggestOptions): TargetModel | null {
  const random = opts.random ?? Math.random;
  const eligible = opts.targets.filter((t) => isGood(t) && (!opts.popularOnly || t.showpiece));
  if (!eligible.length) return null;
  // Never below half the night's best among those eligible. With every
  // worthy one already suggested, nothing: the caller starts the round
  // again rather than being handed a poor target.
  const best = Math.max(...eligible.map((t) => t.score!));
  const pool = eligible.filter((t) => t.score! >= best * 0.5 && !opts.exclude.has(t.name));
  if (!pool.length) return null;

  // Well placed now, if we are in the night and anything is.
  const now = opts.now;
  const current = now ? pool.filter((t) => upAt(t, now)) : [];
  const ranked = (current.length ? current : pool)
    .slice().sort((a, b) => b.score! - a.score!).slice(0, TOP);
  // Weighted by score, squared so the best are favoured, and showpieces
  // half as likely again: a suggestion should usually be a crowd-pleaser.
  const weights = ranked.map((t) => t.score! ** 2 * (t.showpiece ? 1.5 : 1));
  let r = random() * weights.reduce((a, b) => a + b, 0);
  for (let i = 0; i < ranked.length; i++) {
    r -= weights[i];
    if (r < 0) return ranked[i];
  }
  return ranked[ranked.length - 1];
}
