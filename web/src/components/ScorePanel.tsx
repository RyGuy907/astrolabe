/**
 * The headline panel: two scores, the key night facts, and — behind a
 * disclosure — the full twilight table and factor breakdown.
 *
 * The dials used to sit alone with a lot of empty space beside them, and the
 * night times were a separate box. They are one panel now: the six facts an
 * observer actually acts on sit next to the dials, and everything else is one
 * click away.
 *
 * After the facts comes the week ahead (`WeekStrip`), in whatever the facts
 * leave empty of their last row, so the next good night is visible from
 * tonight's dashboard without stepping through the dates.
 *
 * PLAN.md §3.2: "always show the factor breakdown so the number is auditable."
 * The limiting factor is named without any interaction; the full six-factor
 * table is in the disclosure.
 *
 * When weather is unavailable the grades are suppressed rather than shown as
 * an A. Every weather-derived factor defaults to 1.0 with no data, so a grade
 * would be a confident verdict on a night we know nothing about.
 */

import { Fragment, useLayoutEffect, useMemo, useRef, useState } from "react";
import type {
  FactorsModel,
  NightWindowModel,
  ObservingWindow,
  ScoreModel,
  SlotModel,
  WeekNightModel,
} from "../api";
import { ConditionsStrip } from "./ConditionsStrip";
import { SessionEditor } from "./SessionEditor";
import { WeekStrip } from "./WeekStrip";
import {
  formatHours,
  formatTemp,
  formatTempSpread,
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
  // A score of 0 would draw a zero-length arc, which looks like the dial
  // failed to load rather than a real (terrible) score. A sliver of red at
  // the top says "this is a score, and it is zero".
  const MIN_ARC = 3;
  const shown = gradeable ? Math.max(score, MIN_ARC) : 0;
  const offset = circumference * (1 - shown / 100);

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

/** One factor's bar and value, for a cell of the factor table. */
function FactorCell({ value }: { value: number }) {
  return (
    <span className="factor-cell">
      <span className="factor-track">
        <span
          className="factor-fill"
          style={{ width: `${value * 100}%`, background: scoreColor(value * 100) }}
        />
      </span>
      <span className="factor-value">{value.toFixed(2)}</span>
    </span>
  );
}

/** Narrower than this, the week goes on a row of its own under the facts
 *  rather than squeezing seven nights into the gap after the last one. */
const WEEK_MIN_WIDTH = 360;

/**
 * Where the week sits in the facts grid: in the columns left empty after the
 * last fact, when they are wide enough, else across a row of its own.
 *
 * The grid is auto-fit, so how many columns there are -- and so how many
 * the last row leaves free -- depends on the screen: three at 1600 px, seven
 * at 1920, none at 1150, where the facts fill three full rows. CSS cannot
 * say "span whatever is left of this row", so this measures the tracks and
 * counts. Re-measured whenever the grid is resized.
 */
function useWeekPlacement(factCount: number) {
  const gridRef = useRef<HTMLDivElement>(null);
  const [column, setColumn] = useState("1 / -1");

  useLayoutEffect(() => {
    const grid = gridRef.current;
    if (!grid) return;
    const place = () => {
      const style = getComputedStyle(grid);
      const tracks = style.gridTemplateColumns.split(" ").filter(Boolean);
      const columns = tracks.length;
      const width = parseFloat(tracks[0]) || 0;
      const gap = parseFloat(style.columnGap) || 0;
      const free = columns > 0 ? (columns - (factCount % columns)) % columns : 0;
      const room = free * width + Math.max(0, free - 1) * gap;
      setColumn(free > 0 && room >= WEEK_MIN_WIDTH ? `span ${free}` : "1 / -1");
    };
    place();
    const observer = new ResizeObserver(place);
    observer.observe(grid);
    return () => observer.disconnect();
  }, [factCount]);

  return { gridRef, column };
}

export function ScorePanel({ score, window: night, session, onSessionChange,
                             week, onSelectNight }: {
  score: ScoreModel;
  window: NightWindowModel;
  session: ObservingWindow | null;
  /** null restores the default hours: dusk to 01:00 local. */
  onSessionChange: (session: [string, string] | null) => void;
  /** The next seven nights, or null while they load or if they could not. */
  week: WeekNightModel[] | null;
  /** Open another night from the week. */
  onSelectNight: (date: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const tz = night.location.timezone;

  // Across the scored slots, not the calendar day: the question is what it
  // will be like while you are standing outside, and the afternoon high has
  // nothing to do with that. Slots without a reading are skipped rather than
  // counted as zero.
  function spread(pick: (slot: SlotModel) => number | null) {
    const values = score.slots
      .map(pick)
      .filter((value): value is number => value !== null);
    if (values.length === 0) return null;
    return {
      low: Math.min(...values),
      high: Math.max(...values),
      mean: values.reduce((total, value) => total + value, 0) / values.length,
    };
  }

  const temps = useMemo(() => spread((slot) => slot.temperature_c),
                        [score.slots]);
  // Cloud earns the same treatment for a specific reason: a night can average
  // 48% cloud and still show a peak-slot clear factor of 1.00, because the
  // peak slot is the least cloudy one. Seeing "0 - 100%, avg 48%" beside the
  // grade is what makes that legible rather than contradictory.
  const cloud = useMemo(() => spread((slot) => slot.cloud_cover),
                        [score.slots]);

  // The facts that are actually shown: observing hours only with a session,
  // temperature and cloud only with weather.
  const factCount = 6 + (session ? 1 : 0) + (temps ? 1 : 0) + (cloud ? 1 : 0);
  const { gridRef, column: weekColumn } = useWeekPlacement(factCount);

  return (
    <section className="panel score-panel" aria-labelledby="score-heading">
      {/* Visually redundant next to the dials, but it gives the panel a name
          in the document outline so a screen reader can jump to it. */}
      <h2 id="score-heading" className="visually-hidden">
        Tonight's conditions
      </h2>
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

        {/* No one-line verdict above the facts. It restated what the dials
            and the facts below already say ("Workable - the Moon is 73% and
            up...") in the largest type on the page, so it read as the
            headline while carrying nothing the numbers did not. The API
            still returns it, for the CLI. */}
        <div className="score-body">
          {/* The six facts you act on, in the space the dials used to leave empty. */}
          <div className="key-facts" ref={gridRef}>
            {session && (
              <div>
                <span>Observing</span>
                <strong>
                  {formatTime(session.start, tz)}–{formatTime(session.end, tz)}
                </strong>
                <em>
                  {formatHours(session.hours)}
                  {session.is_default ? "" : " · your hours"}
                </em>
              </div>
            )}
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
            {temps && (
              <div>
                <span>Temperature</span>
                <strong>
                  {formatTemp(temps.low)} – {formatTemp(temps.high)}
                </strong>
                <em>avg {formatTemp(temps.mean)}</em>
              </div>
            )}
            {cloud && (
              <div>
                <span>Cloud cover</span>
                <strong>
                  {cloud.low.toFixed(0)} – {cloud.high.toFixed(0)}%
                </strong>
                <em>avg {cloud.mean.toFixed(0)}%</em>
              </div>
            )}
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
            {/* Last, so it lands in whatever the facts leave of their final
                row; see useWeekPlacement. */}
            {week && week.length > 0 && (
              <WeekStrip
                nights={week}
                timeZone={tz}
                selected={night.date}
                onSelect={onSelectNight}
                expanded={open}
                style={{ gridColumn: weekColumn }}
              />
            )}
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
      {score.weather_available && score.weather_fallback_reason && (
        <p className="warning">
          From 7Timer alone ({score.weather_fallback_reason}): three days ahead,
          a reading every three hours, coarser cloud, and wind estimated without
          gusts
        </p>
      )}
      {score.weather_stale_since && (
        <p className="warning">
          Forecast from {formatTime(score.weather_stale_since, tz, true)}; it could
          not be refreshed ({score.weather_stale_reason})
        </p>
      )}
      {score.seeing_estimated && (
        <p className="warning">
          Past 72-hour horizon, seeing and transparency are estimated
        </p>
      )}
      <button className="link-button" onClick={() => setOpen(!open)}>
        {open ? "Less info" : "More info"}
      </button>

      {/* Hour by hour first: it is what people open this for, and it used
          to sit below a tall two-column block with a screen of scrolling in
          between. Under it, three columns of similar height -- the hours
          you plan to be out, the twilight times, and the factor breakdown --
          instead of a long list beside a short one and a gap. */}
      {open && (
        <div className="score-more">
          {/* Folded in rather than a panel of its own: it is the same night
              described hour by hour, and as a separate box it took a full
              screen width to say what the dials say in a glance. */}
          <div className="score-more-wide">
            <ConditionsStrip
              slots={score.slots}
              timeZone={tz}
              weatherAvailable={score.weather_available}
            />
          </div>

          {/* Their own row, so auto-fit can collapse unused tracks: in the
              same grid as the full-width strip above, a spanning item kept a
              fourth, empty column open and left a third of the row blank. */}
          <div className="score-more-cols">
          <div>
            {session && (
              <SessionEditor
                session={session}
                timeZone={tz}
                onChange={onSessionChange}
              />
            )}
            {score.dew_warning && (
              <p className="warning">
                <strong>Dew:</strong> the spread drops below{" "}
                {formatTempSpread(2)} — expect fogging.
              </p>
            )}
          </div>

          {/* Evening beside morning. Each twilight stage has an end and a
              beginning, and pairing them halves the list's height and says
              what a column of eight times left you to work out. */}
          <div>
            <h4>Twilight — {timeZoneAbbreviation(tz)}</h4>
            <table className="twilight-table">
              <thead>
                <tr>
                  <th scope="col"><span className="visually-hidden">Stage</span></th>
                  <th scope="col">Evening</th>
                  <th scope="col">Morning</th>
                </tr>
              </thead>
              <tbody>
                {([
                  ["Sun", night.sunset, night.sunrise],
                  ["Civil", night.civil_dusk, night.civil_dawn],
                  ["Nautical", night.nautical_dusk, night.nautical_dawn],
                  ["Astronomical", night.astronomical_dusk, night.astronomical_dawn],
                ] as [string, string | null, string | null][]).map(
                  ([stage, evening, morning]) => (
                    <tr key={stage}>
                      <th scope="row">{stage}</th>
                      <td>{formatTime(evening, tz)}</td>
                      <td>{formatTime(morning, tz)}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
            <p className="muted small twilight-total">
              {formatHours(night.astronomical_night_hours)} of astronomical night
            </p>
          </div>

          {/* One table, average beside best slot, rather than two lists.
              Both matter: only the peak was shown once, and on a night that
              clouds over halfway through it read `Cloud 1.00` next to an
              hourly row of 100% -- true of thirty minutes and of nothing
              else. The grade comes from the peak; the night looked like the
              average. */}
          {score.peak_factors_deep_sky && (
            <div>
              <h4>Deep-sky factors</h4>
              <div className="factor-table">
                <span />
                <span className="factor-col-head">Night avg</span>
                <span className="factor-col-head">Best hour</span>
                {(Object.keys(FACTOR_LABELS) as (keyof FactorsModel)[]).map((key) => (
                  <Fragment key={key}>
                    <span className="factor-name">{FACTOR_LABELS[key]}</span>
                    <FactorCell value={(score.mean_factors_deep_sky ??
                                        score.peak_factors_deep_sky!)[key]} />
                    <FactorCell value={score.peak_factors_deep_sky![key]} />
                  </Fragment>
                ))}
              </div>
            </div>
          )}
          </div>
        </div>
      )}
    </section>
  );
}
