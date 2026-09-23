/**
 * History: the targets you opened, night by night, with your notes.
 *
 * Replaces the observation log, which asked you to start a session and log
 * objects by hand -- and which almost nobody did. This needs nothing: every
 * target whose details or sky chart you open is filed under the night the
 * dashboard was on, and the notes are there for whatever you want to say
 * about it. It is all kept in this browser (`history.ts`); nothing is sent
 * anywhere.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { history, useHistory, type HistoryEntry } from "../history";
import { formatNight, formatTime } from "../format";
import { readJson, writeJson } from "../storage";

interface Props {
  /** Tonight at the current site, to mark its entries as tonight's. */
  tonight: string | null;
  /** Open an entry's object again, on the night now showing. */
  onOpen: (entry: HistoryEntry) => void;
  /** Whether that object is in the lists now showing, so it can be. */
  canOpen: (entry: HistoryEntry) => boolean;
}

/** A note, saved as it is typed -- a moment after the last keystroke, and
 *  at once when the field is left. The field grows with what is in it. */
function NoteField({ entry }: { entry: HistoryEntry }) {
  const [draft, setDraft] = useState(entry.notes);
  const saved = useRef(entry.notes);
  const field = useRef<HTMLTextAreaElement>(null);

  // Another tab, or a clear, changed it underneath: take the new value
  // unless something is being typed here.
  useEffect(() => {
    if (entry.notes !== saved.current && document.activeElement !== field.current) {
      saved.current = entry.notes;
      setDraft(entry.notes);
    }
  }, [entry.notes]);

  const save = (value: string) => {
    if (value === saved.current) return;
    saved.current = value;
    history.setNotes(entry.id, value);
  };

  useEffect(() => {
    const timer = window.setTimeout(() => save(draft), 500);
    return () => window.clearTimeout(timer);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft]);

  // Folding the panel away mid-sentence keeps the sentence.
  const latest = useRef(draft);
  latest.current = draft;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => () => save(latest.current), []);

  useEffect(() => {
    const el = field.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [draft]);

  return (
    <textarea
      ref={field}
      className="history-note"
      rows={1}
      value={draft}
      placeholder="Add a note…"
      aria-label={`Notes on ${entry.label}`}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => save(draft)}
    />
  );
}

const OPEN_KEY = "astro:history-open";

export function History({ tonight, onOpen, canOpen }: Props) {
  const { entries, saveFailed } = useHistory();
  const [open, setOpen] = useState<boolean>(() => readJson(OPEN_KEY, true));
  const [confirming, setConfirming] = useState(false);
  useEffect(() => { writeJson(OPEN_KEY, open); }, [open]);

  // Newest night first; within a night, the order they were first opened,
  // newest first -- so a note being written never jumps about.
  const nights = useMemo(() => {
    const byNight = new Map<string, HistoryEntry[]>();
    for (const entry of entries) {
      byNight.set(entry.night, [...(byNight.get(entry.night) ?? []), entry]);
    }
    return [...byNight.entries()]
      .sort(([a], [b]) => b.localeCompare(a))
      .map(([night, list]) => ({
        night,
        entries: list.sort((a, b) => b.firstOpened.localeCompare(a.firstOpened)),
      }));
  }, [entries]);

  // The site is only worth naming once there is more than one.
  const manySites = new Set(entries.map((e) => e.siteKey)).size > 1;

  return (
    <section className="panel history-panel" aria-labelledby="history-heading">
      <div className="history-head">
        <h2 id="history-heading" className="panel-heading">
          <button className="group-head" onClick={() => setOpen(!open)}
                  aria-expanded={open}>
            <span className="caret">{open ? "▾" : "▸"}</span>
            History
            <span className="muted">
              {entries.length === 0
                ? "nothing yet"
                : `${entries.length} ${entries.length === 1 ? "target" : "targets"} · ${
                    nights.length} ${nights.length === 1 ? "night" : "nights"}`}
            </span>
          </button>
        </h2>
        {open && entries.length > 0 && (confirming ? (
          <span className="history-confirm" role="group" aria-label="Clear all history">
            <span className="muted small">Delete all {entries.length}, notes too?</span>
            <button className="danger" onClick={() => { history.clear(); setConfirming(false); }}>
              Clear all
            </button>
            <button onClick={() => setConfirming(false)}>Cancel</button>
          </span>
        ) : (
          <button className="link-button history-clear" onClick={() => setConfirming(true)}>
            Clear all history
          </button>
        ))}
      </div>

      {open && (
        <>
          {saveFailed && (
            <p className="warning small">
              This browser isn't letting the page save, so the history will
              be lost when it is closed.
            </p>
          )}
          {entries.length === 0 && (
            <p className="muted small history-empty">
              Every target you open — its details or its sky chart — is listed
              here under the night you were looking at, with room for notes.
              It stays in this browser.
            </p>
          )}
          {nights.map(({ night, entries: list }) => (
            <section key={night} className="history-night"
                     aria-label={formatNight(night)}>
              <h3 className="history-night-head">
                {formatNight(night)}
                {night === tonight && <span className="tag">tonight</span>}
                <span className="muted small">
                  {list.length} {list.length === 1 ? "target" : "targets"}
                </span>
              </h3>
              <ul className="history-list">
                {list.map((entry) => (
                  <li key={entry.id} className="history-item">
                    <div className="history-item-line">
                      <span className="history-time muted small"
                            title={`First opened at ${formatTime(entry.firstOpened, entry.timeZone, true)}, ${entry.siteName}'s time`}>
                        {formatTime(entry.firstOpened, entry.timeZone)}
                      </span>
                      <span className="history-what">
                        {canOpen(entry) ? (
                          <button className="history-name" onClick={() => onOpen(entry)}
                                  title="Open it in the list">
                            {entry.label}
                          </button>
                        ) : (
                          <strong className="history-name-static">{entry.label}</strong>
                        )}
                        {(entry.detail || manySites) && (
                          <span className="muted small">
                            {[entry.detail, manySites ? entry.siteName : null]
                              .filter(Boolean).join(" · ")}
                          </span>
                        )}
                      </span>
                      <button className="icon-button history-delete"
                              onClick={() => history.remove(entry.id)}
                              title="Delete from history"
                              aria-label={`Delete ${entry.label} from the history`}>
                        ✕
                      </button>
                    </div>
                    <NoteField entry={entry} />
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </>
      )}
    </section>
  );
}
