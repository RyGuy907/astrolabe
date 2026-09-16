/**
 * Top-of-page alert for anything happening in the next seven days.
 *
 * These are the things you can miss by not looking: a shower peaks on one
 * night, an eclipse happens once. Everything else on this dashboard is about
 * tonight and will still be there tomorrow, so this is the one thing that
 * earns a position above the fold.
 *
 * Renders nothing when there is nothing imminent — an always-present empty
 * banner would train you to ignore it.
 */

import { useEffect, useState } from "react";
import type { EventsResponse } from "../api";
import { formatDate, titleCase } from "../format";

const ALERT_DAYS = 7;

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
  // Dismissal is keyed to *which* events are showing, so closing it does not
  // suppress a different night's alert. It is also session-only, deliberately:
  // persisting it could hide a once-a-year eclipse for good.
  const [dismissed, setDismissed] = useState<string>("");

  // Built before the early return below: hooks must run on every render, so
  // the "no events yet" case falls out of empty loops rather than a guard.
  const items: Item[] = [];

  for (const shower of events?.showers ?? []) {
    const days = daysUntil(shower.peak_date, fromDate);
    if (days < 0 || days > ALERT_DAYS) continue;
    items.push({
      key: `shower-${shower.code}`,
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
  // The text is part of the signature, not just the key, so an alert you
  // dismissed at "in 3 days" comes back when it becomes "tomorrow".
  const signature = items.map((i) => `${i.key}:${i.text}`).join("|");

  // Reset the dismissal whenever the set of imminent events changes.
  useEffect(() => {
    setDismissed((current) => (current === signature ? current : ""));
  }, [signature]);

  if (items.length === 0 || dismissed === signature) return null;

  return (
    <div className="event-alert" role="status">
      <div className="event-alert-label">
        <span className="event-alert-icon" aria-hidden="true">
          ★
        </span>
        Next {ALERT_DAYS} days
      </div>
      <ul>
        {items.map((item) => (
          <li key={item.key} className={item.urgent ? "urgent" : undefined}>
            <span className="event-alert-date">
              {formatDate(item.date, timeZone)}
            </span>
            {item.text}
          </li>
        ))}
      </ul>
      <button className="link-button" onClick={onOpenEvents}>
        All events
      </button>
      <button
        className="event-alert-close"
        onClick={() => setDismissed(signature)}
        title="Dismiss until these events change"
        aria-label="Dismiss this alert"
      >
        ×
      </button>
    </div>
  );
}
