/** Sun, moon and the two night windows, in the site's local time. */

import type { NightWindowModel } from "../api";
import {
  formatHours,
  formatTime,
  moonPhaseName,
  timeZoneAbbreviation,
} from "../format";

export function NightSummary({ window }: { window: NightWindowModel }) {
  const tz = window.location.timezone;
  const rows: [string, string | null][] = [
    ["Sunset", window.sunset],
    ["Civil twilight ends", window.civil_dusk],
    ["Nautical twilight ends", window.nautical_dusk],
    ["Astronomical dusk", window.astronomical_dusk],
    ["Astronomical dawn", window.astronomical_dawn],
    ["Sunrise", window.sunrise],
  ];

  return (
    <section className="panel">
      <div className="panel-head">
        <h3>The night</h3>
        <span className="muted">all times {timeZoneAbbreviation(tz)}</span>
      </div>

      <div className="two-col">
        <dl className="facts">
          {rows.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{formatTime(value, tz)}</dd>
            </div>
          ))}
        </dl>

        <dl className="facts">
          <div>
            <dt>Moonrise</dt>
            <dd>{formatTime(window.moonrise, tz)}</dd>
          </div>
          <div>
            <dt>Moonset</dt>
            <dd>{formatTime(window.moonset, tz)}</dd>
          </div>
          <div>
            <dt>Illuminated</dt>
            <dd>
              {(window.moon_illumination * 100).toFixed(0)}%{" "}
              <span className="muted">
                {moonPhaseName(window.moon_illumination, window.moon_waxing)}
              </span>
            </dd>
          </div>
          <div>
            <dt>At dusk</dt>
            <dd>{window.moon_up_at_dusk ? "moon up" : "moon down"}</dd>
          </div>
          <div>
            <dt>Astronomical night</dt>
            <dd>{formatHours(window.astronomical_night_hours)}</dd>
          </div>
          <div className="highlight">
            <dt>True dark</dt>
            <dd>
              {formatHours(window.dark_hours)}
              {window.dark_intervals.length > 0 && (
                <span className="muted">
                  {" "}
                  {window.dark_intervals
                    .map(
                      (span) =>
                        `${formatTime(span.start, tz)}–${formatTime(span.end, tz)}`,
                    )
                    .join(", ")}
                </span>
              )}
            </dd>
          </div>
        </dl>
      </div>

      {/* Moon rise/set are calendar-day events, matching published almanacs.
          Saying so avoids the reasonable question of why moonrise can look
          "wrong" relative to the observing window. */}
      <p className="muted small">
        Moon rise and set are calendar-day events, as published almanacs report
        them. The true dark window is computed independently.
      </p>
    </section>
  );
}
