/**
 * Altitude vs time — PLAN.md §4 calls this "the single highest-value visual".
 *
 * Hand-rolled SVG rather than a charting library, because the thing that makes
 * this chart useful is the astronomy-specific furniture a generic library
 * fights you on: twilight bands shading from dusk to true dark, the altitude
 * floor, the obstruction horizon drawn as a filled region under the curves,
 * and a crosshair that reads every series at once.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import type { AltitudeResponse, ObservingWindow } from "../api";
import { formatTime, zoneOffsetMinutes } from "../format";

/** Drawing units. The SVG scales to its column, so these set the shape and
 *  how big text and lines come out relative to it. At 1000 wide the chart
 *  rendered about half size in its ~590px column -- 11-unit axis labels at
 *  6px -- and at 1000 x 420 it was a letterbox with the column's spare height
 *  sitting empty under it. 700 x 460 draws text near its nominal size and
 *  gives altitude room to read. */
export const WIDTH = 700;
export const HEIGHT = 460;
const MARGIN = { top: 16, right: 18, bottom: 40, left: 48 };

/** Below this width -- a phone -- the chart stops being a 700-wide drawing
 *  scaled down, whose labels came out at 5px, and is drawn at the column's
 *  own width instead: one unit to a pixel, so text is its real size, taller
 *  than it is wide so a night of curves still has room. */
const COMPACT_BELOW = 600;
const COMPACT_MARGIN = { top: 14, right: 10, bottom: 34, left: 36 };

/** An hour label needs about this many units to itself, or they collide. */
const MIN_LABEL_SPACING = 50;

interface Layout {
  width: number;
  height: number;
  margin: typeof MARGIN;
  plotWidth: number;
  plotHeight: number;
  compact: boolean;
}

function layoutFor(columnWidth: number): Layout {
  const compact = columnWidth > 0 && columnWidth < COMPACT_BELOW;
  const width = compact ? Math.round(columnWidth) : WIDTH;
  const height = compact ? Math.round(Math.min(440, Math.max(320, columnWidth * 1.05))) : HEIGHT;
  const margin = compact ? COMPACT_MARGIN : MARGIN;
  return {
    width, height, margin, compact,
    plotWidth: width - margin.left - margin.right,
    plotHeight: height - margin.top - margin.bottom,
  };
}

/** The figure's width in CSS pixels, kept current as the layout changes. */
function useWidth(ref: React.RefObject<HTMLElement>): number {
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    setWidth(node.clientWidth);
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(node);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}

/** Colours for the bodies people already picture in a colour.
 *
 * Fixed per body rather than handed out in list order. In order, the same
 * planet was blue one night and purple the next depending on what else was
 * charted, and nothing about the colour said which curve was which. These
 * follow the common picture of each: Mars red, Jupiter tan, Saturn gold,
 * Uranus pale cyan and Neptune a deeper blue, Venus cream, Mercury grey, the
 * Moon near-white. Night vision still maps the whole chart to red while
 * keeping relative lightness, so they stay distinguishable there too.
 */
//: Picked by search within each body's familiar colour family, maximising
//: the smallest CIELAB distance between any two. The first pass was right in
//: hue and too close in practice: the Moon and Mercury were both pale greys
//: (dE 27) and Venus, Jupiter and Saturn three pale yellow-tans (dE 22-28).
//: Now the Moon is white and Mercury a mid slate grey, Venus a light yellow,
//: Saturn a deeper amber and Jupiter beige -- the cream-and-tan it is usually
//: pictured as. Jupiter was a brown-orange first, which still read as a
//: darker Saturn (dE 29); beige puts them at 42 and keeps every other pair
//: at 28 or more.
export const BODY_COLORS: Record<string, string> = {
  moon: "#f7f7f9",
  sun: "#ffd60a",
  mercury: "#7f8791",
  venus: "#f5e27a",
  mars: "#e5533c",
  jupiter: "#c9b28c",
  saturn: "#d99a2b",
  uranus: "#8fdcea",
  neptune: "#4a78ff",
};

