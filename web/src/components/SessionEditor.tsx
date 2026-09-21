/**
 * The hours you plan to be outside, and the control to change them.
 *
 * Two clock fields rather than a datetime picker: nobody thinks
 * about which calendar day 01:00 belongs to, and making them say so would be
 * a worse question than the one being asked. The date comes from the night
 * being viewed, and an end time earlier than the start is read as the small
 * hours of the following morning — which is what "nine till one" means.
 *
 * The times shown and typed are the *site's* local time, not the browser's.
 * Somebody planning a trip to a dark site two timezones away is asking about
 * the hours they will be standing there.
 *
 * The clock fields are a pair of selects, not <input type="time">. A native
 * time input takes its 12- or 24-hour format from the operating system's
 * locale and no page can override it, so on a US-locale machine it showed
 * AM/PM while every other time in the app is 24-hour -- including the chart
 * axis the session line is drawn on.
 */

import { useEffect, useState } from "react";
import type { ObservingWindow } from "../api";
import { formatHours } from "../format";

interface Props {
  session: ObservingWindow;
  /** The site's timezone — the one the observer will be standing in. */
  timeZone: string;
  /** null restores the server default: astronomical dusk to 01:00 local. */
  onChange: (session: [string, string] | null) => void;
}

/** An ISO instant as "HH:MM" in the given zone, for a ClockField. */
function toLocalTimeValue(iso: string, timeZone: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone, hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(new Date(iso));
}

/** What `instant` reads as in `timeZone`, minus what it reads as in UTC. */
function zoneOffsetMs(instant: Date, timeZone: string): number {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", {
      timeZone, hour12: false,
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    })
      .formatToParts(instant)
      .map((part) => [part.type, part.value]),
  ) as Record<string, string>;
  const asIfUtc = Date.UTC(
    Number(parts.year), Number(parts.month) - 1, Number(parts.day),
    // en-US with hour12:false renders midnight as "24"; Date.UTC wants 0.
    Number(parts.hour) % 24, Number(parts.minute), Number(parts.second),
  );
  return asIfUtc - instant.getTime();
}

/**
 * A calendar date and wall-clock time in `timeZone`, as a UTC instant.
 *
 * There is no way to construct a date in a named zone directly, so this
 * subtracts the zone's offset and then corrects once, which resolves the case
 * where the first guess lands on the far side of a DST transition from the
 * answer.
 *
 * The obvious shortcut -- nudge a guess by the difference between what it
 * reads as and what was wanted -- is what this replaced, and it was wrong:
 * wrapping the correction into +/-12 h with `((d + 720) % 1440) - 720` assumes
 * Python's modulo. JavaScript's `%` keeps the sign of the dividend, so a
 * negative drift wrapped the wrong way and the session landed a day out.
 */
function zonedTimeToUtc(year: number, month: number, day: number,
                        hours: number, minutes: number,
                        timeZone: string): Date {
  const naive = Date.UTC(year, month - 1, day, hours, minutes);
  const firstPass = naive - zoneOffsetMs(new Date(naive), timeZone);
  return new Date(naive - zoneOffsetMs(new Date(firstPass), timeZone));
}

/** The calendar date `instant` falls on, as read at the site. */
function zonedDateParts(instant: Date, timeZone: string) {
  const [year, month, day] = new Intl.DateTimeFormat("en-CA", {
    timeZone, year: "numeric", month: "2-digit", day: "2-digit",
  }).format(instant).split("-").map(Number);
  return { year, month, day };
}

/** "HH:MM" to hours and minutes, or null if it is not a time. */
function parseClock(value: string): { hours: number; minutes: number } | null {
  const match = /^(\d{1,2}):(\d{2})$/.exec(value.trim());
  if (!match) return null;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (hours > 23 || minutes > 59) return null;
  return { hours, minutes };
}

const HOURS = Array.from({ length: 24 }, (_, h) => String(h).padStart(2, "0"));
const MINUTE_STEPS = Array.from({ length: 12 }, (_, i) => String(i * 5).padStart(2, "0"));

