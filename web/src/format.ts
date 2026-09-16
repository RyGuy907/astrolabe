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
