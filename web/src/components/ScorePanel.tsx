/**
 * The headline panel: two scores, the key night facts, and — behind a
 * disclosure — the full twilight table and factor breakdown.
 *
 * The dials used to sit alone with a lot of empty space beside them, and the
 * night times were a separate box. They are one panel now: the six facts an
 * observer actually acts on sit next to the dials, and everything else is one
 * click away.
 *
 * PLAN.md §3.2: "always show the factor breakdown so the number is auditable."
 * The limiting factor is named without any interaction; the full six-factor
 * table is in the disclosure.
 *
 * When weather is unavailable the grades are suppressed rather than shown as
 * an A. Every weather-derived factor defaults to 1.0 with no data, so a grade
 * would be a confident verdict on a night we know nothing about.
 */

import { useState } from "react";
import type { FactorsModel, NightWindowModel, ScoreModel } from "../api";
import {
  formatHours,
  formatTime,
  moonPhaseName,
  scoreColor,
  timeZoneAbbreviation,
} from "../format";

const FACTOR_LABELS: Record<keyof FactorsModel, string> = {
  clear: "Cloud",
  transparency: "Transparency",
  moon: "Moon",
  seeing: "Seeing",
  wind: "Wind",
  dew: "Dew",
};

function Dial({ label, score, grade, mean, gradeable }: {
  label: string;
  score: number;
  grade: string;
  mean: number;
  gradeable: boolean;
}) {
  const radius = 46;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - (gradeable ? score : 0) / 100);

  return (
    <div className="dial">
      <svg viewBox="0 0 120 120" aria-label={`${label} score`}>
        <circle cx="60" cy="60" r={radius} className="dial-track" />
        {gradeable && (
          <circle
            cx="60" cy="60" r={radius}
            className="dial-value"
            style={{
              stroke: scoreColor(score),
              strokeDasharray: circumference,
              strokeDashoffset: offset,
            }}
          />
        )}
        <text x="60" y="58" className="dial-number">
          {gradeable ? Math.round(score) : "—"}
        </text>
        <text x="60" y="78" className="dial-grade">
          {gradeable ? grade : "no data"}
        </text>
      </svg>
      <div className="dial-caption">
        <strong>{label}</strong>
        {gradeable && <span className="muted">avg {mean.toFixed(0)}</span>}
      </div>
    </div>
  );
}

function FactorBar({ name, value }: { name: string; value: number }) {
  return (
    <div className="factor">
      <span className="factor-name">{name}</span>
      <span className="factor-track">
        <span
          className="factor-fill"
          style={{ width: `${value * 100}%`, background: scoreColor(value * 100) }}
        />
      </span>
      <span className="factor-value">{value.toFixed(2)}</span>
    </div>
  );
}

