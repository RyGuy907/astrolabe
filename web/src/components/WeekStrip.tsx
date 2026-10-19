/**
 * The week ahead, beside tonight's facts.
 *
 * "Is tonight worth it?" usually follows "which night this week is?", and
 * the dashboard could only answer that a night at a time: step the date,
 * read the dials, step again. Here are seven nights side by side, from one
 * forecast, so the good ones stand out by colour before anything is read.
 *
 * Each column is that night's own dashboard in miniature -- the same
 * `score_night` over the same default session (`engine/outlook.py`) -- and
 * clicking one opens it. Three choices worth knowing:
 *
 * * **Averages, not peaks.** The dials' big number is the best half hour,
 *   which is right for "is there a window worth going out for" and wrong for
 *   comparing nights: a night averaging 89% cloud scored a planetary A off a
 *   single clear slot. The cells are the night's average; the peak and when
 *   it falls are in each column's tooltip.
 * * **Dusk to 01:00 for every night.** Hours set in the session editor are
 *   instants on one night and mean nothing on the next, and comparing nights
 *   needs them measured alike.
 * * **Scores first.** Closed, the strip is the dates and the two scores --
 *   enough to spot the good nights. Cloud, Moon and the overnight low join
 *   them when the panel's "More info" is open, with the rest of the detail.
 * * **Honest past the forecast.** A night the forecast does not reach shows
 *   a dash, as the dials do, rather than a score of darkness and moonlight
 *   dressed up as a verdict. Past 72 hours the planetary figure leans on an
 *   estimated seeing and is marked with a tilde.
 */

import type { CSSProperties } from "react";
import type { WeekNightModel } from "../api";
import { formatHours, formatNightHeading, formatTemp, formatTime,
         moonPhaseName, scoreColor } from "../format";

/** "Mon 19 Oct" for a tooltip. */
function longDate(isoDate: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "UTC", weekday: "short", day: "numeric", month: "short",
  }).format(new Date(`${isoDate}T12:00:00Z`));
}

/** Everything the column does not have room for, as one tooltip. */
function describe(night: WeekNightModel, timeZone: string): string {
  const lines = [longDate(night.date)];
  if (night.session) {
    lines[0] += ` · ${formatTime(night.session.start, timeZone)}–` +
                formatTime(night.session.end, timeZone);
  }
  if (night.is_gradeable) {
    const best = night.best_window
      ? ` (${formatTime(night.best_window.start, timeZone)}–` +
        `${formatTime(night.best_window.end, timeZone)})`
      : "";
    lines.push(`Deep sky: avg ${night.deep_sky_mean.toFixed(0)}, ` +
               `best ${night.deep_sky_peak.toFixed(0)}${best}`);
    lines.push(`Planetary: avg ${night.planetary_mean.toFixed(0)}, ` +
               `best ${night.planetary_peak.toFixed(0)}`);
    if (night.limiting_factor) lines.push(`Limited by ${night.limiting_factor}`);
  } else {
    lines.push(`No forecast${night.weather_note ? `: ${night.weather_note}` : ""}`);
  }
  if (night.cloud_mean_pct !== null) {
    lines.push(`Cloud avg ${night.cloud_mean_pct.toFixed(0)}%` +
               (night.temperature_low_c !== null
                 ? ` · low ${formatTemp(night.temperature_low_c)}` : ""));
  }
  lines.push(`Moon ${(night.moon_illumination * 100).toFixed(0)}%, ` +
             `${moonPhaseName(night.moon_illumination, night.moon_waxing)} · ` +
             `${formatHours(night.dark_hours)} of true dark`);
  if (night.seeing_estimated) lines.push("Seeing estimated: past the 72-hour forecast");
  return lines.join("\n");
}

function Score({ value, gradeable, estimated = false }: {
  value: number;
  gradeable: boolean;
  estimated?: boolean;
}) {
  if (!gradeable) return <span className="week-none">—</span>;
  return (
    <span className="week-score" style={{ color: scoreColor(value) }}>
      {estimated ? "~" : ""}{value.toFixed(0)}
    </span>
  );
}

export function WeekStrip({ nights, timeZone, selected, onSelect, expanded, style }: {
  nights: WeekNightModel[];
  timeZone: string;
  /** The night the dashboard is showing, highlighted when it is in view. */
  selected: string;
  onSelect: (date: string) => void;
  /** The score panel's "More info" is open: show cloud, Moon and the low. */
  expanded: boolean;
  /** Where it sits in the facts grid; worked out by the score panel. */
  style?: CSSProperties;
}) {
  const cell = (night: WeekNightModel) =>
    night.date === selected ? "is-selected" : undefined;

  return (
    <div className="week-strip" style={style}>
      <div className="week-scroll">
        {/* No visible heading: the dates say what it is. Named for a
            screen reader all the same. */}
        <table className="week-table" aria-label={`Next ${nights.length} nights`}>
          <thead>
            <tr>
              <th scope="col"><span className="visually-hidden">Night</span></th>
              {nights.map((night) => {
                const { weekday, day } = formatNightHeading(night.date);
                return (
                  <th key={night.date} scope="col" className={cell(night)}>
                    <button
                      onClick={() => onSelect(night.date)}
                      title={describe(night, timeZone)}
                      aria-pressed={night.date === selected}
                      aria-label={`Show the night of ${longDate(night.date)}`}
                    >
                      <span className="week-weekday">{weekday}</span>
                      <span className="week-day">{day}</span>
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">Deep sky</th>
              {nights.map((night) => (
                <td key={night.date} className={cell(night)}
                    title={describe(night, timeZone)}>
                  <Score value={night.deep_sky_mean} gradeable={night.is_gradeable} />
                </td>
              ))}
            </tr>
            <tr>
              <th scope="row">Planets</th>
              {nights.map((night) => (
                <td key={night.date} className={cell(night)}
                    title={describe(night, timeZone)}>
                  <Score value={night.planetary_mean} gradeable={night.is_gradeable}
                         estimated={night.seeing_estimated} />
                </td>
              ))}
            </tr>
            {expanded && (<>
            <tr>
              <th scope="row">Cloud</th>
              {nights.map((night) => (
                <td key={night.date} className={cell(night)}>
                  {night.cloud_mean_pct === null
                    ? <span className="week-none">—</span>
                    : `${night.cloud_mean_pct.toFixed(0)}%`}
                </td>
              ))}
            </tr>
            <tr>
              <th scope="row">Moon</th>
              {nights.map((night) => (
                <td key={night.date} className={cell(night)}>
                  {(night.moon_illumination * 100).toFixed(0)}%
                </td>
              ))}
            </tr>
            <tr>
              <th scope="row">Low</th>
              {nights.map((night) => (
                <td key={night.date} className={cell(night)}>
                  {night.temperature_low_c === null
                    ? <span className="week-none">—</span>
                    : formatTemp(night.temperature_low_c)}
                </td>
              ))}
            </tr>
            </>)}
          </tbody>
        </table>
      </div>
    </div>
  );
}
