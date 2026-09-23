/**
 * The dashboard's shape, drawn before its numbers arrive.
 *
 * Changing site or date blanks the night on purpose -- stale numbers under a
 * new site's name read as wrong data -- and this used to collapse the page to
 * one "Loading…" line and then snap back to a full dashboard a moment later,
 * a jump of most of a screen each time. This holds the same regions at the
 * same sizes, so the page stays put and only the contents fill in.
 *
 * It mirrors App's layout by hand rather than rendering the real panels
 * empty: the real ones would each need a "no data yet" branch, and a layout
 * placeholder should not be able to throw.
 */

import { HEIGHT as CHART_HEIGHT, WIDTH as CHART_WIDTH } from "./AltitudeChart";

/** A grey bar standing in for a line of text or a number. */
function Bar({ width, height = 12 }: { width: string | number; height?: number }) {
  return <span className="skeleton-bar" style={{ width, height }} />;
}

export function DashboardSkeleton({ splitStyle }: {
  /** The same split the real row uses, so the two line up. */
  splitStyle?: React.CSSProperties;
}) {
  return (
    <main className="dashboard-skeleton" aria-busy="true" aria-label="Loading tonight's plan">
      <section className="panel score-panel">
        <div className="score-top">
          <div className="dials">
            {[0, 1].map((i) => (
              <div key={i} className="dial">
                <span className="skeleton-dial" />
                {/* Two lines, as the real caption has: label, then average. */}
                <Bar width={64} />
                <Bar width={44} height={10} />
              </div>
            ))}
          </div>
          <div className="score-body">
            <div className="key-facts">
              {Array.from({ length: 9 }, (_, i) => (
                <div key={i}>
                  <Bar width="45%" height={9} />
                  {/* Tall: the real figures, "21:04–01:00", wrap to two lines
                      -- except on a phone, where they fit on one and the
                      stylesheet makes this shorter to match. */}
                  <span className="skeleton-bar skeleton-figure" style={{ width: "70%" }} />
                  <Bar width="40%" height={9} />
                </div>
              ))}
            </div>
          </div>
        </div>
        {/* The "More info" disclosure link under the dials and facts. */}
        <Bar width={70} height={16} />
      </section>

      <div className="dashboard-row resizable" style={splitStyle}>
        <section className="panel chart-panel">
          <div className="panel-head">
            <Bar width={80} height={16} />
          </div>
          {/* The chart's own proportions, read from it, so the two cannot
              drift apart when one of them changes. */}
          <span className="skeleton-block skeleton-chart"
                style={{ "--chart-aspect": `${CHART_WIDTH} / ${CHART_HEIGHT}` } as React.CSSProperties} />
          {/* The legend chips under the chart: two rows of them. */}
          <div className="skeleton-legend">
            {[72, 64, 70, 60, 66].map((w, i) => (
              <Bar key={i} width={w} height={26} />
            ))}
          </div>
        </section>
        {/* Holds the divider's track, so the columns sit where the real
            ones will. */}
        <span className="splitter" aria-hidden="true" />
        <section className="panel sky-panel">
          <div className="panel-head">
            <span className="skeleton-bar skeleton-tabs" />
          </div>
          <span className="skeleton-block" style={{ height: 36, marginBottom: 14 }} />
          {Array.from({ length: 8 }, (_, i) => (
            <div key={i} className="skeleton-row">
              <Bar width={28} height={16} />
              {/* A constellation's name, and on a phone its hours under it. */}
              <span className="skeleton-row-text">
                <Bar width={`${55 - (i % 3) * 10}%`} />
                <span className="skeleton-bar skeleton-row-sub" />
              </span>
              <Bar width={40} />
            </div>
          ))}
        </section>
      </div>
    </main>
  );
}
