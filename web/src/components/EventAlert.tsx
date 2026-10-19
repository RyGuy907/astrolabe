/**
 * Floating alert for anything happening in the next seven days.
 *
 * These are the things you can miss by not looking: a shower peaks on one
 * night, an eclipse happens once. Everything else on this dashboard is about
 * tonight and will still be there tomorrow, so this is the one thing that
 * earns a place above the fold -- as a small card pinned to the top right,
 * not a banner that pushes the night's score down the page.
 *
 * Renders nothing when there is nothing imminent — an always-present empty
 * banner would train you to ignore it.
 */

import { useState } from "react";
import type { EventsResponse } from "../api";
import { titleCase } from "../format";

const ALERT_DAYS = 7;

/** Events dismissed in this tab, by identity. sessionStorage, not state: the
 *  alert unmounts while each new night loads, and state kept in it was lost
 *  every time the date moved -- so stepping through the week brought back an
 *  alert that had just been closed. Not localStorage, deliberately: kept for
 *  good, a dismissal could hide a once-a-year eclipse. */
const DISMISSED_KEY = "astro:alerts-dismissed";

function readDismissed(): Set<string> {
  try {
    const raw = window.sessionStorage.getItem(DISMISSED_KEY);
    const keys = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(keys) ? keys.filter((k) => typeof k === "string") : []);
  } catch {
    return new Set();
  }
}

function writeDismissed(keys: Set<string>): void {
  try {
    window.sessionStorage.setItem(DISMISSED_KEY, JSON.stringify([...keys]));
  } catch {
    /* a dismissal we could not save is not worth breaking the page over */
  }
}

interface Item {
  key: string;
  date: string;
  text: string;
  urgent: boolean;
}

function daysUntil(iso: string, from: string): number {
  const target = new Date(`${iso.slice(0, 10)}T12:00:00Z`).getTime();
  const start = new Date(`${from.slice(0, 10)}T12:00:00Z`).getTime();
  return Math.round((target - start) / 86_400_000);
}

/** "Mon 5 Oct": everything here is within the week, so the year is noise
 *  and the weekday is the useful part. Same parsing rule as `formatDate`. */
function shortDate(iso: string, timeZone: string): string {
  const date = iso.length === 10 ? new Date(`${iso}T12:00:00Z`) : new Date(iso);
  return new Intl.DateTimeFormat("en-GB", {
    timeZone,
    weekday: "short",
    month: "short",
    day: "numeric",
  }).format(date);
}

function when(days: number): string {
  if (days <= 0) return "tonight";
  if (days === 1) return "tomorrow";
  return `in ${days} days`;
}

export function EventAlert({ events, fromDate, timeZone, onOpenEvents }: {
  events: EventsResponse | null;
  fromDate: string;
  timeZone: string;
  onOpenEvents: () => void;
}) {
  // Dismissal is by event, not by the alert's wording: the wording says
  // "in 3 days" on one night and "tomorrow" on the next, and keying on it
  // brought a closed alert back on every step of the date. An event not seen
  // before still shows.
  const [dismissed, setDismissed] = useState<Set<string>>(readDismissed);

  // Built before the early return below: hooks must run on every render, so
  // the "no events yet" case falls out of empty loops rather than a guard.
  const items: Item[] = [];

  for (const shower of events?.showers ?? []) {
    const days = daysUntil(shower.peak_date, fromDate);
    if (days < 0 || days > ALERT_DAYS) continue;
    items.push({
      key: `shower-${shower.code}-${shower.peak_date}`,
      date: shower.peak_date,
      urgent: days <= 1,
      text:
        `${shower.name} peaks ${when(days)} — about ` +
        `${Math.round(shower.estimated_rate_per_hour)} meteors/h expected`,
    });
  }

  for (const eclipse of events?.lunar_eclipses ?? []) {
    const days = daysUntil(eclipse.time, fromDate);
    if (days < 0 || days > ALERT_DAYS) continue;
    items.push({
      key: `eclipse-${eclipse.time}`,
      date: eclipse.time,
      urgent: true,
      text:
        `${eclipse.kind} lunar eclipse ${when(days)}` +
        (eclipse.visible_from_location
          ? " — visible from here"
          : " — not visible from here (Moon below the horizon)"),
    });
  }

  for (const conjunction of events?.conjunctions ?? []) {
    const days = daysUntil(conjunction.time, fromDate);
    if (days < 0 || days > ALERT_DAYS) continue;
    // Only the genuinely eye-catching ones; a 4-degree pairing is not news.
    if (conjunction.separation_deg > 2.0) continue;
    items.push({
      key: `conj-${conjunction.time}-${conjunction.body_a}`,
      date: conjunction.time,
      urgent: days <= 1,
      text:
        `${titleCase(conjunction.body_a)} and ${titleCase(conjunction.body_b)} ` +
        `${conjunction.separation_deg.toFixed(1)}° apart ${when(days)}`,
    });
  }

  items.sort((a, b) => a.date.localeCompare(b.date));
  const shown = items.filter((item) => !dismissed.has(item.key));

  function dismiss() {
    const next = new Set(dismissed);
    for (const item of items) next.add(item.key);
    writeDismissed(next);
    setDismissed(next);
  }

  if (shown.length === 0) return null;

  return (
    <div className="event-alert" role="status">
      <div className="event-alert-head">
        <span className="event-alert-label">Next {ALERT_DAYS} days</span>
        <button
          className="event-alert-close"
          onClick={dismiss}
          title="Dismiss these events until this tab is closed"
          aria-label="Dismiss this alert"
        >
          ×
        </button>
      </div>
      <ul>
        {shown.map((item) => (
          <li key={item.key} className={item.urgent ? "urgent" : undefined}>
            <span className="event-alert-date">
              {shortDate(item.date, timeZone)}
            </span>
            {item.text}
          </li>
        ))}
      </ul>
      <button className="link-button" onClick={onOpenEvents}>
        All events
      </button>
    </div>
  );
}
