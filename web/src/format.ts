/**
 * The display boundary: UTC in, local strings out.
 *
 * This is the browser-side counterpart of `engine/timeutil.to_local`. Every
 * UTC-to-local conversion in the UI goes through a function in this file, so
 * the boundary stays greppable — exactly as it is in the Python.
 *
 * Times are rendered in the *observing site's* timezone, not the browser's.
 * Planning a session at a site two zones away and being shown your laptop's
 * clock would be actively misleading.
 */

export function formatTime(
  isoUtc: string | null,
  timeZone: string,
  withDate = false,
): string {
  if (!isoUtc) return "—";
  const options: Intl.DateTimeFormatOptions = {
    timeZone,
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  };
  if (withDate) {
    options.month = "short";
    options.day = "numeric";
  }
  return new Intl.DateTimeFormat("en-GB", options).format(new Date(isoUtc));
}

export function formatDate(isoDate: string | null, timeZone?: string): string {
  if (!isoDate) return "—";
  // A bare YYYY-MM-DD is a calendar date, not an instant: parse it as UTC noon
  // so no timezone can shift it onto the adjacent day.
  const date = isoDate.length === 10 ? new Date(`${isoDate}T12:00:00Z`) : new Date(isoDate);
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: timeZone ?? "UTC",
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(date);
}

/** A night by its calendar date, weekday first: "Wed 23 Sept 2026". */
export function formatNight(isoDate: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "UTC",
    weekday: "short",
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(new Date(`${isoDate}T12:00:00Z`)).replace(",", "");
}

export function timeZoneAbbreviation(timeZone: string): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    timeZoneName: "short",
  }).formatToParts(new Date());
  return parts.find((p) => p.type === "timeZoneName")?.value ?? timeZone;
}

/** Today's date at the site, as YYYY-MM-DD. */
export function siteToday(timeZone: string): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

/**
 * Which night is "tonight" at this site (PLAN.md §3.1).
 *
 * Before local noon you are mid-session, so the night in progress is the one
 * that began yesterday. This mirrors `engine.timeutil.resolve_night_date`; the
 * two must agree or the UI and CLI would disagree about what "tonight" means.
 */
export function resolveNightDate(timeZone: string): string {
  const hour = Number(
    new Intl.DateTimeFormat("en-GB", {
      timeZone,
      hour: "2-digit",
      hour12: false,
    }).format(new Date()),
  );
  const today = siteToday(timeZone);
  if (hour >= 12) return today;

  const [year, month, day] = today.split("-").map(Number);
  const yesterday = new Date(Date.UTC(year, month - 1, day - 1));
  return yesterday.toISOString().slice(0, 10);
}

export function shiftDate(isoDate: string, days: number): string {
  const [year, month, day] = isoDate.split("-").map(Number);
  const shifted = new Date(Date.UTC(year, month - 1, day + days));
  return shifted.toISOString().slice(0, 10);
}

export function formatHours(hours: number): string {
  if (hours <= 0) return "0 h";
  const whole = Math.floor(hours);
  const minutes = Math.round((hours - whole) * 60);
  if (whole === 0) return `${minutes} min`;
  return minutes === 0 ? `${whole} h` : `${whole} h ${minutes} min`;
}

export function formatDegrees(value: number | null, digits = 0): string {
  return value === null ? "—" : `${value.toFixed(digits)}°`;
}

/** Celsius to Fahrenheit. The engine and the forecast APIs are metric
 *  throughout; this is the one place that converts, for the same reason
 *  `formatTime` is the one place that leaves UTC. */
export function toFahrenheit(celsius: number): number {
  return celsius * 9 / 5 + 32;
}

/** A temperature for display, in Fahrenheit, rounded to the degree. */
export function formatTemp(celsius: number | null): string {
  return celsius === null ? "—" : `${Math.round(toFahrenheit(celsius))}°F`;
}

/** A temperature *difference* in Fahrenheit degrees.
 *
 *  Not the same conversion: a 2 °C spread is 3.6 F degrees, not 35.6 °F.
 *  Running a spread through `formatTemp` is the classic way to turn a dew
 *  warning into nonsense. */
export function formatTempSpread(celsiusDegrees: number): string {
  return `${(celsiusDegrees * 9 / 5).toFixed(1)} F°`;
}

export function formatMagnitude(value: number | null): string {
  return value === null ? "—" : (value >= 0 ? "+" : "") + value.toFixed(2);
}

export function titleCase(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

/** Colour ramp for a 0-100 score. Deliberately not red/green only. */
export function scoreColor(score: number): string {
  if (score >= 80) return "var(--good)";
  if (score >= 60) return "var(--ok)";
  if (score >= 35) return "var(--poor)";
  return "var(--bad)";
}

export function moonPhaseName(illumination: number, waxing: boolean): string {
  if (illumination < 0.02) return "new";
  if (illumination > 0.98) return "full";
  const side = waxing ? "waxing" : "waning";
  if (illumination < 0.45) return `${side} crescent`;
  if (illumination <= 0.55) return waxing ? "first quarter" : "last quarter";
  return `${side} gibbous`;
}

// --- wall-clock times in a named zone ---
//
// Moved here from SessionEditor: local-time conversion lives in this file
// and nowhere else.

/** An ISO instant as "HH:MM" in the given zone, for a ClockField. */
export function toLocalTimeValue(iso: string, timeZone: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone, hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(new Date(iso));
}

/** What `instant` reads as in `timeZone`, minus what it reads as in UTC. */
export function zoneOffsetMs(instant: Date, timeZone: string): number {
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
export function zonedTimeToUtc(year: number, month: number, day: number,
                        hours: number, minutes: number,
                        timeZone: string): Date {
  const naive = Date.UTC(year, month - 1, day, hours, minutes);
  const firstPass = naive - zoneOffsetMs(new Date(naive), timeZone);
  return new Date(naive - zoneOffsetMs(new Date(firstPass), timeZone));
}

/** The calendar date `instant` falls on, as read at the site. */
export function zonedDateParts(instant: Date, timeZone: string) {
  const [year, month, day] = new Intl.DateTimeFormat("en-CA", {
    timeZone, year: "numeric", month: "2-digit", day: "2-digit",
  }).format(instant).split("-").map(Number);
  return { year, month, day };
}

/** The site's offset from UTC at an instant, in whole minutes (+330 for
 *  India, -360 for Denver in summer). Daylight saving included. */
export function zoneOffsetMinutes(ms: number, timeZone: string): number {
  return Math.round(zoneOffsetMs(new Date(ms), timeZone) / 60_000);
}
