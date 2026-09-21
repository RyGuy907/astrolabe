/**
 * The night hour by hour: score, cloud, wind, temperature, moon altitude.
 *
 * This was a row of tall bars with four numbers stacked under each, and it
 * took a full-width panel and most of a screen to present about sixty
 * numbers. It is now a small matrix -- hours across, quantities down -- with
 * the score carried by the cell's tint rather than by a 60px column of empty
 * space above it. Same numbers, a fifth of the height, which is what lets it
 * live inside the score panel's disclosure instead of owning a panel.
 */

import type { SlotModel } from "../api";
import { formatTemp, formatTime, scoreColor } from "../format";

interface Props {
  slots: SlotModel[];
  timeZone: string;
  weatherAvailable: boolean;
}

export function ConditionsStrip({ slots, timeZone, weatherAvailable }: Props) {
  // Slots are half-hourly; show every other one so the row stays readable.
  const hourly = slots.filter((_, index) => index % 2 === 0);

  // No slots is a real answer, not a broken panel -- most starkly at a polar
  // site in summer, where the engine correctly reports no astronomical night.
  if (hourly.length === 0) {
    return (
      <p className="muted small">
        No hours to score for this night — either there is no astronomical
        night at this latitude on this date, or the forecast does not reach it.
      </p>
    );
  }

  const hasTemperature = hourly.some((slot) => slot.temperature_c !== null);

  return (
    <div className="hourly">
      <h4>Hour by hour</h4>
      <div className="hourly-scroll">
        <table className="hourly-grid">
          <thead>
            <tr>
              <th scope="col" className="hourly-corner">
                <span className="visually-hidden">Quantity</span>
              </th>
              {hourly.map((slot) => (
                <th key={slot.time} scope="col">
                  {formatTime(slot.time, timeZone)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">Score</th>
              {hourly.map((slot) => (
                <td
                  key={slot.time}
                  className="hourly-score"
                  style={
                    weatherAvailable
                      ? {
                          // Tint rather than a bar: the colour carries the
                          // reading and the cell costs one line of height.
                          background: scoreColor(slot.deep_sky),
                          color: "#0b0e14",
                        }
                      : undefined
                  }
                >
                  {weatherAvailable ? (
                    <>
                      <span className="visually-hidden">Deep sky </span>
                      {slot.deep_sky.toFixed(0)}
                      {/* The planetary score for the same hour, as a disc on
                          the cell's right edge in its own grade colour. The
                          two modes can differ a lot -- a bright Moon costs
                          deep sky and not planets -- so one tinted number per
                          hour hid half of what the hour was good for. */}
                      <span
                        className="hourly-planet"
                        style={{ background: scoreColor(slot.planetary) }}
                        title={`Planetary ${slot.planetary.toFixed(0)}`}
                      >
                        <span className="visually-hidden">, planetary </span>
                        {slot.planetary.toFixed(0)}
                      </span>
                    </>
                  ) : "—"}
                </td>
              ))}
            </tr>
            <tr>
              <th scope="row">Cloud</th>
              {hourly.map((slot) => (
                <td key={slot.time}>
                  {slot.cloud_cover === null
                    ? "—"
                    : `${slot.cloud_cover.toFixed(0)}%`}
                </td>
              ))}
            </tr>
            <tr>
              <th scope="row">Gust <span className="muted">km/h</span></th>
              {hourly.map((slot) => (
                <td key={slot.time}>
                  {slot.wind_gust_kmh === null
                    ? "—"
                    : slot.wind_gust_kmh.toFixed(0)}
                </td>
              ))}
            </tr>
            {hasTemperature && (
              <tr>
                <th scope="row">Temp</th>
                {hourly.map((slot) => (
                  <td key={slot.time}>{formatTemp(slot.temperature_c)}</td>
                ))}
              </tr>
            )}
            <tr>
              <th scope="row">Moon</th>
              {hourly.map((slot) => (
                <td
                  key={slot.time}
                  className={slot.moon_altitude_deg > 0 ? "moon-up" : undefined}
                >
                  {slot.moon_altitude_deg.toFixed(0)}°
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}