/** For everything else -- constellations and deep-sky objects -- in order.
 *
 * Chosen to stay clear of the planets' hues: no reds, tans, golds or blues
 * that would make a constellation read as a planet. Greens, violets and
 * pinks. */
const SERIES_COLORS = [
  "#30d158", "#bf5af2", "#ff6fae", "#63e6be",
  "#9a7bff", "#b5e853", "#ff9ecf", "#2fb8a0",
];

/**
 * One colour per series, by body where it has one and otherwise by its place
 * among the series that do not. Indexed against the full list, so hiding a
 * curve never recolours the others.
 */
function seriesColors(series: { label: string }[]): string[] {
  let other = 0;
  return series.map((s) =>
    BODY_COLORS[s.label.toLowerCase()] ??
      SERIES_COLORS[other++ % SERIES_COLORS.length]);
}

/** Charted by default but drawn thin and faded. Uranus and Neptune are
 *  telescope objects -- worth knowing where they are, not worth two full-
 *  weight curves competing with the planets you can see by eye. */
const FAINT_BODIES = new Set(["uranus", "neptune"]);

/** How near the pointer must be to a curve, in screen pixels, to label it. */
const HOVER_RADIUS_PX = 14;

/** Legend groupings, in display order. Anything unmatched falls into Other. */
const LEGEND_SECTIONS = [
  { label: "Solar system", kinds: ["moon", "sun", "planet"] },
  { label: "Constellations", kinds: ["constellation"] },
  { label: "Other", kinds: ["deep-sky"] },
];

interface Props {
  data: AltitudeResponse;
  timeZone: string;
  /** Labels currently hidden. They keep their legend chip and their colour. */
  hidden?: string[];
  /** Show/hide a curve without dropping it from the legend. */
  onToggle?: (label: string) => void;
  /** Drop a curve from the chart entirely. */
  onRemove?: (label: string) => void;
  /** Show or hide a group of curves at once, for a legend section's toggle. */
  onSetVisible?: (labels: string[], visible: boolean) => void;
  /** The observing session, drawn as the boundary of the planned night. */
  session?: ObservingWindow | null;
  /** The site's obstruction angle, which is the altitude floor now. Its peak
   *  for a measured profile, since one line cannot draw a varying horizon. */
  obstructionDeg?: number;
  /** True when that angle is the peak of a profile rather than a flat ring. */
  obstructionVaries?: boolean;
}

