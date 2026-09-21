/**
 * A finder chart: the patch of sky around a target, for star hopping.
 *
 * Opens as a tab over the altitude chart. The engine lays everything out --
 * `/api/finder` returns chart units, degrees on the tangent plane with the
 * target at the origin -- so this component only scales and draws.
 *
 * Defaults to the sky *as seen from the site* at the target's best time:
 * zenith up, so the chart can be held against the sky without turning it.
 * North-up is one click away for anyone working from an atlas.
 *
 * The Telrad rings -- 0.5, 2 and 4 degrees -- are the standard star-hopping
 * reticle, and what printed finder charts overlay for the same reason: point
 * the rings where the chart says and the target is in the eyepiece.
 */

import { useEffect, useMemo, useState } from "react";
import { api, ApiError, type ChartPointModel, type FinderChartModel } from "../api";
import { formatTime } from "../format";
import { BODY_COLORS } from "./AltitudeChart";

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
];

/** On-screen size the SVG is drawn for; everything below scales from it. */
const VIEW_PX = 560;
const STEP_MS = 30 * 60_000;

const TELRAD_RINGS = [0.5, 2, 4];

function compass(az: number): string {
  const names = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                 "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
  return names[Math.round((((az % 360) + 360) % 360) / 22.5) % 16];
}

export function FinderChart({ subject, location, timeZone, nightStart, nightEnd }: Props) {
  const [orientation, setOrientation] = useState<"sky" | "north">("sky");
  const [radius, setRadius] = useState(10);
  const [at, setAt] = useState(subject.at);
  const [chart, setChart] = useState<FinderChartModel | null>(null);
  const [error, setError] = useState<string | null>(null);

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
  const k = VIEW_PX / 2 / R;                       // pixels per degree
  const X = (x: number) => VIEW_PX / 2 + x * k;
  const Y = (y: number) => VIEW_PX / 2 - y * k;    // chart +y is up

  const stars = useMemo(() => {
    if (!chart) return [];
    const faint = chart.limiting_mag;
    return chart.stars.map((s) => ({
      ...s,
      // Each magnitude brighter reads as a clearly larger dot, without the
      // brightest swamping the field.
      r: Math.max(0.7, Math.min(7, 0.9 + (faint - (s.mag ?? faint)) * 0.95)),
    }));
  }, [chart]);

  const below = chart && chart.center_alt_deg < 0;
  const poly = (pts: [number, number][]) =>
    pts.map(([x, y]) => `${X(x).toFixed(1)},${Y(y).toFixed(1)}`).join(" ");

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

            {chart?.lines.map((run, i) => (
              <polyline key={i} className="finder-line" strokeWidth={1.1}
                        points={poly(run)} />
            ))}

            {stars.map((s, i) => (
              <circle key={i} cx={X(s.x)} cy={Y(s.y)} r={s.r} className="finder-star" />
            ))}
            {stars.filter((s) => s.label && (s.mag ?? 99) <= 4.6).map((s, i) => (
              <text key={i} x={X(s.x) + s.r + 3} y={Y(s.y) + 3.5}
                    className="finder-star-label" fontSize={10.5}>
                {s.label}
              </text>
            ))}

            {chart?.objects.map((o, i) => (
              <ObjectMark key={i} point={o} cx={X(o.x)} cy={Y(o.y)} />
            ))}

            {/* Telrad rings on the target. */}
            {TELRAD_RINGS.filter((r) => r < R).map((r) => (
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
          {" "}· stars to magnitude {chart.limiting_mag} · Telrad rings 0.5°, 2°, 4°
        </p>
      )}
    </div>
  );
}

/** A neighbour on the chart: a deep-sky showpiece by type, or a body. */
function ObjectMark({ point, cx, cy }: { point: ChartPointModel; cx: number; cy: number }) {
  const body = BODY_COLORS[point.kind];
  const label = (
    <text x={cx + 8} y={cy + 3.5} className="finder-object-label" fontSize={10}>
      {point.label}
    </text>
  );
  if (body) {
    return (
      <g>
        <circle cx={cx} cy={cy} r={4.5} fill={body} />
        {label}
      </g>
    );
  }
  const s = 5;
  const shape = point.kind === "Galaxies"
    ? <ellipse cx={cx} cy={cy} rx={s * 1.4} ry={s * 0.7} />
    : point.kind === "Nebulae"
      ? <rect x={cx - s} y={cy - s} width={2 * s} height={2 * s} />
      : point.kind === "Double Stars"
        ? <circle cx={cx} cy={cy} r={s * 0.6} />
        : <circle cx={cx} cy={cy} r={s} strokeDasharray="1.6 1.2" />;
  return (
    <g className="finder-object" strokeWidth={1.2}>
      {shape}
      {label}
    </g>
  );
}
