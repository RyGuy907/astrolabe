/**
 * The hours you plan to be outside, and the control to change them.
 *
 * Two <input type="time"> fields rather than a datetime picker: nobody thinks
 * about which calendar day 01:00 belongs to, and making them say so would be
 * a worse question than the one being asked. The date comes from the night
 * being viewed, and an end time earlier than the start is read as the small
 * hours of the following morning — which is what "nine till one" means.
 *
 * The times shown and typed are the *site's* local time, not the browser's.
 * Somebody planning a trip to a dark site two timezones away is asking about
 * the hours they will be standing there.
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

/** An ISO instant as "HH:MM" in the given zone, for an <input type="time">. */
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

    // The session's own evening, as the site reads it.
    const evening = zonedDateParts(new Date(session.start), timeZone);
    const startUtc = zonedTimeToUtc(evening.year, evening.month, evening.day,
                                    startClock.hours, startClock.minutes,
                                    timeZone);
    let endUtc = zonedTimeToUtc(evening.year, evening.month, evening.day,
                                endClock.hours, endClock.minutes, timeZone);
    // An end at or before the start means the small hours of the next
    // morning. "21:00 to 01:00" is the ordinary case, not an error.
    if (endUtc <= startUtc) {
      endUtc = zonedTimeToUtc(evening.year, evening.month, evening.day + 1,
                              endClock.hours, endClock.minutes, timeZone);
    }

    setError(null);
    onChange([startUtc.toISOString(), endUtc.toISOString()]);
  }

  return (
    <div className="session-editor">
      <h4>Observing hours</h4>
      <div className="session-fields">
        <label>
          <span>From</span>
          <input type="time" value={start}
                 onChange={(e) => setStart(e.target.value)} />
        </label>
        <label>
          <span>To</span>
          <input type="time" value={end}
                 onChange={(e) => setEnd(e.target.value)} />
        </label>
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
