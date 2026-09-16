/** Hourly conditions across the night: score, cloud, wind, moon altitude. */

import type { SlotModel } from "../api";
import { formatTime, scoreColor } from "../format";

interface Props {
  slots: SlotModel[];
  timeZone: string;
  weatherAvailable: boolean;
}

export function ConditionsStrip({ slots, timeZone, weatherAvailable }: Props) {
  // Slots are half-hourly; show every other one so the strip stays readable.
  const hourly = slots.filter((_, index) => index % 2 === 0);
  if (hourly.length === 0) return null;

  return (
    <section className="panel">
      <h3>Hourly conditions</h3>
      <div className="strip">
        {hourly.map((slot) => (
          <div key={slot.time} className="strip-cell">
            <span className="strip-time">{formatTime(slot.time, timeZone)}</span>

            <span
              className="strip-bar"
              title={`Deep-sky ${slot.deep_sky.toFixed(0)}`}
              aria-label={`Deep sky score ${slot.deep_sky.toFixed(0)}`}
            >
              <span
                style={{
                  height: `${Math.max(2, slot.deep_sky)}%`,
                  background: weatherAvailable ? scoreColor(slot.deep_sky) : "var(--muted)",
                }}
              />
            </span>

            <span className="strip-value">
              {weatherAvailable ? slot.deep_sky.toFixed(0) : "—"}
            </span>
            <span className="strip-sub">
              {slot.cloud_cover === null ? "—" : `${slot.cloud_cover.toFixed(0)}%`}
            </span>
            <span className="strip-sub">
              {slot.wind_gust_kmh === null ? "—" : `${slot.wind_gust_kmh.toFixed(0)}`}
            </span>
            <span className={`strip-sub ${slot.moon_altitude_deg > 0 ? "moon-up" : ""}`}>
              {slot.moon_altitude_deg.toFixed(0)}°
            </span>
          </div>
        ))}
      </div>
      <div className="strip-key muted">
        rows: deep-sky score · cloud % · wind gust km/h · moon altitude
        (highlighted when up)
      </div>
    </section>
  );
}
