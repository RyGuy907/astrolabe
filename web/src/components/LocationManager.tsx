/**
 * Add and remove observing sites (HANDOFF item 1).
 *
 * `POST /api/locations` and `/api/geocode` have existed and been tested since
 * Phase 4; nothing in the UI called them, so a new site meant editing
 * `config/locations.yaml` or POSTing by hand. This is that missing surface.
 *
 * Three things here are deliberate rather than incidental:
 *
 * 1. **The geocode call is debounced and cancellable.** The target search once
 *    cost 3.9 s per keystroke by doing unbounded work on every input event;
 *    firing a network request per keystroke is the same mistake with a worse
 *    constant. Nothing is requested until a query has been still for
 *    DEBOUNCE_MS, and a superseded request is aborted so a slow early response
 *    cannot overwrite a fast later one.
 *
 * 2. **Bortle is a required decision, not an optional field.** A location with
 *    no Bortle falls back to `BORTLE_SQM[5]` in `engine/targets.py`, which sets
 *    the limiting magnitude and the surface-brightness contrast test — i.e. it
 *    changes which objects appear at all. That fallback is fine; it silently
 *    happening is not. The form makes the user choose a class or explicitly say
 *    they do not know, and "do not know" is stored as null so the site can be
 *    filled in properly later rather than inheriting a fabricated 5.
 *
 * 3. **Saving always sends coordinates, never `query`.** The server would
 *    happily geocode a name itself, but then the site could be saved at
 *    coordinates the user never saw. Search resolves to a candidate first; what
 *    gets written is what was on screen.
 */

import { useEffect, useRef, useState } from "react";
import {
  api,
  ApiError,
  type GeocodeCandidate,
  type LocationModel,
} from "../api";
import { SitePicker } from "./SitePicker";

/** Long enough that ordinary typing issues no requests, short enough that a
 *  pause between words still feels instant. */
const DEBOUNCE_MS = 400;

/** One or two characters match half the gazetteer; not worth a round trip. */
const MIN_QUERY_LENGTH = 3;

/** Display labels for the Bortle scale (Bortle 2001). Display only — the
 *  engine stores SQM, and the Bortle -> SQM mapping lives in
 *  `engine/locations.py:BORTLE_SQM`. */
const BORTLE_CLASSES: [number, string][] = [
  [1, "Excellent dark-sky site"],
  [2, "Typical truly dark site"],
  [3, "Rural sky"],
  [4, "Rural / suburban transition"],
  [5, "Suburban sky"],
  [6, "Bright suburban sky"],
  [7, "Suburban / urban transition"],
  [8, "City sky"],
  [9, "Inner-city sky"],
];

/**
 * Mirrors `engine/horizon.py:PRESETS`. Everything but "flat" is flagged
 * generic there, and the UI repeats that warning rather than hiding it.
 *
 * Each label names the obstruction the user can actually look at and judge,
 * and the degrees the preset assumes, so the choice is checkable rather than
 * a vibe. `directional` marks the ones where "which way is it blocked?" is a
 * meaningful question — the one thing about a horizon you can answer without
 * instruments.
 */
interface HorizonPreset {
  key: string;
  label: string;
  /** Whether it rises above the 25° altitude floor and so changes anything. */
  binds: boolean;
  directional: boolean;
}

const HORIZON_PRESETS: HorizonPreset[] = [
  { key: "flat", label: "Nothing in the way — sea horizon, playa, open plain",
    binds: false, directional: false },
  { key: "hilly", label: "Distant rolling hills (~12°)",
    binds: false, directional: false },
  { key: "trees", label: "Trees or buildings close on every side (~30°)",
    binds: true, directional: false },
  { key: "ridge", label: "One side blocked close in — hillside, canyon (~35°)",
    binds: true, directional: true },
  { key: "valley", label: "Two opposing sides blocked — valley floor (~35°)",
    binds: true, directional: true },
];

/** Compass points for the bearing control. Nobody knows the azimuth of their
 *  treeline in degrees, but everybody knows which way the sun sets. */
const BEARINGS: [number, string][] = [
  [0, "N — north"],
  [45, "NE — north-east"],
  [90, "E — east (sunrise)"],
  [135, "SE — south-east"],
  [180, "S — south"],
  [225, "SW — south-west"],
  [270, "W — west (sunset)"],
  [315, "NW — north-west"],
];

