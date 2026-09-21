/**
 * A finder chart: the patch of sky around a target, for star hopping.
 *
 * Opens as a tab over the altitude chart. The engine lays everything out --
 * `/api/finder` returns chart units, degrees on the tangent plane with the
 * target at the origin -- so this component scales, filters and draws.
 *
 * Defaults to the sky *as seen from the site* at the target's best time:
 * zenith up, so the chart can be held against the sky without turning it.
 * North-up is one click away for anyone working from an atlas.
 *
 * Four fields: 10, 20 and 40 degrees for hopping, and "Sky", a 90-degree
 * overview of where the target sits among the major constellations. Layers
 * -- constellations, star names, other objects -- can be switched off, and a
 * density slider shows fewer or more faint stars without a new request: the
 * chart arrives 1.5 magnitudes deeper than its default.
 *
 * The Telrad rings -- 0.5, 2 and 4 degrees -- are the standard star-hopping
 * reticle, and what printed finder charts overlay for the same reason: point
 * the rings where the chart says and the target is in the eyepiece.
 */

import { useEffect, useMemo, useState } from "react";
import { api, ApiError, type FinderChartModel } from "../api";
import { formatTime } from "../format";
import { BODY_COLORS } from "./AltitudeChart";
import { placeLabels, type LabelRequest } from "./labels";

/** What the chart is of: a catalogue object or a solar-system body. */
export interface FinderSubject {
  kind: "target" | "body";
  /** Catalogue id (NGC6205) or body name (saturn). */
  id: string;
  /** Shown on the tab. */
  label: string;
  /** When to draw the sky: the target's best time, as ISO UTC. */
  at: string;
}

interface Props {
  subject: FinderSubject;
  location: string;
  timeZone: string;
  /** The night's bounds, so the time stepper stays inside it. */
  nightStart: string | null;
  nightEnd: string | null;
}

const FIELDS: { radius: number; label: string; hint: string }[] = [
  { radius: 5, label: "10°", hint: "Finder scope" },
  { radius: 10, label: "20°", hint: "Binoculars and the Telrad" },
  { radius: 20, label: "40°", hint: "The region, by eye" },
  { radius: 45, label: "Sky", hint: "Where it is among the major constellations" },
];

/** The layers that can be switched off. */
type Layer = "constellations" | "names" | "objects";
const LAYERS: { key: Layer; label: string }[] = [
  { key: "constellations", label: "Constellations" },
  { key: "names", label: "Star names" },
  { key: "objects", label: "Other objects" },
];

/** On-screen size the SVG is drawn for; everything scales from it. */
const VIEW_PX = 560;
const STEP_MS = 30 * 60_000;
const TELRAD_RINGS = [0.5, 2, 4];
/** The density slider's reach: fewer stars down to this, more up to depth. */
const FEWER_MAG = 2.0;
/** Stars at least this bright get a name when they have one: every named
 *  star a hop might use on a hopping chart, only the major ones on the
 *  whole-sky overview, which is about the shape of the sky. */
const NAMED_BRIGHTER_THAN = 4.6;
const NAMED_BRIGHTER_THAN_OVERVIEW = 2.5;

function compass(az: number): string {
  const names = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                 "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
  return names[Math.round((((az % 360) + 360) % 360) / 22.5) % 16];
}

/** A per-browser preference: read once, guarded, since storage can throw. */
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

