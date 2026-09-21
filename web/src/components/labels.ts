/**
 * Label placement for the finder chart: no label over another, or over a
 * bright star.
 *
 * Greedy, in priority order -- the target, then bodies, constellation names,
 * deep-sky neighbours, then stars brightest first. Each label tries eight
 * positions around its point (east first, the atlas convention, then west,
 * above, below and the diagonals) at three distances, and takes the first
 * that collides with nothing already placed. Pushed out past the first
 * distance, it gets a leader line back to its point, so a displaced label
 * still says whose it is. A label that fits nowhere is dropped rather than
 * drawn over another: a missing name on a faint star costs less than two
 * unreadable ones.
 *
 * Text is measured by estimate, not by the DOM, since placement runs before
 * anything is rendered. 0.62 em per character plus padding: a first pass at
 * 0.56 was right for lower-case Latin and short for capitals and Greek
 * letters, and measured overlaps in dense fields came from exactly those.
 * Generous boxes err towards space, which is the side to err on.
 */

export interface LabelRequest {
  text: string;
  /** Anchor point, in chart pixels. */
  x: number;
  y: number;
  /** Radius of the mark the label belongs to, so it clears it. */
  r: number;
  size: number;
  /** Lower goes first and wins contested space. */
  priority: number;
  className: string;
  /** May be dropped when there is no room. Targets and bodies may not. */
  optional: boolean;
  /** The caller's handle for what the label names, carried through so a
   *  click on the label can find it. */
  tag?: number;
}

export interface PlacedLabel extends LabelRequest {
  lx: number;          // text x
  ly: number;          // text baseline y
  anchor: "start" | "end" | "middle";
  leader: [number, number, number, number] | null;
}

interface Box { x0: number; y0: number; x1: number; y1: number }
/** An occupied area: a placed label, or a bright star's dot (with its
 *  centre, so a star's own label can be told to ignore it). */
interface Taken extends Box { star?: { x: number; y: number } }

const overlaps = (a: Box, b: Box) =>
  a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;

/** Eight directions, as unit offsets, in the order they are tried. */
const DIRECTIONS: [number, number][] = [
  [1, 0], [-1, 0], [0, -1], [0, 1], [1, -1], [-1, -1], [1, 1], [-1, 1],
];
/** Extra distance beyond the mark for each ring of attempts, in pixels. */
const RINGS = [3, 16, 32];

export function placeLabels(requests: LabelRequest[],
                            obstacles: { x: number; y: number; r: number }[],
                            width: number, height: number): PlacedLabel[] {
  const taken: Taken[] = obstacles.map((o) => ({
    x0: o.x - o.r, y0: o.y - o.r, x1: o.x + o.r, y1: o.y + o.r,
    star: { x: o.x, y: o.y },
  }));
  const placed: PlacedLabel[] = [];

  for (const req of [...requests].sort((a, b) => a.priority - b.priority)) {
    const w = req.text.length * req.size * 0.62 + 6;
    const h = req.size * 1.35;
    let done = false;

    for (const [ringIndex, ring] of RINGS.entries()) {
      for (const [dx, dy] of DIRECTIONS) {
        // The box's near edge sits `reach` from the point along (dx, dy);
        // on a diagonal each axis takes 0.7 of it, so the gap stays even.
        const reach = (req.r + ring) * (dx !== 0 && dy !== 0 ? 0.7 : 1);
        const cx = dx > 0 ? req.x + reach + w / 2
                 : dx < 0 ? req.x - reach - w / 2 : req.x;
        const cy = dy > 0 ? req.y + reach + h / 2
                 : dy < 0 ? req.y - reach - h / 2 : req.y;
        const box = { x0: cx - w / 2, y0: cy - h / 2, x1: cx + w / 2, y1: cy + h / 2 };
        if (box.x0 < 2 || box.y0 < 2 || box.x1 > width - 2 || box.y1 > height - 2) continue;
        // A label may touch its own star's dot -- and nothing else. This
        // used to exempt any occupied box containing the label's point,
        // which let M29 sit on NGC 6888's label because M29's position fell
        // inside it.
        const own = (t: Taken) => t.star !== undefined &&
          Math.abs(t.star.x - req.x) < 0.5 && Math.abs(t.star.y - req.y) < 0.5;
        if (taken.some((t) => overlaps(box, t) && !own(t))) continue;
        taken.push(box);
        placed.push({
          ...req,
          lx: cx,
          ly: cy + req.size * 0.36,
          anchor: "middle",
          leader: ringIndex === 0 ? null : leaderFor(req, box),
        });
        done = true;
        break;
      }
      if (done) break;
    }

    // Nowhere free. Required labels still go on, at the default spot, so the
    // target is never unnamed; optional ones are left off.
    if (!done && !req.optional) {
      placed.push({ ...req, lx: req.x + req.r + 4, ly: req.y + req.size * 0.36,
                    anchor: "start", leader: null });
    }
  }
  return placed;
}

/** From the edge of the mark to the nearest point of the label's box. */
function leaderFor(req: LabelRequest, box: Box): [number, number, number, number] {
  const tx = Math.max(box.x0, Math.min(req.x, box.x1));
  const ty = Math.max(box.y0, Math.min(req.y, box.y1));
  const len = Math.hypot(tx - req.x, ty - req.y) || 1;
  const sx = req.x + ((tx - req.x) / len) * (req.r + 1.5);
  const sy = req.y + ((ty - req.y) / len) * (req.r + 1.5);
  return [sx, sy, tx, ty];
}
