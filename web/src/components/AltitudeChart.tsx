/**
 * Altitude vs time — PLAN.md §4 calls this "the single highest-value visual".
 *
 * Hand-rolled SVG rather than a charting library, because the thing that makes
 * this chart useful is the astronomy-specific furniture a generic library
 * fights you on: twilight bands shading from dusk to true dark, the altitude
 * floor, the obstruction horizon drawn as a filled region under the curves,
 * and a crosshair that reads every series at once.
 */

import { useMemo, useRef, useState } from "react";
import type { AltitudeResponse } from "../api";
import { formatTime } from "../format";

const WIDTH = 1000;
const HEIGHT = 420;
const MARGIN = { top: 16, right: 18, bottom: 40, left: 48 };

const PLOT_WIDTH = WIDTH - MARGIN.left - MARGIN.right;
const PLOT_HEIGHT = HEIGHT - MARGIN.top - MARGIN.bottom;

const SERIES_COLORS = [
  "#f4c95d", "#6fb1fc", "#f78da7", "#7ddf9a",
  "#c39bd3", "#f0946b", "#79d0d8", "#b8c46a",
];

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
}

export function AltitudeChart({
  data, timeZone, hidden = [], onToggle, onRemove,
}: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [hoverX, setHoverX] = useState<number | null>(null);

  const startMs = new Date(data.start).getTime();
  const endMs = new Date(data.end).getTime();

  const xOf = (iso: string) =>
    ((new Date(iso).getTime() - startMs) / (endMs - startMs)) * PLOT_WIDTH;
  const xOfMs = (ms: number) => ((ms - startMs) / (endMs - startMs)) * PLOT_WIDTH;
  // Altitude runs -10 to 90: a little below the horizon so setting objects
  // visibly leave the plot rather than being clipped at the axis.
  const yOf = (altitude: number) =>
    PLOT_HEIGHT - ((altitude + 10) / 100) * PLOT_HEIGHT;

  const paths = useMemo(
    () =>
      data.series.map((series, index) => {
        const points = series.points
          .map((p) => `${xOf(p.time).toFixed(1)},${yOf(p.altitude_deg).toFixed(1)}`)
          .join(" L ");
        return {
          label: series.label,
          kind: series.kind,
          // Indexed against the full list, so hiding one curve never
          // recolours the others.
          color: SERIES_COLORS[index % SERIES_COLORS.length],
          d: `M ${points}`,
        };
      }),
    [data],
  );

  // Hour ticks on the site's clock, not the browser's.
  const hourTicks = useMemo(() => {
    const ticks: { ms: number; label: string }[] = [];
    const first = new Date(startMs);
    first.setUTCMinutes(0, 0, 0);
    for (let ms = first.getTime(); ms <= endMs; ms += 3600_000) {
      if (ms < startMs) continue;
      ticks.push({
        ms,
        label: formatTime(new Date(ms).toISOString(), timeZone),
      });
    }
    return ticks.filter((_, i) => i % 2 === 0);
  }, [startMs, endMs, timeZone]);

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
        color: SERIES_COLORS[index % SERIES_COLORS.length],
        altitude: nearest.altitude_deg,
        azimuth: nearest.azimuth_deg,
      };
    });
  }, [hoverTime, data, hidden]);

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

  function onMove(event: React.MouseEvent<SVGSVGElement>) {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const scale = WIDTH / rect.width;
    const x = (event.clientX - rect.left) * scale - MARGIN.left;
    setHoverX(x >= 0 && x <= PLOT_WIDTH ? x : null);
  }

  return (
    <figure className="chart">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label="Altitude versus time for the selected objects"
        onMouseMove={onMove}
        onMouseLeave={() => setHoverX(null)}
      >
        <g transform={`translate(${MARGIN.left},${MARGIN.top})`}>
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

          {/* The configured altitude floor — below this we do not recommend. */}
          <line
            x1={0}
            x2={PLOT_WIDTH}
            y1={yOf(data.min_altitude_deg)}
            y2={yOf(data.min_altitude_deg)}
            className="chart-floor"
          />
          <text x={4} y={yOf(data.min_altitude_deg) - 5} className="chart-floor-label">
            {data.min_altitude_deg}° floor
          </text>

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
                className={`chart-line chart-line-${path.kind}`}
                style={{ stroke: path.color }}
              />
            ))}

          {hoverX !== null && (
            <line x1={hoverX} x2={hoverX} y1={0} y2={PLOT_HEIGHT} className="chart-cursor" />
          )}

        </g>
      </svg>

      {/* Legend as chips rather than SVG text, so each carries its own remove
          control and the plot can use the full width. Split by kind: solar
          system objects move night to night, constellations do not, and they
          are chosen from different places in the UI. */}
      {paths.length === 0 ? (
        <p className="muted small">
          Nothing charted. Add planets or constellations from the panel beside
          this one.
        </p>
      ) : (
        <div className="chart-legend">
          {LEGEND_SECTIONS.map((section) => {
            const chips = paths.filter((path) => section.kinds.includes(path.kind));
            if (chips.length === 0) return null;
            return (
              <div key={section.label} className="chart-legend-section">
                <span className="chart-legend-label">{section.label}</span>
                <div className="chart-legend-row">
                  {chips.map((path) => renderChip(path))}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <figcaption>
        {hoverTime ? (
          <div className="chart-readout">
            <strong>{formatTime(hoverTime.toISOString(), timeZone)}</strong>
            {readouts.map((r) => (
              <span key={r.label}>
                <i style={{ background: r.color }} />
                {r.label} {r.altitude.toFixed(0)}° / az {r.azimuth.toFixed(0)}°
              </span>
            ))}
          </div>
        ) : (
          <span className="muted">
            Sunset to sunrise. Darker bands are astronomical night and true dark
            (moon down). Hover for altitude and azimuth.
          </span>
        )}
      </figcaption>
    </figure>
  );
}