type SearchState = "idle" | "waiting" | "searching" | "done" | "failed";

interface Props {
  locations: LocationModel[];
  onClose: () => void;
  /** Called with the new key so the dashboard can select it. */
  onCreated: (key: string) => void;
  onDeleted: (key: string) => void;
}

/** Selector for things a keyboard can reach. `:not([disabled])` matters — a
 *  disabled Delete button on a config-owned site must not swallow a Tab. */
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), ' +
  'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * The behaviour `aria-modal="true"` promises but does not provide.
 *
 * Declaring a dialog modal tells assistive technology the rest of the page is
 * inert. Nothing enforces that for the keyboard, so without this the user
 * could Tab straight out of the dialog into content their screen reader has
 * been told does not exist, with no visible focus ring to follow. This adds
 * the four things that make the declaration true:
 *
 *   - focus moves into the dialog on open, and back to whatever opened it on
 *     close, so keyboard position is never lost;
 *   - Tab and Shift+Tab wrap inside the dialog;
 *   - Escape closes it, which is the first thing anyone tries;
 *   - the page behind stops scrolling, so the wheel does not silently move
 *     content the user cannot see.
 */
function useModalBehaviour(
  dialog: React.RefObject<HTMLElement>,
  onClose: () => void,
) {
  // Held in a ref so the effect can stay mount-only: re-running it would
  // steal focus back to the dialog on every parent render.
  const close = useRef(onClose);
  close.current = onClose;

  useEffect(() => {
    const node = dialog.current;
    if (!node) return;

    const previouslyFocused = document.activeElement as HTMLElement | null;
    node.focus();

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    function visibleFocusables(): HTMLElement[] {
      return Array.from(
        node!.querySelectorAll<HTMLElement>(FOCUSABLE),
      ).filter((el) => el.getClientRects().length > 0);
    }

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.stopPropagation();
        close.current();
        return;
      }
      if (event.key !== "Tab") return;

      const items = visibleFocusables();
      if (items.length === 0) {
        // Nothing to land on; keep focus on the dialog rather than letting it
        // escape to the page behind.
        event.preventDefault();
        node!.focus();
        return;
      }

      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;

      if (event.shiftKey && (active === first || active === node)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    }

    node.addEventListener("keydown", onKeyDown);
    return () => {
      node.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
      previouslyFocused?.focus?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}

/** Nearest compass point to a bearing, for display. */
function compassPoint(bearing: number): string {
  const names = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
  return names[Math.round((bearing % 360) / 45) % 8];
}

/** Mirrors the key normalisation in `api/main.py:create_location`, so the key
 *  shown in the form is the key that gets saved. */
function normaliseKey(raw: string): string {
  const cleaned = raw
    .toLowerCase()
    .replace(/\s+/g, "_")
    .replace(/[^a-z0-9_]/g, "_");
  return cleaned.replace(/_+/g, "_").replace(/^_|_$/g, "");
}

export function LocationManager({ locations, onClose, onCreated, onDeleted }: Props) {
  // "map" leads: it is the only mode that can reach a site with no name,
  // which is most dark-sky sites.
  const [entry, setEntry] = useState<"map" | "search" | "manual">("map");

  const [search, setSearch] = useState("");
  const [candidates, setCandidates] = useState<GeocodeCandidate[]>([]);
  const [searchState, setSearchState] = useState<SearchState>("idle");
  const [searchError, setSearchError] = useState<string | null>(null);
  const [chosen, setChosen] = useState<GeocodeCandidate | null>(null);

  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const [keyEdited, setKeyEdited] = useState(false);
  const [lat, setLat] = useState("");
  const [lon, setLon] = useState("");
  const [elevation, setElevation] = useState("");
  const [bortle, setBortle] = useState<string>("");
  const [horizon, setHorizon] = useState("flat");
  const [facing, setFacing] = useState(0);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);

  const dialog = useRef<HTMLDivElement>(null);
  useModalBehaviour(dialog, onClose);

  // --- debounced, cancellable geocode ---------------------------------------
  // The timer means a burst of keystrokes issues one request, not one each; the
  // AbortController means the request for a query the user has already moved
  // past is cancelled rather than left to land late and out of order.
  useEffect(() => {
    const query = search.trim();
    if (query.length < MIN_QUERY_LENGTH) {
      setCandidates([]);
      setSearchState("idle");
      setSearchError(null);
      return;
    }

    setSearchState("waiting");
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setSearchState("searching");
      api
        .geocode(query, controller.signal)
        .then((results) => {
          setCandidates(results);
          setSearchState("done");
          setSearchError(null);
        })
        .catch((e) => {
          if (e instanceof DOMException && e.name === "AbortError") return;
          if ((e as Error)?.name === "AbortError") return;
          setCandidates([]);
          setSearchState("failed");
          setSearchError(e instanceof ApiError ? e.message : String(e));
        });
    }, DEBOUNCE_MS);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [search]);

  function pickCandidate(candidate: GeocodeCandidate) {
    setChosen(candidate);
    setName(candidate.label);
    setKey(candidate.suggested_key);
    setKeyEdited(false);
    setLat(candidate.lat.toFixed(4));
    setLon(candidate.lon.toFixed(4));
    setElevation(candidate.elevation_m.toFixed(0));
    setError(null);
  }

  function updateName(value: string) {
    setName(value);
    if (!keyEdited) setKey(normaliseKey(value));
  }

  function resetForm() {
    setChosen(null);
    setName("");
    setKey("");
    setKeyEdited(false);
    setLat("");
    setLon("");
    setElevation("");
    setBortle("");
    setHorizon("flat");
    setFacing(0);
    setSearch("");
    setCandidates([]);
    setSearchState("idle");
  }

  const latValue = Number(lat);
  const lonValue = Number(lon);
  const latValid = lat.trim() !== "" && Number.isFinite(latValue) &&
    latValue >= -90 && latValue <= 90;
  const lonValid = lon.trim() !== "" && Number.isFinite(lonValue) &&
    lonValue >= -180 && lonValue <= 180;
  const elevationValid = elevation.trim() === "" || Number.isFinite(Number(elevation));
  const keyValid = normaliseKey(key) !== "";
  const keyTaken = locations.some((l) => l.key === normaliseKey(key));
  const bortleChosen = bortle !== "";

  const canSave =
    !busy && latValid && lonValid && elevationValid && keyValid && !keyTaken &&
    bortleChosen && name.trim() !== "";

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const created = await api.createLocation({
        key: normaliseKey(key),
        name: name.trim(),
        lat: latValue,
        lon: lonValue,
        elevation_m: elevation.trim() === "" ? undefined : Number(elevation),
        // "unknown" is stored as a real null, not as 5. See the module note.
        bortle: bortle === "unknown" ? null : Number(bortle),
        horizon,
        // Only meaningful for the directional presets; sending it for a
        // symmetric one would record a bearing the shape does not have.
        horizon_facing: horizonPreset?.directional ? facing : null,
      });
      resetForm();
      onCreated(created.key);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove(target: string) {
    setBusy(true);
    setDeleteError(null);
    try {
      await api.deleteLocation(target);
      setConfirming(null);
      onDeleted(target);
    } catch (e) {
      // Includes the 409 for config-owned sites. The button below is disabled
      // for those, but the server's message is shown verbatim if the rule and
      // the UI ever disagree — the server is the authority, not this file.
      setDeleteError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const emptyResult = searchState === "done" && candidates.length === 0;
  const horizonPreset = HORIZON_PRESETS.find((h) => h.key === horizon);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        ref={dialog}
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="sites-dialog-title"
        // Focusable so opening the dialog can land focus on it, which makes a
        // screen reader announce the dialog and its label before its contents.
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h2 id="sites-dialog-title">Observing sites</h2>
          <button className="modal-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <div className="modal-body">
          <h4>Add a site</h4>

          <div className="segmented">
            <button
              className={entry === "map" ? "on" : ""}
              onClick={() => setEntry("map")}
            >
              Pick on map
            </button>
            <button
              className={entry === "search" ? "on" : ""}
              onClick={() => setEntry("search")}
            >
              Find by name
            </button>
            <button
              className={entry === "manual" ? "on" : ""}
              onClick={() => setEntry("manual")}
            >
              Enter coordinates
            </button>
          </div>

          {entry === "map" && (
            <SitePicker
              lat={latValid ? latValue : null}
              lon={lonValid ? lonValue : null}
              onPick={(pickedLat, pickedLon) => {
                setLat(String(pickedLat));
                setLon(String(pickedLon));
                setChosen(null);
                setError(null);
                // A map point has no name of its own. Offer one the user can
                // overwrite, rather than blocking Save on an empty field.
                if (!name.trim()) {
                  const suggestion =
                    `Site ${pickedLat.toFixed(3)}, ${pickedLon.toFixed(3)}`;
                  setName(suggestion);
                  if (!keyEdited) setKey(normaliseKey(suggestion));
                }
              }}
            />
          )}

          {entry === "search" && (
            <div className="site-search">
              <input
                type="search"
                value={search}
                placeholder="Town, park or landmark — e.g. Lone Pine"
                onChange={(e) => setSearch(e.target.value)}
              />
              <p className="muted small">
                {searchState === "waiting" && "…"}
                {searchState === "searching" && "Searching…"}
                {searchState === "idle" && search.trim() !== "" &&
                  `Type at least ${MIN_QUERY_LENGTH} characters.`}
                {searchState === "done" && candidates.length > 0 &&
                  `${candidates.length} match${candidates.length === 1 ? "" : "es"}.`}
              </p>

              {searchState === "failed" && (
                <p className="warning">Geocode request failed: {searchError}</p>
              )}

              {emptyResult && (
                // The API returns 200 [] both for "no such place" and for an
                // unreachable geocoder — engine/geocode.py degrades to an empty
                // list rather than raising, and nothing in the response
                // distinguishes the two. Say so instead of guessing.
                <p className="warning">
                  No candidates for “{search.trim()}”. The geocoder also returns
                  an empty list when it is unreachable, so this may mean it is
                  offline rather than that the place is unknown — enter
                  coordinates manually instead.{" "}
                  <button className="link-button" onClick={() => setEntry("manual")}>
                    Enter coordinates
                  </button>
                </p>
              )}

              {candidates.length > 0 && (
                <ul className="candidates">
                  {candidates.map((candidate) => (
                    <li key={`${candidate.lat},${candidate.lon},${candidate.name}`}>
                      <button
                        className={`candidate ${
                          chosen && chosen.lat === candidate.lat &&
                          chosen.lon === candidate.lon ? "on" : ""
                        }`}
                        onClick={() => pickCandidate(candidate)}
                      >
                        <strong>{candidate.label}</strong>
                        <span className="muted small">
                          {candidate.lat.toFixed(4)}, {candidate.lon.toFixed(4)} ·{" "}
                          {candidate.elevation_m.toFixed(0)} m
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          <div className="site-form">
            <label>
              <span>Name</span>
              <input
                type="text"
                value={name}
                onChange={(e) => updateName(e.target.value)}
                placeholder="Site name"
              />
            </label>
            <label>
              <span>Key</span>
              <input
                type="text"
                value={key}
                onChange={(e) => {
                  setKey(e.target.value);
                  setKeyEdited(true);
                }}
                placeholder="site_key"
              />
            </label>
            <label>
              <span>Latitude</span>
              <input
                type="text"
                inputMode="decimal"
                value={lat}
                onChange={(e) => setLat(e.target.value)}
                placeholder="34.1361"
                className={lat.trim() !== "" && !latValid ? "invalid" : ""}
              />
            </label>
            <label>
              <span>Longitude</span>
              <input
                type="text"
                inputMode="decimal"
                value={lon}
                onChange={(e) => setLon(e.target.value)}
                placeholder="-118.7745"
                className={lon.trim() !== "" && !lonValid ? "invalid" : ""}
              />
            </label>
            <label>
              <span>Elevation (m)</span>
              <input
                type="text"
                inputMode="decimal"
                value={elevation}
                onChange={(e) => setElevation(e.target.value)}
                placeholder="0"
                className={!elevationValid ? "invalid" : ""}
              />
            </label>
            <label>
              <span>Bortle class</span>
              <select value={bortle} onChange={(e) => setBortle(e.target.value)}>
                <option value="">— choose —</option>
                {BORTLE_CLASSES.map(([value, label]) => (
                  <option key={value} value={String(value)}>
                    {value} — {label}
                  </option>
                ))}
                <option value="unknown">I don’t know</option>
              </select>
            </label>
            <label className="wide">
              <span>What blocks the view</span>
              <select value={horizon} onChange={(e) => setHorizon(e.target.value)}>
                {HORIZON_PRESETS.map((option) => (
                  <option key={option.key} value={option.key}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            {horizonPreset?.directional && (
              <label>
                <span>Blocked toward</span>
                <select
                  value={String(facing)}
                  onChange={(e) => setFacing(Number(e.target.value))}
                >
                  {BEARINGS.map(([value, label]) => (
                    <option key={value} value={String(value)}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>

          {horizon !== "flat" && (
            <p className="note">
              This is a GENERIC assumption from a typical height and distance —
              see <code>engine/horizon.py</code> for the geometry behind each
              one — not a survey of this site. It will be labelled “generic”
              wherever it appears. A measured azimuth→altitude map in{" "}
              <code>config/locations.yaml</code> is the only form that is not a
              guess.
            </p>
          )}

          {horizonPreset && !horizonPreset.binds && horizon !== "flat" && (
            <p className="warning">
              This profile tops out below the 25° altitude floor, so it will not
              change which targets are listed. That is the right answer —
              distant terrain does not matter if you are not observing that low
              — but do not expect the target list to differ from “nothing in the
              way”.
            </p>
          )}

          {bortle === "unknown" && (
            <p className="warning">
              With no Bortle class this site is scored as{" "}
              <strong>Bortle 5 (SQM 20.4)</strong>, a suburban sky. That sets the
              limiting magnitude and the surface-brightness contrast test, so it
              changes <em>which objects appear at all</em> — not just their
              ranking. The site will be labelled “assumed” everywhere until you
              set a real value.
            </p>
          )}

          {keyTaken && (
            <p className="warning">
              A site with the key <code>{normaliseKey(key)}</code> already exists.
            </p>
          )}
          {error && <p className="warning">{error}</p>}

          <div className="site-actions">
            <button className="secondary" onClick={save} disabled={!canSave}>
              {busy ? "Saving…" : "Save site"}
            </button>
            {!bortleChosen && (
              <span className="muted small">
                Choose a Bortle class — or “I don’t know” — before saving.
              </span>
            )}
          </div>

          <h4>Existing sites</h4>
          {deleteError && <p className="warning">{deleteError}</p>}
          <table className="targets">
            <thead>
              <tr>
                <th>Site</th>
                <th>Coordinates</th>
                <th>Sky</th>
                <th>Horizon</th>
                <th>Source</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {locations.map((site) => {
                const fromConfig = site.source === "config";
                return (
                  <tr key={site.key}>
                    <td>
                      <strong>{site.name}</strong>
                      <div className="target-notes">{site.key}</div>
                    </td>
                    <td className="nowrap">
                      {site.lat.toFixed(4)}, {site.lon.toFixed(4)}
                      <div className="target-notes">
                        {site.elevation_m.toFixed(0)} m · {site.timezone}
                      </div>
                    </td>
                    <td className="nowrap">
                      {site.bortle === null ? (
                        <span className="tag tag-warn" title="No Bortle class set">
                          Bortle 5 assumed
                        </span>
                      ) : (
                        <>
                          Bortle {site.bortle}
                          <div className="target-notes">
                            SQM {site.sqm?.toFixed(1)}
                          </div>
                        </>
                      )}
                    </td>
                    <td className="nowrap">
                      {site.horizon_name}
                      {site.horizon_facing !== null &&
                        ` → ${compassPoint(site.horizon_facing)}`}
                      <div className="target-notes">
                        {site.horizon_is_generic ? "generic preset" : "measured"}
                        {site.horizon_name !== "flat" && !site.horizon_binds &&
                          " · below the 25° floor, no effect"}
                      </div>
                    </td>
                    <td className="muted nowrap">{site.source}</td>
                    <td className="nowrap">
                      {fromConfig ? (
                        <span className="muted small">
                          <button className="chip" disabled title="Config-owned">
                            Delete
                          </button>
                          <div className="target-notes">
                            defined in config/locations.yaml — edit that file to
                            remove it
                          </div>
                        </span>
                      ) : confirming === site.key ? (
                        <>
                          <button
                            className="chip chip-on"
                            disabled={busy}
                            onClick={() => remove(site.key)}
                          >
                            Confirm
                          </button>{" "}
                          <button className="chip" onClick={() => setConfirming(null)}>
                            Cancel
                          </button>
                        </>
                      ) : (
                        <button
                          className="chip"
                          disabled={busy}
                          onClick={() => {
                            setDeleteError(null);
                            setConfirming(site.key);
                          }}
                        >
                          Delete
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