export function ScorePanel({ score, window: night }: {
  score: ScoreModel;
  window: NightWindowModel;
}) {
  const [open, setOpen] = useState(false);
  const tz = night.location.timezone;

  return (
    <section className="panel score-panel">
      <div className="score-top">
        <div className="dials">
          <Dial
            label="Deep sky"
            score={score.deep_sky_peak}
            grade={score.deep_sky_grade}
            mean={score.deep_sky_mean}
            gradeable={score.is_gradeable}
          />
          <Dial
            label="Planetary"
            score={score.planetary_peak}
            grade={score.planetary_grade}
            mean={score.planetary_mean}
            gradeable={score.is_gradeable}
          />
        </div>

        <div className="score-body">
          <p className="verdict">{score.verdict}</p>

          {/* The six facts you act on, in the space the dials used to leave empty. */}
          <div className="key-facts">
            <div>
              <span>True dark</span>
              <strong>{formatHours(night.dark_hours)}</strong>
              {night.dark_intervals.length > 0 && (
                <em>
                  {formatTime(night.dark_intervals[0].start, tz)}–
                  {formatTime(
                    night.dark_intervals[night.dark_intervals.length - 1].end, tz,
                  )}
                </em>
              )}
            </div>
            <div>
              <span>Astronomical dusk</span>
              <strong>{formatTime(night.astronomical_dusk, tz)}</strong>
              <em>dawn {formatTime(night.astronomical_dawn, tz)}</em>
            </div>
            <div>
              <span>Sunset</span>
              <strong>{formatTime(night.sunset, tz)}</strong>
              <em>sunrise {formatTime(night.sunrise, tz)}</em>
            </div>
            <div>
              <span>Moon</span>
              <strong>{(night.moon_illumination * 100).toFixed(0)}%</strong>
              <em>{moonPhaseName(night.moon_illumination, night.moon_waxing)}</em>
            </div>
            <div>
              <span>Moon rise / set</span>
              <strong>
                {formatTime(night.moonrise, tz)} / {formatTime(night.moonset, tz)}
              </strong>
              <em>{night.moon_up_at_dusk ? "up at dusk" : "down at dusk"}</em>
            </div>
            <div>
              <span>Best window</span>
              <strong>
                {score.best_window
                  ? `${formatTime(score.best_window.start, tz)}–${formatTime(
                      score.best_window.end, tz)}`
                  : "—"}
              </strong>
              <em>
                {score.limiting_factor
                  ? `limited by ${score.limiting_factor}`
                  : "nothing limiting"}
              </em>
            </div>
          </div>
        </div>
      </div>

      {!score.weather_available && (
        <p className="warning">
          <strong>No weather data.</strong> {score.weather_note}. These scores
          reflect darkness and moonlight only and are not a verdict on
          conditions — which is why no grade is shown.
        </p>
      )}
      {score.seeing_estimated && (
        <p className="warning">
          Seeing and transparency are <strong>estimated</strong> — this night is
          past 7Timer's 72-hour horizon, so a humidity and wind proxy was used.
        </p>
      )}
      {score.dew_warning && (
        <p className="warning">
          <strong>Dew warning:</strong> the dew point spread drops below 2 °C.
        </p>
      )}

      <button className="link-button" onClick={() => setOpen(!open)}>
        {open ? "Less" : "More"} — twilight times
        {score.peak_factors_deep_sky ? " and factor breakdown" : ""}
      </button>

      {open && (
        <div className="score-more">
          <div>
            <h4>Twilight — all times {timeZoneAbbreviation(tz)}</h4>
            <dl className="facts">
              {([
                ["Sunset", night.sunset],
                ["Civil twilight ends", night.civil_dusk],
                ["Nautical twilight ends", night.nautical_dusk],
                ["Astronomical dusk", night.astronomical_dusk],
                ["Astronomical dawn", night.astronomical_dawn],
                ["Nautical twilight begins", night.nautical_dawn],
                ["Civil twilight begins", night.civil_dawn],
                ["Sunrise", night.sunrise],
              ] as [string, string | null][]).map(([label, value]) => (
                <div key={label}>
                  <dt>{label}</dt>
                  <dd>{formatTime(value, tz)}</dd>
                </div>
              ))}
              <div>
                <dt>Astronomical night</dt>
                <dd>{formatHours(night.astronomical_night_hours)}</dd>
              </div>
            </dl>
            <p className="muted small">
              Moon rise and set are calendar-day events, as published almanacs
              report them. The true dark window is computed independently.
            </p>
          </div>

          {score.peak_factors_deep_sky && (
            <div className="factors-pair">
              <div>
                <h4>Deep sky at peak</h4>
                {(Object.keys(FACTOR_LABELS) as (keyof FactorsModel)[]).map((key) => (
                  <FactorBar
                    key={key}
                    name={FACTOR_LABELS[key]}
                    value={score.peak_factors_deep_sky![key]}
                  />
                ))}
              </div>
              <div>
                <h4>Planetary at peak</h4>
                {(Object.keys(FACTOR_LABELS) as (keyof FactorsModel)[]).map((key) => (
                  <FactorBar
                    key={key}
                    name={FACTOR_LABELS[key]}
                    value={score.peak_factors_planetary![key]}
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