/**
 * A 24-hour "HH:MM" as two selects.
 *
 * Minutes step by five, which is finer than anyone plans a night -- but the
 * default session starts at astronomical dusk, which lands on whatever minute
 * the Sun decides (20:58, say), so the current minute is always offered too.
 * Otherwise the field would silently show 20:55 for a session that starts at
 * 20:58.
 */
function ClockField({ label, value, onChange }: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const [hour = "00", minute = "00"] = value.split(":");
  const minutes = MINUTE_STEPS.includes(minute)
    ? MINUTE_STEPS
    : [...MINUTE_STEPS, minute].sort();

  return (
    <label>
      <span>{label}</span>
      <span className="clock-field">
        <select value={hour} aria-label={`${label}, hour`}
                onChange={(e) => onChange(`${e.target.value}:${minute}`)}>
          {HOURS.map((h) => <option key={h} value={h}>{h}</option>)}
        </select>
        <span aria-hidden="true">:</span>
        <select value={minute} aria-label={`${label}, minute`}
                onChange={(e) => onChange(`${hour}:${e.target.value}`)}>
          {minutes.map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
      </span>
    </label>
  );
}

export function SessionEditor({ session, timeZone, onChange }: Props) {
  const [start, setStart] = useState(() => toLocalTimeValue(session.start, timeZone));
  const [end, setEnd] = useState(() => toLocalTimeValue(session.end, timeZone));
  const [error, setError] = useState<string | null>(null);

  // Follow the session when it changes underneath — a new night, a new site,
  // or the server clamping what was asked for to what the night allows.
  useEffect(() => {
    setStart(toLocalTimeValue(session.start, timeZone));
    setEnd(toLocalTimeValue(session.end, timeZone));
    setError(null);
  }, [session.start, session.end, timeZone]);

  function apply() {
    const startClock = parseClock(start);
    const endClock = parseClock(end);
    if (!startClock || !endClock) {
      setError("Use 24-hour times, like 21:30.");
      return;
    }

    // The night's evening, as the site reads it. Taken twelve hours before
    // the session start, not from the start's own date: a session that
    // already begins after midnight -- 04:00 -- sits on the *next* calendar
    // day, and reading the evening from it moved every later edit a further
    // day ahead. Anything in a night, 18:00 to 06:00, minus twelve hours
    // lands on that night's evening.
    const evening = zonedDateParts(
      new Date(new Date(session.start).getTime() - 12 * 3600_000), timeZone);

    // A clock time before noon is the small hours of the following morning.
    // Only the end used to roll over, and only when it came before the
    // start, so "04:00 to 06:00" was read as the morning *before* the night
    // -- outside it entirely -- and the server fell back to the default.
    const atClock = (clock: { hours: number; minutes: number }) =>
      zonedTimeToUtc(evening.year, evening.month,
                     evening.day + (clock.hours < 12 ? 1 : 0),
                     clock.hours, clock.minutes, timeZone);
    const startUtc = atClock(startClock);
    let endUtc = atClock(endClock);
    // "21:00 to 01:00" is the ordinary case and is handled above. An end
    // still at or before the start is an evening wrapping past a later
    // evening time -- rare, but it means the next day, not an error.
    if (endUtc <= startUtc) {
      endUtc = new Date(endUtc.getTime() + 24 * 3600_000);
    }

    setError(null);
    onChange([startUtc.toISOString(), endUtc.toISOString()]);
  }

  return (
    <div className="session-editor">
      <h4>Observing hours</h4>
      <div className="session-fields">
        <ClockField label="From" value={start} onChange={setStart} />
        <ClockField label="To" value={end} onChange={setEnd} />
        <button className="secondary" onClick={apply}>Apply</button>
        {!session.is_default && (
          <button className="link-button" onClick={() => onChange(null)}>
            reset
          </button>
        )}
      </div>

      {error && <p className="warning">{error}</p>}

      <p className="muted small">
        {formatHours(session.hours)} · {formatHours(session.dark_hours)} true dark
      </p>
    </div>
  );
}
