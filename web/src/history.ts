/**
 * History: every target whose sky chart or details were opened, by night.
 *
 * Kept in the browser, like the sites. An entry is one object on one
 * observing night -- opening M31 five times on the 23rd is one entry, opened
 * again on the 24th is a second -- and carries the observer's own notes.
 *
 * A small store rather than React state in the app: the notes are typed into
 * the History panel, and routing every keystroke through the app would
 * re-render the whole target list with it. Components read it with
 * `useHistory`; anything can write with `history.record` and friends.
 */

import { useSyncExternalStore } from "react";
import { readJson, writeJson } from "./storage";

export interface HistoryEntry {
  /** `${night}|${kind}:${objectId}` -- one object, one night. */
  id: string;
  /** The observing night the dashboard was on, YYYY-MM-DD. */
  night: string;
  kind: "target" | "body";
  /** Catalogue id (NGC0224) or body name (saturn). */
  objectId: string;
  label: string;
  /** "Galaxy · Andromeda", or null. */
  detail: string | null;
  siteKey: string;
  siteName: string;
  /** The site's zone, for showing when it was opened on the site's clock. */
  timeZone: string;
  /** ISO UTC. */
  firstOpened: string;
  lastOpened: string;
  notes: string;
}

export type HistoryInput = Omit<HistoryEntry, "id" | "firstOpened" | "lastOpened" | "notes">;

const KEY = "astro:history";
/** Enough for years of nights. Past it, the oldest entries without notes go
 *  first -- a note is the observer's own writing and outlives a bare visit. */
const LIMIT = 1500;

interface State {
  entries: HistoryEntry[];
  /** The last save failed (storage full or blocked); the panel says so. */
  saveFailed: boolean;
}

function load(): HistoryEntry[] {
  const saved = readJson<{ entries?: unknown } | null>(KEY, null);
  const entries = Array.isArray(saved?.entries) ? saved.entries : [];
  return entries.filter((e): e is HistoryEntry =>
    !!e && typeof e.id === "string" && typeof e.night === "string" &&
    typeof e.label === "string");
}

let state: State = { entries: load(), saveFailed: false };
const listeners = new Set<() => void>();

function commit(entries: HistoryEntry[]) {
  const saved = writeJson(KEY, { version: 1, entries });
  state = { entries, saveFailed: !saved };
  listeners.forEach((listener) => listener());
}

function trim(entries: HistoryEntry[]): HistoryEntry[] {
  if (entries.length <= LIMIT) return entries;
  const byAge = [...entries].sort((a, b) =>
    (a.notes ? 1 : 0) - (b.notes ? 1 : 0) || a.lastOpened.localeCompare(b.lastOpened));
  const drop = new Set(byAge.slice(0, entries.length - LIMIT).map((e) => e.id));
  return entries.filter((e) => !drop.has(e.id));
}

export const history = {
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  },
  getSnapshot: () => state,

  /** Note that an object was opened. Opening it again the same night only
   *  moves its last-opened time; its notes stay. */
  record(input: HistoryInput) {
    const id = `${input.night}|${input.kind}:${input.objectId}`;
    const now = new Date().toISOString();
    const existing = state.entries.find((e) => e.id === id);
    if (existing) {
      commit(state.entries.map((e) => e.id === id
        ? { ...e, ...input, id, lastOpened: now } : e));
    } else {
      commit(trim([{ ...input, id, firstOpened: now, lastOpened: now, notes: "" },
                   ...state.entries]));
    }
  },

  setNotes(id: string, notes: string) {
    commit(state.entries.map((e) => (e.id === id ? { ...e, notes } : e)));
  },

  remove(id: string) {
    commit(state.entries.filter((e) => e.id !== id));
  },

  clear() {
    commit([]);
  },
};

// Another tab's changes, so two open tabs never overwrite each other's notes.
if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (event.key !== KEY) return;
    state = { entries: load(), saveFailed: false };
    listeners.forEach((listener) => listener());
  });
}

export function useHistory(): State {
  return useSyncExternalStore(history.subscribe, history.getSnapshot);
}
