/**
 * An interactive sky chart for star hopping: drag to pan, wheel or pinch to
 * zoom, click an object to go to it.
 *
 * Opens as a tab over the altitude chart. The work is split so that panning
 * costs nothing on the server:
 *
 * * The static sky -- 41,411 stars to magnitude 8, the constellation figures,
 *   the showpieces -- arrives once from `/api/sky/catalog` and is cached.
 * * Each moment needs only `/api/sky/frame`: the rotation from the
 *   catalogue's frame to the observer's horizon, the same for every star,
 *   and where the Moon and planets are. The astronomy is inside that
 *   rotation, computed by the engine; this component only multiplies.
 *
 * Drawn on a canvas, since thousands of stars as SVG nodes would not pan
 * smoothly. Detail follows zoom: fainter stars, more names, the showpieces
 * and the faint constellations appear as the field narrows. Labels are
 * placed without overlaps (see `labels.ts`) once the view settles; while it
 * is being dragged only the target and the planets are named.
 *
 * Clicking an object, a planet or either's label goes to it; clicking a star
 * or its name opens a card saying what the star is (`StarCard`).
 *
 * Stereographic throughout: it keeps shapes true from a 2-degree finder
 * field to the whole sky, and at finder scales it is indistinguishable from
 * the gnomonic projection printed charts use.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, type SkyCatalog, type SkyFrame } from "../api";
import { formatTime } from "../format";
import { BODY_COLORS } from "./AltitudeChart";
import { placeLabels, type LabelRequest } from "./labels";
import { StarCard } from "./StarCard";
import {
  apply, applyT, DEG, dot, easeInOut, horizonOnScreen, limitFor, makeView, norm, panned,
  placeAt, slerp, unit, zoomed,
  type Vec, type View,
} from "./skyview";

/** What the chart is centred on: a catalogue object or a solar-system body. */
export interface FinderSubject {
  kind: "target" | "body";
  /** Catalogue id (NGC6205) or body name (saturn). */
  id: string;
  /** Shown on the tab. */
  label: string;
  /** When to draw the sky, as ISO UTC. */
  at: string;
  /** J2000 position, for catalogue objects; bodies come from the frame. */
  ra?: number;
  dec?: number;
  /** Field width to show it at. Unset keeps the current zoom, unless that
   *  is so far out or in that the target would be lost. */
  fov?: number;
}

interface Props {
  subject: FinderSubject;
  location: string;
  timeZone: string;
  /** The night's bounds, so the time stepper stays inside it. */
  nightStart: string | null;
  nightEnd: string | null;
  /** Clicking an object or planet on the chart: go to it. */
  onSelect?: (subject: FinderSubject) => void;
}

type Layer = "constellations" | "names" | "objects";

const LAYERS: { key: Layer; label: string }[] = [
  { key: "constellations", label: "Constellations" },
  { key: "names", label: "Star names" },
  { key: "objects", label: "Other objects" },
];
const STEP_MS = 30 * 60_000;
/** The field the chart opens at, and goes back to on "Recenter". */
export const DEFAULT_FOV = 20;
/** Outside these, a new subject is shown at the default width instead. */
const KEEP_FOV_MIN = 4;
const KEEP_FOV_MAX = 70;
/** A Telrad's three circles, by diameter in degrees. */
const TELRAD_RINGS = [0.5, 2, 4];
/** The true field of a standard 7x50 finder scope, diameter in degrees. */
const FINDER_FIELD = 7;

/** The catalogue, fetched once per page load and shared by every chart. */
let catalogPromise: Promise<SkyCatalog> | null = null;
const loadCatalog = () => (catalogPromise ??= api.skyCatalog().catch((e) => {
  catalogPromise = null;
  throw e;
}));

function stored<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}
function store(key: string, value: unknown) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* a display preference is not worth breaking the page over */
  }
}

function compass(az: number): string {
  const names = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                 "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
  return names[Math.round((((az % 360) + 360) % 360) / 22.5) % 16];
}

/** What a click can pick: an object or body to go to, or a star to describe. */
type Pick = { kind: "go"; subject: FinderSubject } | { kind: "star"; index: number };
const pickKey = (p: Pick) => p.kind === "go" ? `${p.subject.kind}:${p.subject.id}` : `star:${p.index}`;

