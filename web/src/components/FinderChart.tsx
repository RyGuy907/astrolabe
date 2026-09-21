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
 * Stereographic throughout: it keeps shapes true from a 2-degree finder
 * field to the whole sky, and at finder scales it is indistinguishable from
 * the gnomonic projection printed charts use.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, type SkyCatalog, type SkyFrame } from "../api";
import { formatTime } from "../format";
import { BODY_COLORS } from "./AltitudeChart";
import { placeLabels, type LabelRequest } from "./labels";
import {
  apply, applyT, DEG, limitFor, makeView, norm, panned, placeAt, unit, zoomed,
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
/** Quick zooms, re-centring on the subject. */
const PRESETS: { fov: number; label: string }[] = [
  { fov: 10, label: "10°" },
  { fov: 20, label: "20°" },
  { fov: 40, label: "40°" },
  { fov: 120, label: "Sky" },
];
const STEP_MS = 30 * 60_000;
const TELRAD_RINGS = [0.5, 2, 4];

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

/** Something on screen that a click can land on. */
interface Hit { x: number; y: number; subject: FinderSubject }

export function FinderChart({ subject, location, timeZone, nightStart, nightEnd,
                              onSelect }: Props) {
  const [catalog, setCatalog] = useState<SkyCatalog | null>(null);
  const [frame, setFrame] = useState<SkyFrame | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [orientation, setOrientation] = useState<"sky" | "north">("sky");
  const [at, setAt] = useState(subject.at);
  const [layers, setLayers] = useState<Record<Layer, boolean>>(() =>
    stored("astro:finder-layers", { constellations: true, names: true, objects: true }));
  const [density, setDensity] = useState<number>(() => stored("astro:finder-density", 0));
  //: Shown under the chart; the live value is in `fov`.
  const [fovShown, setFovShown] = useState(20);

  useEffect(() => store("astro:finder-layers", layers), [layers]);
  useEffect(() => store("astro:finder-density", density), [density]);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  // The view, in refs so a drag redraws without a React render per frame.
  const centerEq = useRef<Vec>([1, 0, 0]);        // J2000 unit vector
  const fov = useRef(20);                          // field width, degrees
  const settled = useRef(true);                    // not mid-drag: full labels
  const hits = useRef<Hit[]>([]);
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

  // A new subject re-centres on it; a click on the chart keeps the time,
  // a pick from the list starts at the subject's own best time.
  useEffect(() => setAt(subject.at), [subject.at]);
  const centred = useRef<string>("");
  useEffect(() => {
    if (!subjectEq || centred.current === subject.id) return;
    centred.current = subject.id;
    centerEq.current = subjectEq;
    schedule();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subjectEq, subject.id]);

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
    const limit = Math.max(2, Math.min(8, limitFor(f) + density));
    const newHits: Hit[] = [];

    ctx.fillStyle = "#05070d";
    ctx.fillRect(0, 0, w, h);

    // Ground below the horizon, as seen.
    if (orientation === "sky") {
      const ring: [number, number][] = [];
      for (let az = 0; az <= 360; az += 2) {
        const p = project([Math.sin(az * DEG), Math.cos(az * DEG), 0]);
        if (p) ring.push([p[0], p[1]]);
      }
      if (ring.length > 2) {
        ctx.beginPath();
        ring.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
        const nadir = project([0, 0, -1]);
        const inside = nadir && ctx.isPointInPath(nadir[0] * dpr, nadir[1] * dpr);
        if (!inside) ctx.rect(w * 3, -h * 2, -w * 5, h * 5);
        ctx.fillStyle = "rgba(60, 45, 30, 0.55)";
        ctx.fill("evenodd");
        ctx.strokeStyle = "rgba(200, 170, 130, 0.7)";
        ctx.lineWidth = 1.4;
        ctx.beginPath();
        ring.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
        ctx.stroke();
      }
    }

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
    const nameLimit = limit - 2.5;
    for (let i = 0; i < sky.n; i++) {
      const m = sky.mag[i];
      if (m > limit) break;
      const p = project([working.wx[i], working.wy[i], working.wz[i]]);
      if (!p || p[0] < -8 || p[1] < -8 || p[0] > w + 8 || p[1] > h + 8) continue;
      const r = Math.max(0.6, Math.min(7, 0.9 + (limit - m) * 0.9));
      ctx.beginPath();
      ctx.arc(p[0], p[1], r, 0, 2 * Math.PI);
      ctx.fill();
      if (r >= 2.4) bright.push({ x: p[0], y: p[1], r: r + 1 });
      const label = sky.labels[String(i)];
      if (label && layers.names && m <= nameLimit) {
        starLabels.push({ text: label, x: p[0], y: p[1], r, size: 10,
                          priority: 10 + m, className: "#8a94ad", optional: true });
      }
    }

    const labels: LabelRequest[] = [];

    // Showpieces, from a 70-degree field inward; named from 45.
    if (layers.objects && f <= 70) {
      ctx.lineWidth = 1.2;
      ctx.strokeStyle = "#7fd6a0";
      for (const o of sky.objects) {
        if (subject.kind === "target" && o.id === subject.id) continue;
        const p = project(toWork(o.v));
        if (!p || p[0] < 0 || p[1] < 0 || p[0] > w || p[1] > h) continue;
        drawObject(ctx, o.group, p[0], p[1]);
        newHits.push({ x: p[0], y: p[1], subject: {
          kind: "target", id: o.id, label: o.name, at, ra: o.ra, dec: o.dec } });
        if (f <= 45) {
          labels.push({ text: o.label, x: p[0], y: p[1], r: 6, size: 10, priority: 3,
                        className: "#9adfb4", optional: true });
        }
      }
    }

    // The Moon and planets, always -- they are the brightest things there.
    if (layers.objects) {
      for (const b of frame.bodies) {
        if (subject.kind === "body" && b.name === subject.id) continue;
        const p = project(toWork(unit(b.ra, b.dec)));
        if (!p || p[0] < 0 || p[1] < 0 || p[0] > w || p[1] > h) continue;
        ctx.fillStyle = BODY_COLORS[b.name] ?? "#fff";
        ctx.beginPath();
        ctx.arc(p[0], p[1], b.name === "moon" ? 7 : 4.5, 0, 2 * Math.PI);
        ctx.fill();
        const name = b.name[0].toUpperCase() + b.name.slice(1);
        newHits.push({ x: p[0], y: p[1], subject: { kind: "body", id: b.name, label: name, at } });
        labels.push({ text: name, x: p[0], y: p[1], r: 6, size: 10, priority: 1,
                      className: "#d8dcff", optional: false });
      }
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

    // The subject: crosshair, and Telrad rings where they are big enough to
    // mean something.
    if (subjectEq) {
      const p = project(toWork(subjectEq));
      if (p) {
        const k = (2 / (1 + p[2])) * v.s * DEG;       // pixels per degree there
        if (f <= 60) {
          ctx.strokeStyle = "rgba(255, 90, 80, 0.55)";
          ctx.lineWidth = 1.2;
          for (const ring of TELRAD_RINGS) {
            if (ring * k < 4) continue;
            ctx.beginPath();
            ctx.arc(p[0], p[1], ring * k, 0, 2 * Math.PI);
            ctx.stroke();
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
      ctx.fillStyle = colour;
      ctx.textAlign = "center";
      ctx.fillText(l.text, l.lx, l.ly);
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
      // Hover: a hand over anything clickable.
      const near = hits.current.some((h) => Math.hypot(h.x - p.x, h.y - p.y) < 12);
      e.currentTarget.style.cursor = near ? "pointer" : "grab";
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
  const onPointerUp = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const from = dragFrom.current;
    pointers.current.delete(e.pointerId);
    if (pointers.current.size < 2) pinch.current = null;
    e.currentTarget.style.cursor = "grab";
    if (from && !from.moved && onSelect) {
      // A click, not a drag: the nearest object or planet under it.
      const p = local(e);
      let best: Hit | null = null, bestD = 12;
      for (const h of hits.current) {
        const d = Math.hypot(h.x - p.x, h.y - p.y);
        if (d < bestD) { best = h; bestD = d; }
      }
      if (best) onSelect(best.subject);
    }
    dragFrom.current = null;
  };

  // Wheel zoom needs a non-passive listener to stop the page scrolling.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = canvas.getBoundingClientRect();
      zoomAt(Math.exp(e.deltaY * 0.0015), e.clientX - rect.left, e.clientY - rect.top);
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", onWheel);
  });

  const onKeyDown = (e: React.KeyboardEvent<HTMLCanvasElement>) => {
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

  const recentre = (width?: number) => {
    if (subjectEq) centerEq.current = subjectEq;
    if (width) { fov.current = width; setFovShown(width); }
    moving();
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
        <div className="segmented" role="group" aria-label="Zoom to">
          {PRESETS.map((p) => (
            <button key={p.fov} className={fovShown === p.fov ? "on" : ""}
                    onClick={() => recentre(p.fov)}
                    title={`Centre on ${subject.label} at ${p.fov}° wide`}>
              {p.label}
            </button>
          ))}
        </div>
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
        <label className="finder-density" title="Fewer or more faint stars than the zoom suggests">
          <span>Fewer</span>
          <input type="range" min={-2} max={1.5} step={0.1} value={density}
                 onChange={(e) => setDensity(Number(e.target.value))}
                 aria-label="Star density" />
          <span>More</span>
        </label>
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
          onKeyDown={onKeyDown}
        />
        {!catalog && !error && <p className="finder-loading muted small">Loading the sky…</p>}
      </div>

      <p className="finder-caption muted small">
        {subject.label}
        {place && orientation === "sky" && (place.alt < 0
          ? <> · below the horizon at {formatTime(at, timeZone)}</>
          : <> · {Math.round(place.alt)}° up in the {compass(place.az)} at {formatTime(at, timeZone)}</>)}
        {orientation === "north" && <> · north up, east left</>}
        {" "}· {fovShown}° wide · drag to pan, scroll to zoom, click an object to go to it
        {" "}<button className="link-button" onClick={() => recentre(20)}>re-centre</button>
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