export function FinderChart({ subject, location, timeZone, nightStart, nightEnd }: Props) {
  const [orientation, setOrientation] = useState<"sky" | "north">("sky");
  const [radius, setRadius] = useState(10);
  const [at, setAt] = useState(subject.at);
  const [chart, setChart] = useState<FinderChartModel | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [layers, setLayers] = useState<Record<Layer, boolean>>(() =>
    stored("astro:finder-layers",
           { constellations: true, names: true, objects: true }));
  //: The density slider, as magnitudes relative to the field's default, so
  //: a setting carries sensibly from one field to another.
  const [density, setDensity] = useState<number>(() => stored("astro:finder-density", 0));

  useEffect(() => store("astro:finder-layers", layers), [layers]);
  useEffect(() => store("astro:finder-density", density), [density]);

  // A new subject starts at its own best time.
  useEffect(() => setAt(subject.at), [subject.id, subject.at]);

  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    api.finder(location,
               subject.kind === "body" ? { body: subject.id } : { target: subject.id },
               at, radius, orientation, controller.signal)
      .then(setChart)
      .catch((e) => {
        if ((e as Error)?.name === "AbortError") return;
        setError(e instanceof ApiError ? e.message : String(e));
      });
    return () => controller.abort();
  }, [subject.kind, subject.id, location, at, radius, orientation]);

  const step = (direction: 1 | -1) => {
    const next = new Date(new Date(at).getTime() + direction * STEP_MS);
    if (nightStart && next < new Date(nightStart)) return;
    if (nightEnd && next > new Date(nightEnd)) return;
    setAt(next.toISOString());
  };

  // Drawn in pixel units on a VIEW_PX square, with chart degrees converted
  // on the way in. Drawing in degrees directly set label fonts at ~0.4 of a
  // unit, and Chrome renders text that small -- then scaled up 28 times --
  // with broken hinting: glyphs doubled and overlapping.
  const R = chart?.radius_deg ?? radius;
  const k = VIEW_PX / 2 / R;
  const X = (x: number) => VIEW_PX / 2 + x * k;
  const Y = (y: number) => VIEW_PX / 2 - y * k;

  // The faintest star shown: the field's default moved by the slider, never
  // past what the chart carries.
  const limit = chart
    ? Math.min(chart.max_mag, Math.max(chart.limiting_mag - FEWER_MAG,
                                       chart.limiting_mag + density))
    : 0;

  const stars = useMemo(() => {
    if (!chart) return [];
    return chart.stars
      .filter((s) => (s.mag ?? 99) <= limit)
      .map((s) => ({
        ...s,
        px: X(s.x),
        py: Y(s.y),
        // Each magnitude brighter reads as a clearly larger dot, without the
        // brightest swamping the field. Sized against the shown limit, so
        // thinning the field does not shrink what is left.
        r: Math.max(0.7, Math.min(7, 0.9 + (limit - (s.mag ?? limit)) * 0.95)),
      }));
  // X and Y are pure functions of k.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chart, limit, k]);

  const labels = useMemo(() => {
    if (!chart) return [];
    const requests: LabelRequest[] = [{
      text: chart.target.replace(/\s*\(.*\)$/, ""),
      x: VIEW_PX / 2, y: VIEW_PX / 2, r: 10, size: 11.5, priority: 0,
      className: "finder-target-label", optional: false,
    }];
    if (layers.objects) {
      for (const o of chart.objects) {
        const body = o.kind in BODY_COLORS;
        // The overview is major constellations and bright stars; deep-sky
        // neighbours there are clutter. Planets stay: they are bright.
        if (chart.overview && !body) continue;
        requests.push({
          text: o.label.replace(/\s*\(.*\)$/, ""), x: X(o.x), y: Y(o.y), r: 6,
          size: 10, priority: body ? 1 : 3, className: "finder-object-label",
          optional: !body,
        });
      }
    }
    if (layers.constellations) {
      for (const c of chart.constellations) {
        requests.push({
          text: c.label, x: X(c.x), y: Y(c.y), r: 0, size: 11, priority: 2,
          className: "finder-constellation-label", optional: true,
        });
      }
    }
    if (layers.names) {
      for (const s of stars) {
        if (!s.label || (s.mag ?? 99) >
            (chart.overview ? NAMED_BRIGHTER_THAN_OVERVIEW : NAMED_BRIGHTER_THAN)) continue;
        requests.push({
          text: s.label, x: s.px, y: s.py, r: s.r, size: 10,
          priority: 10 + (s.mag ?? 10), className: "finder-star-label",
          optional: true,
        });
      }
    }
    // Labels keep clear of bright stars as well as of each other.
    const obstacles = stars.filter((s) => s.r >= 2.4)
      .map((s) => ({ x: s.px, y: s.py, r: s.r + 1 }));
    return placeLabels(requests, obstacles, VIEW_PX, VIEW_PX);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chart, stars, layers, k]);

  const below = chart && chart.center_alt_deg < 0;
  const poly = (pts: [number, number][]) =>
    pts.map(([x, y]) => `${X(x).toFixed(1)},${Y(y).toFixed(1)}`).join(" ");
  const shown = stars.length;

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
        <div className="segmented" role="group" aria-label="Field of view">
          {FIELDS.map((f) => (
            <button key={f.radius} className={radius === f.radius ? "on" : ""}
                    onClick={() => setRadius(f.radius)} title={f.hint}>
              {f.label}
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
        <label className="finder-density" title="How faint a star to show">
          <span>Fewer</span>
          <input type="range" min={-FEWER_MAG}
                 max={chart ? chart.max_mag - chart.limiting_mag : 1.5}
                 step={0.1} value={density}
                 onChange={(e) => setDensity(Number(e.target.value))}
                 aria-label="Star density" />
          <span>More</span>
        </label>
      </div>

      {error && <p className="warning">{error}</p>}

      <figure className="finder-figure">
        <svg viewBox={`0 0 ${VIEW_PX} ${VIEW_PX}`} role="img"
             aria-label={`Finder chart for ${subject.label}`}>
          <defs>
            <clipPath id="finder-clip">
              <rect x={0} y={0} width={VIEW_PX} height={VIEW_PX} rx={10} />
            </clipPath>
          </defs>
          <g clipPath="url(#finder-clip)">
            <rect x={0} y={0} width={VIEW_PX} height={VIEW_PX} className="finder-sky" />

            {/* The ground, below the horizon, so a target near it reads as
                low rather than as merely off-centre. */}
            {chart && chart.orientation === "sky" && chart.horizon.length > 1 && (
              <polygon
                className="finder-ground"
                points={`${poly(chart.horizon)} ${
                  X(chart.horizon[chart.horizon.length - 1][0]).toFixed(1)},${VIEW_PX * 3} ${
                  X(chart.horizon[0][0]).toFixed(1)},${VIEW_PX * 3}`}
              />
            )}

            {layers.constellations && chart?.lines.map((run, i) => (
              <polyline key={i} className="finder-line" strokeWidth={1.1}
                        points={poly(run)} />
            ))}

            {stars.map((s, i) => (
              <circle key={i} cx={s.px} cy={s.py} r={s.r} className="finder-star" />
            ))}

            {layers.objects && chart?.objects
              .filter((o) => !chart.overview || o.kind in BODY_COLORS)
              .map((o, i) => (
                <ObjectMark key={i} kind={o.kind} cx={X(o.x)} cy={Y(o.y)} />
              ))}

            {/* Telrad rings on the target: a hopping aid, so not in the
                whole-sky overview, where they would be specks. */}
            {!chart?.overview && TELRAD_RINGS.filter((r) => r < R).map((r) => (
              <circle key={r} cx={VIEW_PX / 2} cy={VIEW_PX / 2} r={r * k}
                      className="finder-telrad" strokeWidth={1.2} />
            ))}
            <g className="finder-target" strokeWidth={1.6}
               transform={`translate(${VIEW_PX / 2} ${VIEW_PX / 2})`}>
              <line x1={-9} x2={-3} y1={0} y2={0} />
              <line x1={3} x2={9} y1={0} y2={0} />
              <line y1={-9} y2={-3} x1={0} x2={0} />
              <line y1={3} y2={9} x1={0} x2={0} />
            </g>

            {chart?.orientation === "sky" && chart.horizon.length > 1 && (
              <polyline className="finder-horizon" strokeWidth={1.4}
                        points={poly(chart.horizon)} />
            )}
            {chart?.directions.map((d) => (
              <text key={d.label} x={X(d.x)} y={Y(d.y) + 13} textAnchor="middle"
                    className="finder-direction" fontSize={11}>
                {d.label}
              </text>
            ))}
            {chart?.orientation === "north" && (
              <>
                <text x={VIEW_PX / 2} y={16} textAnchor="middle"
                      className="finder-direction" fontSize={11}>N</text>
                <text x={12} y={VIEW_PX / 2 + 4} textAnchor="middle"
                      className="finder-direction" fontSize={11}>E</text>
              </>
            )}

            {labels.map((l, i) => (
              <g key={i}>
                {l.leader && (
                  <line x1={l.leader[0]} y1={l.leader[1]} x2={l.leader[2]}
                        y2={l.leader[3]} className="finder-leader" />
                )}
                <text x={l.lx} y={l.ly} textAnchor={l.anchor}
                      className={l.className} fontSize={l.size}>
                  {l.text}
                </text>
              </g>
            ))}
          </g>
        </svg>
      </figure>

      {chart && (
        <p className="finder-caption muted small">
          {chart.target} · {orientation === "sky"
            ? (below
                ? <>below the horizon at {formatTime(chart.at, timeZone)}</>
                : <>{Math.round(chart.center_alt_deg)}° up in the {compass(chart.center_az_deg)} at {formatTime(chart.at, timeZone)}</>)
            : <>north up, east left</>}
          {" "}· {shown} stars to magnitude {limit.toFixed(1)}
          {!chart.overview && <> · Telrad rings 0.5°, 2°, 4°</>}
        </p>
      )}
    </div>
  );
}

/** A neighbour's mark: a deep-sky showpiece by type, or a body. */
function ObjectMark({ kind, cx, cy }: { kind: string; cx: number; cy: number }) {
  const body = BODY_COLORS[kind];
  if (body) return <circle cx={cx} cy={cy} r={4.5} fill={body} />;
  const s = 5;
  const shape = kind === "Galaxies"
    ? <ellipse cx={cx} cy={cy} rx={s * 1.4} ry={s * 0.7} />
    : kind === "Nebulae"
      ? <rect x={cx - s} y={cy - s} width={2 * s} height={2 * s} />
      : kind === "Double Stars"
        ? <circle cx={cx} cy={cy} r={s * 0.6} />
        : <circle cx={cx} cy={cy} r={s} strokeDasharray="1.6 1.2" />;
  return <g className="finder-object" strokeWidth={1.2}>{shape}</g>;
}