/** Something on screen that a click can land on: a mark (within `reach` of
 *  its point) or a label (inside its box). Lower `rank` wins a tie, so an
 *  object beats the star it sits beside. */
interface Hit {
  x: number; y: number; reach: number; rank: number; pick: Pick;
  box?: { x0: number; y0: number; x1: number; y1: number };
}

/** The best hit at a pixel, or null. */
function hitAt(hits: Hit[], px: number, py: number): Hit | null {
  let best: Hit | null = null, bestScore = Infinity;
  for (const h of hits) {
    const inBox = h.box && px >= h.box.x0 && px <= h.box.x1 && py >= h.box.y0 && py <= h.box.y1;
    const d = Math.hypot(h.x - px, h.y - py);
    if (!inBox && d > h.reach) continue;
    const score = h.rank * 1000 + (inBox ? 0 : d);
    if (score < bestScore) { best = h; bestScore = score; }
  }
  return best;
}

export function FinderChart({ subject, location, timeZone, nightStart, nightEnd,
                              onSelect }: Props) {
  const [catalog, setCatalog] = useState<SkyCatalog | null>(null);
  const [frame, setFrame] = useState<SkyFrame | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [orientation, setOrientation] = useState<"sky" | "north">("sky");
  const [at, setAt] = useState(subject.at);
  const [layers, setLayers] = useState<Record<Layer, boolean>>(() =>
    stored("astro:finder-layers", { constellations: true, names: true, objects: true }));
  //: Shown under the chart; the live value is in `fov`.
  const [fovShown, setFovShown] = useState(DEFAULT_FOV);

  useEffect(() => store("astro:finder-layers", layers), [layers]);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  // The view, in refs so a drag redraws without a React render per frame.
  const centerEq = useRef<Vec>([1, 0, 0]);        // J2000 unit vector
  const fov = useRef(DEFAULT_FOV);                          // field width, degrees
  const settled = useRef(true);                    // not mid-drag: full labels
  const hits = useRef<Hit[]>([]);
  // Under the pointer, by pick key: highlighted rather than changing the
  // cursor, which would jump between hand shapes.
  const hovered = useRef<string | null>(null);
  // A clicked star's card, where it was clicked.
  const [card, setCard] = useState<{ index: number; x: number; y: number } | null>(null);
  const cardKey = card ? `star:${card.index}` : null;
  const frameRequest = useRef(0);
  const settleTimer = useRef(0);

  useEffect(() => {
    loadCatalog().then(setCatalog)
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    api.skyFrame(location, at, controller.signal).then(setFrame)
      .catch((e) => {
        if ((e as Error)?.name !== "AbortError") {
          setError(e instanceof ApiError ? e.message : String(e));
        }
      });
    return () => controller.abort();
  }, [location, at]);

  // The subject's J2000 direction: its catalogue position, or for a body
  // wherever the frame says it is at this moment.
  const subjectEq = useMemo<Vec | null>(() => {
    if (subject.kind === "target" && subject.ra !== undefined && subject.dec !== undefined) {
      return unit(subject.ra, subject.dec);
    }
    const body = frame?.bodies.find((b) => b.name === subject.id);
    return body ? unit(body.ra, body.dec) : null;
  }, [subject, frame]);

  // A new subject recenters on it; a click on the chart keeps the time,
  // a pick from the list starts at the subject's own best time.
  useEffect(() => setAt(subject.at), [subject.at]);
  useEffect(() => setCard(null), [at, orientation, subject.id]);
  // Each new subject -- a new selection, even of the same object again --
  // is glided to: along the great circle from where the view is, over about
  // half a second, easing in and out, so the eye can follow where it went.
  // The first one, when the chart opens, is simply shown. A body's position
  // arrives with the frame, so the glide waits for it.
  const shown = useRef<FinderSubject | null>(null);
  useEffect(() => {
    if (!subjectEq || shown.current === subject) return;
    const first = shown.current === null;
    shown.current = subject;
    const width = subject.fov ??
      (fov.current < KEEP_FOV_MIN || fov.current > KEEP_FOV_MAX ? DEFAULT_FOV : fov.current);
    if (first) {
      centerEq.current = subjectEq;
      fov.current = width;
      setFovShown(Math.round(width));
      schedule();
    } else {
      glideTo(subjectEq, width);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subjectEq, subject]);

  // --- the static sky, as unit vectors ---
  const sky = useMemo(() => {
    if (!catalog) return null;
    const n = catalog.ra.length;
    const vx = new Float64Array(n), vy = new Float64Array(n), vz = new Float64Array(n);
    for (let i = 0; i < n; i++) {
      const [x, y, z] = unit(catalog.ra[i], catalog.dec[i]);
      vx[i] = x; vy[i] = y; vz[i] = z;
    }
    const figures = Object.values(catalog.constellations).map((c) => ({
      name: c.name, rank: c.rank,
      label: c.label ? unit(c.label[0], c.label[1]) : null,
      lines: c.lines.map((run) => run.map(([r, d]) => unit(r, d))),
    }));
    const objects = catalog.objects.map((o) => ({ ...o, v: unit(o.ra, o.dec) }));
    return { n, vx, vy, vz, mag: catalog.mag, labels: catalog.labels, figures, objects };
  }, [catalog]);

  // --- the stars in the working frame: horizon for "as seen", J2000 for
  //     north-up. Recomputed only when the moment or the orientation changes.
  const working = useMemo(() => {
    if (!sky || !frame) return null;
    if (orientation === "north") return { wx: sky.vx, wy: sky.vy, wz: sky.vz };
    const m = frame.matrix;
    const wx = new Float64Array(sky.n), wy = new Float64Array(sky.n), wz = new Float64Array(sky.n);
    for (let i = 0; i < sky.n; i++) {
      const x = sky.vx[i], y = sky.vy[i], z = sky.vz[i];
      wx[i] = m[0][0] * x + m[0][1] * y + m[0][2] * z;
      wy[i] = m[1][0] * x + m[1][1] * y + m[1][2] * z;
      wz[i] = m[2][0] * x + m[2][1] * y + m[2][2] * z;
    }
    return { wx, wy, wz };
  }, [sky, frame, orientation]);

  /** J2000 -> working frame, and back. */
  const toWork = useCallback((v: Vec): Vec =>
    orientation === "sky" && frame ? apply(frame.matrix, v) : v, [orientation, frame]);
  const fromWork = useCallback((v: Vec): Vec =>
    orientation === "sky" && frame ? applyT(frame.matrix, v) : v, [orientation, frame]);

  /** The projection for the current view: working vector -> screen pixel. */
  const size = () => {
    const canvas = canvasRef.current;
    return { w: canvas ? canvas.clientWidth : 560, h: canvas ? canvas.clientHeight : 560 };
  };
  const view = (): View => {
    const { w, h } = size();
    return makeView(toWork(centerEq.current), fov.current, w, h);
  };

  const schedule = () => {
    cancelAnimationFrame(frameRequest.current);
    frameRequest.current = requestAnimationFrame(() => draw());
  };
  /** Mark the view as moving: cheap labels now, full ones once it stops. */
  const moving = () => {
    setCard(null);
    settled.current = false;
    window.clearTimeout(settleTimer.current);
    settleTimer.current = window.setTimeout(() => { settled.current = true; schedule(); }, 150);
    schedule();
  };

  // --- drawing ---
  const draw = () => {
    const canvas = canvasRef.current;
    if (!canvas || !sky || !working || !frame) return;
    const dpr = window.devicePixelRatio || 1;
    const cw = canvas.clientWidth, ch = canvas.clientHeight;
    if (canvas.width !== Math.round(cw * dpr)) canvas.width = Math.round(cw * dpr);
    if (canvas.height !== Math.round(ch * dpr)) canvas.height = Math.round(ch * dpr);
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const v = view();
    const { w, h, project } = v;
    const f = fov.current;
    const limit = limitFor(f);
    const newHits: Hit[] = [];
    // Picks for labels, by the tag each label carries.
    const tagged: Pick[] = [];
    const tag = (pick: Pick) => tagged.push(pick) - 1;

    ctx.fillStyle = "#05070d";
    ctx.fillRect(0, 0, w, h);

    // As seen, what is below the horizon is under the ground: drawn, then
    // covered by it, and neither named nor clickable.
    const belowGround = (wv: Vec) => orientation === "sky" && wv[2] < 0;

    // Constellation figures: at the widest zooms only the major ones.
    const maxRank = f > 100 ? 2 : 3;
    if (layers.constellations) {
      ctx.strokeStyle = "rgba(110, 150, 255, 0.35)";
      ctx.lineWidth = 1.1;
      ctx.beginPath();
      for (const fig of sky.figures) {
        if (fig.rank > maxRank) continue;
        for (const run of fig.lines) {
          let prev: [number, number, number] | null = null;
          for (const p of run) {
            const q = project(toWork(p));
            if (q && prev) { ctx.moveTo(prev[0], prev[1]); ctx.lineTo(q[0], q[1]); }
            prev = q;
          }
        }
      }
      ctx.stroke();
    }

    // Stars: brightest first, so everything to the limit is a prefix.
    ctx.fillStyle = "#f2f4ff";
    const starLabels: LabelRequest[] = [];
    const bright: { x: number; y: number; r: number }[] = [];
    // Names for the brighter stars in view: only the very brightest on the
    // whole sky, down to magnitude 5.5 in a finder field.
    const nameLimit = Math.max(1, limit - 2.5);
    for (let i = 0; i < sky.n; i++) {
      const m = sky.mag[i];
      if (m > limit) break;
      const p = project([working.wx[i], working.wy[i], working.wz[i]]);
      if (!p || p[0] < -8 || p[1] < -8 || p[0] > w + 8 || p[1] > h + 8) continue;
      const r = Math.max(0.6, Math.min(7, 0.9 + (limit - m) * 0.9));
      ctx.beginPath();
      ctx.arc(p[0], p[1], r, 0, 2 * Math.PI);
      ctx.fill();
      if (orientation === "sky" && working.wz[i] < 0) continue;
      if (r >= 2.4) bright.push({ x: p[0], y: p[1], r: r + 1 });
      const pick: Pick = { kind: "star", index: i };
      newHits.push({ x: p[0], y: p[1], reach: Math.max(6, r + 3), rank: 2, pick });
      const label = sky.labels[String(i)];
      if (label && layers.names && m <= nameLimit) {
        starLabels.push({ text: label, x: p[0], y: p[1], r, size: 10,
                          priority: 10 + m, className: "#8a94ad", optional: true,
                          tag: tag(pick) });
      }
    }

    const labels: LabelRequest[] = [];

    // Showpieces, from a 70-degree field inward; named from 45.
    if (layers.objects && f <= 70) {
      ctx.lineWidth = 1.2;
      ctx.strokeStyle = "#7fd6a0";
      for (const o of sky.objects) {
        if (subject.kind === "target" && o.id === subject.id) continue;
        const ov = toWork(o.v);
        const p = project(ov);
        if (!p || p[0] < 0 || p[1] < 0 || p[0] > w || p[1] > h) continue;
        drawObject(ctx, o.group, p[0], p[1]);
        if (belowGround(ov)) continue;
        const pick: Pick = { kind: "go", subject: {
          kind: "target", id: o.id, label: o.name, at, ra: o.ra, dec: o.dec } };
        newHits.push({ x: p[0], y: p[1], reach: 12, rank: 0, pick });
        if (f <= 45) {
          labels.push({ text: o.label, x: p[0], y: p[1], r: 6, size: 10, priority: 3,
                        className: "#9adfb4", optional: true, tag: tag(pick) });
        }
      }
    }

    // The Moon and planets, always -- they are the brightest things there.
    if (layers.objects) {
      for (const b of frame.bodies) {
        if (subject.kind === "body" && b.name === subject.id) continue;
        const bv = toWork(unit(b.ra, b.dec));
        const p = project(bv);
        if (!p || p[0] < 0 || p[1] < 0 || p[0] > w || p[1] > h) continue;
        ctx.fillStyle = BODY_COLORS[b.name] ?? "#fff";
        ctx.beginPath();
        ctx.arc(p[0], p[1], b.name === "moon" ? 7 : 4.5, 0, 2 * Math.PI);
        ctx.fill();
        if (belowGround(bv)) continue;
        const name = b.name[0].toUpperCase() + b.name.slice(1);
        const pick: Pick = { kind: "go", subject: { kind: "body", id: b.name, label: name, at } };
        newHits.push({ x: p[0], y: p[1], reach: 12, rank: 0, pick });
        labels.push({ text: name, x: p[0], y: p[1], r: 6, size: 10, priority: 1,
                      className: "#d8dcff", optional: false, tag: tag(pick) });
      }
    }

    // The ground, as seen: the horizon is an exact circle (or line) on this
    // projection -- see `horizonOnScreen` -- filled on the ground's side,
    // over the stars and figures below it.
    if (orientation === "sky") {
      const hz = horizonOnScreen(v);
      ctx.beginPath();
      if (hz.kind === "circle") {
        ctx.arc(hz.cx, hz.cy, hz.r, 0, 2 * Math.PI);
        if (!hz.groundInside) ctx.rect(-w, -h, 3 * w, 3 * h);
      } else {
        const far = 4 * (w + h);
        ctx.moveTo(hz.x - hz.dx * far, hz.y - hz.dy * far);
        ctx.lineTo(hz.x + hz.dx * far, hz.y + hz.dy * far);
        ctx.lineTo(hz.x + hz.dx * far + hz.gx * far, hz.y + hz.dy * far + hz.gy * far);
        ctx.lineTo(hz.x - hz.dx * far + hz.gx * far, hz.y - hz.dy * far + hz.gy * far);
        ctx.closePath();
      }
      ctx.fillStyle = "rgba(38, 29, 20, 0.88)";
      ctx.fill("evenodd");
      ctx.strokeStyle = "rgba(200, 170, 130, 0.7)";
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      if (hz.kind === "circle") ctx.arc(hz.cx, hz.cy, hz.r, 0, 2 * Math.PI);
      else {
        const far = 4 * (w + h);
        ctx.moveTo(hz.x - hz.dx * far, hz.y - hz.dy * far);
        ctx.lineTo(hz.x + hz.dx * far, hz.y + hz.dy * far);
      }
      ctx.stroke();
    }

    // Constellation names, on wide views.
    if (layers.constellations && f >= 25) {
      for (const fig of sky.figures) {
        if (fig.rank > maxRank || !fig.label) continue;
        const p = project(toWork(fig.label));
        if (!p || p[0] < 0 || p[1] < 0 || p[0] > w || p[1] > h) continue;
        labels.push({ text: fig.name, x: p[0], y: p[1], r: 0, size: 11, priority: 2,
                      className: "italic:rgba(140,170,255,0.75)", optional: true });
      }
    }

    // The subject: crosshair, the Telrad's circles in red, and a 7x50
    // finder's field in amber, dashed -- the view to expect once the Telrad
    // has put the finder on the spot. Each only where it is big enough to
    // mean something.
    if (subjectEq) {
      const p = project(toWork(subjectEq));
      if (p) {
        const k = (2 / (1 + p[2])) * v.s * DEG;       // pixels per degree there
        if (f <= 60) {
          ctx.strokeStyle = "rgba(255, 90, 80, 0.55)";
          ctx.lineWidth = 1.2;
          for (const ring of TELRAD_RINGS) {
            const radius = (ring / 2) * k;
            if (radius < 4) continue;
            ctx.beginPath();
            ctx.arc(p[0], p[1], radius, 0, 2 * Math.PI);
            ctx.stroke();
          }
          const finder = (FINDER_FIELD / 2) * k;
          if (finder >= 12) {
            ctx.strokeStyle = "rgba(244, 201, 93, 0.7)";
            ctx.setLineDash([6, 4]);
            ctx.beginPath();
            ctx.arc(p[0], p[1], finder, 0, 2 * Math.PI);
            ctx.stroke();
            ctx.setLineDash([]);
            if (finder > 40) {
              ctx.font = "10px system-ui, sans-serif";
              ctx.textAlign = "center";
              ctx.fillStyle = "rgba(244, 201, 93, 0.85)";
              ctx.fillText("7×50 finder", p[0], p[1] - finder - 5);
            }
          }
        }
        ctx.strokeStyle = "#ff6a5c";
        ctx.lineWidth = 1.6;
        ctx.beginPath();
        for (const [a, b] of [[3, 9], [-9, -3]]) {
          ctx.moveTo(p[0] + a, p[1]); ctx.lineTo(p[0] + b, p[1]);
          ctx.moveTo(p[0], p[1] + a); ctx.lineTo(p[0], p[1] + b);
        }
        ctx.stroke();
        labels.push({ text: subject.label.replace(/\s*\(.*\)$/, ""), x: p[0], y: p[1],
                      r: 10, size: 11.5, priority: 0, className: "bold:#ff8a7e",
                      optional: false });
      }
    }

    // Compass points on the horizon, as seen.
    ctx.font = "600 11px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillStyle = "rgba(220, 200, 160, 0.9)";
    if (orientation === "sky") {
      for (const [name, az] of [["N", 0], ["NE", 45], ["E", 90], ["SE", 135],
                                ["S", 180], ["SW", 225], ["W", 270], ["NW", 315]] as const) {
        const p = project([Math.sin(az * DEG), Math.cos(az * DEG), 0]);
        if (p && p[0] > 10 && p[0] < w - 10 && p[1] > 0 && p[1] < h - 14) {
          ctx.fillText(name, p[0], p[1] + 14);
        }
      }
    }

    // Labels: everything once settled; mid-drag, only what must be named.
    const requests = settled.current
      ? [...labels, ...starLabels]
      : labels.filter((l) => !l.optional);
    for (const l of placeLabels(requests, settled.current ? bright : [], w, h)) {
      if (l.leader) {
        ctx.strokeStyle = "rgba(160, 170, 200, 0.45)";
        ctx.lineWidth = 0.8;
        ctx.beginPath();
        ctx.moveTo(l.leader[0], l.leader[1]);
        ctx.lineTo(l.leader[2], l.leader[3]);
        ctx.stroke();
      }
      const [style, colour] = l.className.includes(":") ? l.className.split(":") : ["", l.className];
      ctx.font = `${style === "bold" ? "600 " : style === "italic" ? "italic " : ""}${l.size}px system-ui, sans-serif`;
      const pick = l.tag !== undefined ? tagged[l.tag] : undefined;
      const lit = pick && (pickKey(pick) === hovered.current || pickKey(pick) === cardKey);
      ctx.fillStyle = lit ? "#ffffff" : colour;
      ctx.textAlign = l.anchor === "start" ? "left" : l.anchor === "end" ? "right" : "center";
      ctx.fillText(l.text, l.lx, l.ly);
      if (pick) {
        // The label's own box, measured now it is drawn, is clickable too.
        const tw = ctx.measureText(l.text).width;
        const x0 = l.anchor === "start" ? l.lx : l.anchor === "end" ? l.lx - tw : l.lx - tw / 2;
        newHits.push({ x: l.x, y: l.y, reach: 0, rank: pick.kind === "go" ? 1 : 3, pick,
                       box: { x0: x0 - 3, y0: l.ly - l.size - 2, x1: x0 + tw + 3, y1: l.ly + 4 } });
      }
    }

    // What is under the pointer, and the star whose card is open: a ring
    // round the mark, whether the mark or its label is what was pointed at.
    for (const [key, colour] of [[hovered.current, "rgba(255,255,255,0.75)"],
                                 [cardKey, "rgba(255,214,120,0.95)"]] as const) {
      if (!key) continue;
      const mark = newHits.find((hh) => !hh.box && pickKey(hh.pick) === key);
      if (!mark) continue;
      ctx.strokeStyle = colour;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(mark.x, mark.y, Math.max(8, mark.reach + 2), 0, 2 * Math.PI);
      ctx.stroke();
    }

    hits.current = newHits;
  };

  // Redraw whenever anything the drawing reads changes.
  useEffect(() => { schedule(); });

  // Keep the canvas sharp and square as its column is resized.
  useEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return;
    const observer = new ResizeObserver(() => schedule());
    observer.observe(wrap);
    return () => observer.disconnect();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // --- gliding to a target ---
  const glide = useRef(0);
  const stopGlide = () => { cancelAnimationFrame(glide.current); glide.current = 0; };
  const glideTo = (toEq: Vec, toFov: number) => {
    stopGlide();
    const fromEq = centerEq.current, fromFov = fov.current;
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const degrees = Math.acos(Math.max(-1, Math.min(1, dot(fromEq, toEq)))) / DEG;
    // Longer trips take a little longer, never so long it feels slow.
    const ms = reduced ? 0 : Math.min(700, 280 + degrees * 4);
    const start = performance.now();
    const frame = (now: number) => {
      const k = ms ? Math.min(1, (now - start) / ms) : 1;
      const e = easeInOut(k);
      centerEq.current = slerp(fromEq, toEq, e);
      fov.current = Math.exp(Math.log(fromFov) + (Math.log(toFov) - Math.log(fromFov)) * e);
      moving();
      if (k < 1) glide.current = requestAnimationFrame(frame);
      else { glide.current = 0; setFovShown(Math.round(toFov)); }
    };
    glide.current = requestAnimationFrame(frame);
  };
  useEffect(() => stopGlide, []);

  // --- interaction ---
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  // Where a drag started, and the sky point (J2000) that was grabbed there.
  const dragFrom = useRef<{ x: number; y: number; moved: boolean; grabbed: Vec } | null>(null);
  const pinch = useRef<number | null>(null);

  /** Shift the view by (dx, dy) pixels -- the arrow keys. */
  const panBy = (dx: number, dy: number) => {
    centerEq.current = norm(fromWork(panned(view(), dx, dy)));
  };
  /** Put the sky point `grabbedEq` (J2000) under pixel (px, py) -- dragging. */
  const dragTo = (grabbedEq: Vec, px: number, py: number) => {
    const { w, h } = size();
    const c = placeAt(toWork(centerEq.current), fov.current, toWork(grabbedEq), px, py, w, h);
    centerEq.current = norm(fromWork(c));
  };

  const zoomAt = (factor: number, px: number, py: number) => {
    const { w, h } = size();
    const next = zoomed(toWork(centerEq.current), fov.current, factor, px, py, w, h);
    centerEq.current = norm(fromWork(next.center));
    fov.current = next.fov;
    setFovShown(Math.round(fov.current));
    moving();
  };

  const local = (e: { clientX: number; clientY: number }) => {
    const rect = canvasRef.current!.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  };

  const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    stopGlide();                          // a hand on the chart takes over
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* best effort */ }
    const p = local(e);
    pointers.current.set(e.pointerId, p);
    if (pointers.current.size === 1)
      dragFrom.current = { ...p, moved: false, grabbed: fromWork(view().unproject(p.x, p.y)) };
    else { dragFrom.current = null; pinch.current = null; }
  };
  const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const p = local(e);
    const last = pointers.current.get(e.pointerId);
    if (!last) {
      // Hover: ring whatever a click would pick. The cursor stays as it is.
      const hit = hitAt(hits.current, p.x, p.y);
      const key = hit ? pickKey(hit.pick) : null;
      if (key !== hovered.current) { hovered.current = key; schedule(); }
      return;
    }
    pointers.current.set(e.pointerId, p);
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      if (pinch.current) zoomAt(pinch.current / d, (a.x + b.x) / 2, (a.y + b.y) / 2);
      pinch.current = d;
      return;
    }
    const from = dragFrom.current;
    if (!from) return;
    if (Math.hypot(p.x - from.x, p.y - from.y) > 3) from.moved = true;
    if (from.moved) {
      dragTo(from.grabbed, p.x, p.y);
      e.currentTarget.style.cursor = "grabbing";
      moving();
    }
  };
  const onPointerLeave = () => {
    if (hovered.current) { hovered.current = null; schedule(); }
  };
  const onPointerUp = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const from = dragFrom.current;
    pointers.current.delete(e.pointerId);
    if (pointers.current.size < 2) pinch.current = null;
    e.currentTarget.style.cursor = "grab";
    if (from && !from.moved) {
      // A click, not a drag: go to an object or planet, describe a star, or
      // on empty sky put any card away.
      const p = local(e);
      const hit = hitAt(hits.current, p.x, p.y);
      if (hit?.pick.kind === "go") { setCard(null); onSelect?.(hit.pick.subject); }
      else if (hit?.pick.kind === "star") setCard({ index: hit.pick.index, x: p.x, y: p.y });
      else setCard(null);
    }
    dragFrom.current = null;
  };

  // Wheel zoom needs a non-passive listener to stop the page scrolling.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      stopGlide();
      const rect = canvas.getBoundingClientRect();
      zoomAt(Math.exp(e.deltaY * 0.0015), e.clientX - rect.left, e.clientY - rect.top);
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", onWheel);
  });

  const onKeyDown = (e: React.KeyboardEvent<HTMLCanvasElement>) => {
    if (e.key === "Escape" && card) { setCard(null); return; }
    stopGlide();
    const v = view();
    const stepPx = v.w * 0.1;
    const moves: Record<string, () => void> = {
      ArrowLeft: () => panBy(stepPx, 0), ArrowRight: () => panBy(-stepPx, 0),
      ArrowUp: () => panBy(0, stepPx), ArrowDown: () => panBy(0, -stepPx),
      "+": () => zoomAt(1 / 1.25, v.w / 2, v.h / 2), "=": () => zoomAt(1 / 1.25, v.w / 2, v.h / 2),
      "-": () => zoomAt(1.25, v.w / 2, v.h / 2),
    };
    if (!moves[e.key]) return;
    e.preventDefault();
    moves[e.key]();
    moving();
  };

  /** Back to the view the chart opened with: the subject, 20 degrees wide. */
  const recenter = () => {
    if (subjectEq) glideTo(subjectEq, DEFAULT_FOV);
  };

  const step = (direction: 1 | -1) => {
    const next = new Date(new Date(at).getTime() + direction * STEP_MS);
    if (nightStart && next < new Date(nightStart)) return;
    if (nightEnd && next > new Date(nightEnd)) return;
    setAt(next.toISOString());
  };

  // Where the subject is, for the caption.
  const place = useMemo(() => {
    if (!subjectEq || !frame) return null;
    const hv = apply(frame.matrix, subjectEq);
    return { alt: Math.asin(hv[2]) / DEG, az: Math.atan2(hv[0], hv[1]) / DEG };
  }, [subjectEq, frame]);

  return (
    <div className="finder">
      <div className="finder-controls">
        <div className="segmented" role="group" aria-label="Orientation">
          <button className={orientation === "sky" ? "on" : ""}
                  onClick={() => setOrientation("sky")}
                  title="Zenith up, as you will see it from this site">
            As seen
          </button>
          <button className={orientation === "north" ? "on" : ""}
                  onClick={() => setOrientation("north")}
                  title="North up, east left, like an atlas">
            North up
          </button>
        </div>
        <button className="finder-recenter" onClick={recenter}
                title={`Back to ${subject.label}, ${DEFAULT_FOV}° wide`}>
          Recenter
        </button>
        <div className="finder-time">
          <button onClick={() => step(-1)} aria-label="Half an hour earlier">‹</button>
          <span>{formatTime(at, timeZone)}</span>
          <button onClick={() => step(1)} aria-label="Half an hour later">›</button>
        </div>
      </div>

      <div className="finder-controls finder-layers">
        {LAYERS.map((l) => (
          <button key={l.key} className={`finder-layer ${layers[l.key] ? "on" : ""}`}
                  aria-pressed={layers[l.key]}
                  onClick={() => setLayers({ ...layers, [l.key]: !layers[l.key] })}>
            {l.label}
          </button>
        ))}
      </div>

      {error && <p className="warning">{error}</p>}

      <div className="finder-figure" ref={wrapRef}>
        <canvas
          ref={canvasRef}
          className="finder-canvas"
          tabIndex={0}
          role="img"
          aria-label={`Sky chart centred on ${subject.label}. Drag to pan, scroll or pinch to zoom, arrow keys and plus or minus from the keyboard.`}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onPointerLeave={onPointerLeave}
          onKeyDown={onKeyDown}
        />
        {!catalog && !error && <p className="finder-loading muted small">Loading the sky…</p>}
        {card && (
          <StarCard index={card.index} x={card.x} y={card.y}
                    width={canvasRef.current?.clientWidth ?? 560}
                    height={canvasRef.current?.clientHeight ?? 560}
                    onClose={() => { setCard(null); canvasRef.current?.focus(); }} />
        )}
      </div>

      <p className="finder-caption muted small">
        {subject.label}
        {place && orientation === "sky" && (place.alt < 0
          ? <> · below the horizon at {formatTime(at, timeZone)}</>
          : <> · {Math.round(place.alt)}° up in the {compass(place.az)} at {formatTime(at, timeZone)}</>)}
        {orientation === "north" && <> · north up, east left</>}
        {" "}· {fovShown}° wide
      </p>
    </div>
  );
}

/** A showpiece's mark by type: ellipse galaxy, square nebula, dashed cluster. */
function drawObject(ctx: CanvasRenderingContext2D, group: string, x: number, y: number) {
  const s = 5;
  ctx.beginPath();
  ctx.setLineDash([]);
  if (group === "Galaxies") ctx.ellipse(x, y, s * 1.4, s * 0.7, 0, 0, 2 * Math.PI);
  else if (group === "Nebulae") ctx.rect(x - s, y - s, 2 * s, 2 * s);
  else if (group === "Double Stars") ctx.arc(x, y, s * 0.6, 0, 2 * Math.PI);
  else { ctx.setLineDash([2, 1.5]); ctx.arc(x, y, s, 0, 2 * Math.PI); }
  ctx.stroke();
  ctx.setLineDash([]);
}