export function AltitudeChart({
  data, timeZone, hidden = [], onToggle, onRemove, onSetVisible, session = null,
  obstructionDeg = 0, obstructionVaries = false,
}: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const figureRef = useRef<HTMLElement>(null);
  const layout = layoutFor(useWidth(figureRef));
  const { width: W, height: H, margin: M, plotWidth: PLOT_WIDTH, plotHeight: PLOT_HEIGHT } = layout;
  const [hoverX, setHoverX] = useState<number | null>(null);
  //: Pointer position: `y` in plot units, for finding the nearest curve, and
  //: `left`/`top` in CSS pixels within the figure, for placing the label.
  const [pointer, setPointer] =
    useState<{ y: number; left: number; top: number; unitsPerPx: number;
               width: number } | null>(null);

  const startMs = new Date(data.start).getTime();
  const endMs = new Date(data.end).getTime();

  const xOf = (iso: string) =>
    ((new Date(iso).getTime() - startMs) / (endMs - startMs)) * PLOT_WIDTH;
  const xOfMs = (ms: number) => ((ms - startMs) / (endMs - startMs)) * PLOT_WIDTH;
  // Altitude runs -10 to 90: a little below the horizon so setting objects
  // visibly leave the plot rather than being clipped at the axis.
  const yOf = (altitude: number) =>
    PLOT_HEIGHT - ((altitude + 10) / 100) * PLOT_HEIGHT;

  const colors = useMemo(() => seriesColors(data.series), [data]);

  const paths = useMemo(
    () =>
      data.series.map((series, index) => {
        const points = series.points
          .map((p) => `${xOf(p.time).toFixed(1)},${yOf(p.altitude_deg).toFixed(1)}`)
          .join(" L ");
        return {
          label: series.label,
          kind: series.kind,
          color: colors[index],
          d: `M ${points}`,
        };
      }),
    // The drawing size changes the path, so it is a dependency too.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [data, PLOT_WIDTH, PLOT_HEIGHT],
  );

  // Hour ticks on the site's clock, not the browser's -- and on its hours:
  // rounding to the UTC hour put every tick on :30 or :45 in zones like
  // India's (+5:30) or Nepal's (+5:45).
  const hourTicks = useMemo(() => {
    const ticks: { ms: number; label: string }[] = [];
    const offset = zoneOffsetMinutes(startMs, timeZone) * 60_000;
    const firstLocal = Math.ceil((startMs + offset) / 3600_000) * 3600_000;
    for (let ms = firstLocal - offset; ms <= endMs; ms += 3600_000) {
      if (ms < startMs) continue;
      ticks.push({
        ms,
        label: formatTime(new Date(ms).toISOString(), timeZone),
      });
    }
    // Every other hour on a wide chart; on a narrow one, as many hours apart
    // as it takes for the labels not to run into each other.
    const unitsPerHour = PLOT_WIDTH / ((endMs - startMs) / 3600_000);
    const every = Math.max(2, Math.ceil(MIN_LABEL_SPACING / unitsPerHour));
    return ticks.filter((_, i) => i % every === 0);
  }, [startMs, endMs, timeZone, PLOT_WIDTH]);

  const hoverTime = hoverX === null
    ? null
    : new Date(startMs + (hoverX / PLOT_WIDTH) * (endMs - startMs));

  const readouts = useMemo(() => {
    if (!hoverTime) return [];
    return data.series
      .map((series, index) => ({ series, index }))
      .filter(({ series }) => !hidden.includes(series.label))
      .map(({ series, index }) => {
      let nearest = series.points[0];
      let best = Infinity;
      for (const point of series.points) {
        const distance = Math.abs(new Date(point.time).getTime() - hoverTime.getTime());
        if (distance < best) {
          best = distance;
          nearest = point;
        }
      }
      return {
        label: series.label,
        color: colors[index],
        altitude: nearest.altitude_deg,
        azimuth: nearest.azimuth_deg,
      };
    });
  }, [hoverTime, data, hidden]);

  // The curve under the pointer, for the hover label: whichever visible
  // series is vertically closest at the pointer's time, if it is within
  // HOVER_RADIUS_PX on screen. Measured in screen pixels, so "close" means
  // the same thing however wide the chart is drawn.
  const nearest = useMemo(() => {
    if (!pointer || readouts.length === 0) return null;
    let best: (typeof readouts)[number] | null = null;
    let bestPx = Infinity;
    for (const r of readouts) {
      const px = Math.abs(yOf(r.altitude) - pointer.y) / pointer.unitsPerPx;
      if (px < bestPx) {
        best = r;
        bestPx = px;
      }
    }
    return bestPx <= HOVER_RADIUS_PX ? best : null;
    // yOf is a pure function of the plot height.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pointer, readouts, PLOT_HEIGHT]);

  function renderChip(path: { label: string; color: string }) {
    const off = hidden.includes(path.label);
    return (
      <span key={path.label} className={`legend-chip ${off ? "off" : ""}`}>
        <button
          className="legend-toggle"
          onClick={() => onToggle?.(path.label)}
          aria-pressed={!off}
          title={off ? `Show ${path.label}` : `Hide ${path.label}`}
        >
          <i
            style={{
              background: off ? "transparent" : path.color,
              borderColor: path.color,
            }}
          />
          {path.label}
        </button>
        {onRemove && (
          <button
            className="legend-remove"
            onClick={() => onRemove(path.label)}
            title={`Remove ${path.label} from the chart`}
            aria-label={`Remove ${path.label} from the chart`}
          >
            ×
          </button>
        )}
      </span>
    );
  }

  // Pointer events, so a finger reads the chart as a mouse does: touch and
  // slide sideways to move the time line. The SVG's touch-action lets a
  // vertical swipe still scroll the page.
  function onMove(event: React.PointerEvent<SVGSVGElement>) {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const scale = W / rect.width;
    const x = (event.clientX - rect.left) * scale - M.left;
    const inside = x >= 0 && x <= PLOT_WIDTH;
    setHoverX(inside ? x : null);
    setPointer(inside ? {
      y: (event.clientY - rect.top) * scale - M.top,
      // The figure is the label's positioning parent, and the SVG is its
      // first child, so SVG-relative pixels are figure-relative too.
      left: event.clientX - rect.left,
      top: event.clientY - rect.top,
      unitsPerPx: scale,
      width: rect.width,
    } : null);
  }

  return (
    <figure className={`chart${layout.compact ? " chart-compact" : ""}`} ref={figureRef}>
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label="Altitude versus time for the selected objects"
        onPointerMove={onMove}
        onPointerDown={onMove}
        // A mouse leaving clears the readout; a finger lifting leaves it up
        // to be read.
        onPointerLeave={(e) => {
          if (e.pointerType === "mouse") { setHoverX(null); setPointer(null); }
        }}
      >
        <g transform={`translate(${M.left},${M.top})`}>
          {/* Sky background, then progressively darker bands for astronomical
              night and for true dark. The shading is the point: it shows at a
              glance which part of the night is actually worth using. */}
          <rect width={PLOT_WIDTH} height={PLOT_HEIGHT} className="chart-sky" />

          {data.astronomical_night.map((span, i) => (
            <rect
              key={`night-${i}`}
              x={xOf(span.start)}
              width={Math.max(0, xOf(span.end) - xOf(span.start))}
              height={PLOT_HEIGHT}
              className="chart-band-night"
            />
          ))}
          {data.dark_intervals.map((span, i) => (
            <rect
              key={`dark-${i}`}
              x={xOf(span.start)}
              width={Math.max(0, xOf(span.end) - xOf(span.start))}
              height={PLOT_HEIGHT}
              className="chart-band-dark"
            />
          ))}

          {/* Altitude gridlines every 15 degrees. */}
          {[0, 15, 30, 45, 60, 75, 90].map((altitude) => (
            <g key={altitude}>
              <line
                x1={0}
                x2={PLOT_WIDTH}
                y1={yOf(altitude)}
                y2={yOf(altitude)}
                className={altitude === 0 ? "chart-horizon" : "chart-grid"}
              />
              <text x={-8} y={yOf(altitude) + 4} className="chart-axis-label" textAnchor="end">
                {altitude}°
              </text>
            </g>
          ))}

          {/* Where the session ends. A curve that only climbs to the right of
              this line belongs to a night the observer has said they will not
              be awake for, which is the difference between "visible" and
              "visible late" everywhere else in the app. Drawn after the bands
              so it sits over them, before the curves so it sits under those. */}
          {session && (() => {
            const x = xOf(session.end);
            if (x < 0 || x > PLOT_WIDTH) return null;
            return (
              <g className="chart-session-end">
                <line x1={x} x2={x} y1={0} y2={PLOT_HEIGHT}
                      className="chart-session-line" />
                <text x={x - 5} y={12} textAnchor="end"
                      className="chart-session-label">
                  {formatTime(session.end, timeZone)}
                </text>
              </g>
            );
          })()}

          {/* The site's own obstruction, which is the altitude floor now that
              there is no universal one. Nothing is drawn for an unobstructed
              site: a line along the horizon would only restate the axis. */}
          {obstructionDeg > 0 && (
            <>
              <line
                x1={0}
                x2={PLOT_WIDTH}
                y1={yOf(obstructionDeg)}
                y2={yOf(obstructionDeg)}
                className="chart-floor"
              />
              <text x={4} y={yOf(obstructionDeg) - 5} className="chart-floor-label">
                {obstructionDeg.toFixed(0)}°{obstructionVaries ? " max" : ""}
              </text>
            </>
          )}

          {hourTicks.map((tick) => (
            <g key={tick.ms}>
              <line
                x1={xOfMs(tick.ms)}
                x2={xOfMs(tick.ms)}
                y1={0}
                y2={PLOT_HEIGHT}
                className="chart-grid"
              />
              <text
                x={xOfMs(tick.ms)}
                y={PLOT_HEIGHT + 20}
                className="chart-axis-label"
                textAnchor="middle"
              >
                {tick.label}
              </text>
            </g>
          ))}

          {paths
            .filter((path) => !hidden.includes(path.label))
            .map((path) => (
              <path
                key={path.label}
                d={path.d}
                className={`chart-line chart-line-${path.kind}${
                  FAINT_BODIES.has(path.label.toLowerCase()) ? " chart-line-faint" : ""}`}
                style={{ stroke: path.color }}
              />
            ))}

          {hoverX !== null && (
            <line x1={hoverX} x2={hoverX} y1={0} y2={PLOT_HEIGHT} className="chart-cursor" />
          )}

        </g>
      </svg>

      {nearest && pointer && (
        <div
          // Right of the pointer, or left of it near the chart's right edge
          // so it never spills out of the panel. aria-hidden: the readout
          // under the chart already says the same, for every curve.
          className={`chart-tip${pointer.left > pointer.width - 160 ? " flip" : ""}`}
          style={{ left: pointer.left, top: pointer.top }}
          aria-hidden="true"
        >
          <i style={{ background: nearest.color }} />
          {nearest.label} <span>{nearest.altitude.toFixed(0)}°</span>
        </div>
      )}

      {/* Legend as chips rather than SVG text, so each carries its own remove
          control and the plot can use the full width. Split by kind: solar
          system objects move night to night, constellations do not, and they
          are chosen from different places in the UI. */}
      {paths.length === 0 ? (
        <p className="muted small">
          Nothing charted. Add constellations from Deep Sky or the Moon and
          planets from Solar System, in the panel beside this one.
        </p>
      ) : (
        <div className="chart-legend">
          {LEGEND_SECTIONS.map((section) => {
            const chips = paths.filter((path) => section.kinds.includes(path.kind));
            if (chips.length === 0) return null;
            return (
              <div key={section.label} className="chart-legend-section">
                <div className="chart-legend-head">
                  <span className="chart-legend-label">{section.label}</span>
                  {/* One click for the whole group: "hide all" while any of
                      it is showing, "show all" once none is. Hiding keeps
                      the chips, as it does for one curve. */}
                  {onSetVisible && (() => {
                    const anyShown = chips.some((c) => !hidden.includes(c.label));
                    return (
                      <button
                        className="legend-all"
                        onClick={() => onSetVisible(chips.map((c) => c.label), !anyShown)}
                        aria-label={`${anyShown ? "Hide" : "Show"} all ${section.label.toLowerCase()}`}
                      >
                        {anyShown ? "hide all" : "show all"}
                      </button>
                    );
                  })()}
                </div>
                <div className="chart-legend-row">
                  {chips.map((path) => renderChip(path))}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Only rendered while hovering. The caption used to explain the bands
          and the hover affordance in a permanent line of text; the readout it
          describes appears the moment anyone tries. */}
      {hoverTime && (
        <figcaption>
          <div className="chart-readout">
            <strong>{formatTime(hoverTime.toISOString(), timeZone)}</strong>
            {readouts.map((r) => (
              <span key={r.label}>
                <i style={{ background: r.color }} />
                {r.label} {r.altitude.toFixed(0)}° / az {r.azimuth.toFixed(0)}°
              </span>
            ))}
          </div>
        </figcaption>
      )}
    </figure>
  );
}
