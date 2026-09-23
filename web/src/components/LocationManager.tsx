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
  type SkyBrightnessReading,
} from "../api";
import { SitePicker } from "./SitePicker";
import { HorizonMeasure } from "./HorizonMeasure";

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
 * Obstruction angle: how high the terrain reaches, in degrees above the
 * horizon.
 *
 * This used to be a list of described situations -- "trees or buildings close
 * on every side (~30°)" -- which asked the observer to match their site to a
 * picture in someone else's head. The angle is the thing the engine uses, and
 * it is now what the control offers: five-degree steps, no interpretation.
 * Anyone who wants an answer rather than an estimate uses the constellation
 * measurement above it.
 *
 * `engine/horizon.py` still flags a uniform ring `is_generic`, which is
 * correct -- an angle applied to every bearing is an assumption about a site,
 * not a survey of one.
 */
const OBSTRUCTION_ANGLES: number[] = [
  0, 5, 10, 15, 20, 25, 30, 35, 40, 45,
];

type SearchState = "idle" | "waiting" | "searching" | "done" | "failed";

interface Props {
  locations: LocationModel[];
  /** The site being edited, or null when adding a new one. */
  editingKey: string | null;
  onClose: () => void;
  /** Called with the saved key so the dashboard can select it. */
  onCreated: (key: string) => void;
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
      // The Edit / Add button that opened the dialog was in the site menu,
      // which closed as the dialog opened -- so it is gone, and focusing it
      // left focus on <body>. The menu's own trigger is where to come back to.
      const back = previouslyFocused?.isConnected
        ? previouslyFocused
        : document.querySelector<HTMLElement>(".location-trigger");
      back?.focus?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}

/** The name offered for a point picked off the map, which has none of its own. */
function suggestedName(lat: number, lon: number): string {
  return `Site ${lat.toFixed(3)}, ${lon.toFixed(3)}`;
}

/** Whether a name is one `suggestedName` made, rather than one someone typed. */
function isSuggestedName(name: string): boolean {
  return /^Site -?\d+\.\d{3}, -?\d+\.\d{3}$/.test(name.trim());
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

export function LocationManager({ locations, editingKey, onClose, onCreated }: Props) {
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
  //: Uniform obstruction angle in degrees, applied to every bearing. Null
  //: until chosen: this is the site's altitude floor now, so defaulting it to
  //: zero would quietly claim a clear horizon for somebody in a forest.
  const [horizon, setHorizon] = useState<number | null>(null);
  //: What the atlas says for the coordinates currently in the form. Null
  //: until asked; `in_coverage: false` means it has nothing here.
  const [atlas, setAtlas] = useState<SkyBrightnessReading | null>(null);
  //: True once the observer has touched the Bortle field themselves. From
  //: that point the atlas stops overwriting it -- their judgement about
  //: their own sky outranks a lookup, which is the same rule the engine
  //: applies to a value set in config.
  const [bortleEdited, setBortleEdited] = useState(false);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  //: An azimuth->altitude map built by naming visible constellations. When
  //: present it is sent instead of a preset, and the engine treats it as
  //: measured rather than generic.
  const [measuredHorizon, setMeasuredHorizon] =
    useState<Record<number, number> | null>(null);

  const dialog = useRef<HTMLDivElement>(null);
  useModalBehaviour(dialog, onClose);

  const editing = editingKey
    ? locations.find((l) => l.key === editingKey) ?? null
    : null;

  // Load the site being edited into the form, once. `POST /api/locations`
  // is an upsert -- `db/store.save_location` is INSERT OR REPLACE -- so
  // saving under the same key edits in place, and there is no second
  // endpoint to keep in step with this one.
  //
  // A measured horizon is loaded back in as it was saved, so an edit that
  // does not touch it -- a rename, a corrected elevation -- sends the same
  // profile back. It used to be dropped: the API reported only the profile's
  // name and peak, so saving any edit replaced a survey with a flat ring at
  // its highest angle, and the horizon had to be measured again. It is a set
  // of *angles*, not of constellations, so it holds on every date and only
  // ever needs measuring once.
  useEffect(() => {
    if (!editing) return;
    setEntry("manual");
    setName(editing.name);
    setKey(editing.key);
    setKeyEdited(true);
    setLat(editing.lat.toFixed(4));
    setLon(editing.lon.toFixed(4));
    setElevation(editing.elevation_m.toFixed(0));
    if (editing.sky_source === "observer" && editing.bortle !== null) {
      setBortle(String(editing.bortle));
      setBortleEdited(true);
    }
    setHorizon(Math.round(editing.horizon_max_deg / 5) * 5);
    if (editing.horizon_points && !editing.horizon_is_generic) {
      setMeasuredHorizon(Object.fromEntries(
        Object.entries(editing.horizon_points)
          .map(([az, alt]) => [Number(az), alt])));
    }
    // Advanced stays closed: editing opens on the coordinates tab, which
    // already shows the fields most edits are for.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editingKey]);

  const latValue = Number(lat);
  const lonValue = Number(lon);
  const latValid = lat.trim() !== "" && Number.isFinite(latValue) &&
    latValue >= -90 && latValue <= 90;
  const lonValid = lon.trim() !== "" && Number.isFinite(lonValue) &&
    lonValue >= -180 && lonValue <= 180;
  // A suggested name follows the coordinates it was made from. It used to be
  // set once, when the name was empty, so moving the pin afterwards -- or
  // editing a site's coordinates later -- left a site called "Site 42.232,
  // -107.450" sitting at 40.408, -111.792. A name the observer typed is
  // theirs and is never touched.
  useEffect(() => {
    if (!latValid || !lonValid || !isSuggestedName(name)) return;
    const suggestion = suggestedName(latValue, lonValue);
    if (suggestion === name) return;
    setName(suggestion);
    if (!keyEdited) setKey(normaliseKey(suggestion));
    // Only the coordinates should trigger this; the name is read, not watched.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [latValue, lonValue, latValid, lonValid]);

  const elevationValid = elevation.trim() === "" || Number.isFinite(Number(elevation));
  const keyValid = normaliseKey(key) !== "";
  // Editing a site keeps its own key, so that key is not "taken" by anyone
  // else -- without this exception the form refuses to save what it loaded.
  const keyTaken = locations.some(
    (l) => l.key === normaliseKey(key) && l.key !== editingKey);
  const bortleChosen = bortle !== "";

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

  // Ask the atlas whenever the coordinates settle, and fill the Bortle class
  // in. This is the whole point of having a raster: the observer should not
  // be recalling a class from memory when the answer is on disk. Debounced
  // and cancellable for the same reason the geocode call is -- these
  // coordinates change on every keystroke in the lat/lon fields.
  useEffect(() => {
    if (!latValid || !lonValid) {
      setAtlas(null);
      return;
    }
    // The last reading was for other coordinates: drop it now, and the class
    // it filled in, rather than keep both labelled "from atlas" while the new
    // lookup is pending -- or, if it fails, for good. An emptied class has to
    // be chosen before saving, so nothing stale is saved unseen.
    setAtlas(null);
    if (!bortleEdited) setBortle("");
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      api
        .skyBrightnessAt(latValue, lonValue, controller.signal)
        .then((reading) => {
          setAtlas(reading);
          if (bortleEdited) return;          // theirs now; leave it alone

          if (reading.bortle !== null) {
            setBortle(String(reading.bortle));
          } else {
            // Moving outside coverage has to clear the field, not leave the
            // last answer sitting there. Otherwise looking at Salt Lake City
            // and then at Los Angeles saves Los Angeles as Bortle 7 -- a
            // stale reading for somewhere else, with nothing on screen
            // saying so.
            setBortle("");
          }
        })
        .catch(() => {
          /* no atlas, offline, or superseded: the class stays for the
             observer to choose */
        });
    }, 250);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [latValid, lonValid, latValue, lonValue, bortleEdited]);

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
    setBortleEdited(false);
    setAtlas(null);
    setHorizon(null);
    setMeasuredHorizon(null);
    setSearch("");
    setCandidates([]);
    setSearchState("idle");
  }

  //: A measured profile counts: it is a horizon, and a better one.
  const horizonChosen = horizon !== null || measuredHorizon !== null;
  const canSave =
    !busy && latValid && lonValid && elevationValid && keyValid && !keyTaken &&
    bortleChosen && horizonChosen && name.trim() !== "";
  //: The atlas answered for these coordinates, so the Bortle field filled
  //: itself and the observer has not overridden it.
  const bortleFromAtlas =
    !bortleEdited && atlas?.bortle !== null && atlas?.bortle !== undefined &&
    bortle === String(atlas.bortle);

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
        // A measured profile outranks the angle control. `parse_horizon`
        // reads a JSON object as an explicit azimuth->altitude map and does
        // not flag it generic, which is the whole point of measuring one; a
        // bare number is a uniform ring and stays flagged.
        horizon: measuredHorizon
          ? JSON.stringify(measuredHorizon)
          : String(horizon ?? 0),
        horizon_facing: null,
      });
      resetForm();
      onCreated(created.key);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const emptyResult = searchState === "done" && candidates.length === 0;

  //: Latitude, longitude and elevation. On the "Enter coordinates" tab they
  //: are the whole point of the tab, so they sit in the main form; on the map
  //: and search tabs they are filled for you and live under Advanced, for
  //: when the pick was slightly off. Never both, so a field is never shown
  //: twice.
  const coordinateFields = (
    <>
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
    </>
  );

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
          <h2 id="sites-dialog-title">
            {editing ? `Edit ${editing.name}` : "Add a site"}
          </h2>
          <button className="modal-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <div className="modal-body">

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
              reading={atlas}
              readingLabel={BORTLE_CLASSES.find(([value]) => value === atlas?.bortle)?.[1] ?? null}
              onPick={(pickedLat, pickedLon) => {
                setLat(String(pickedLat));
                setLon(String(pickedLon));
                setChosen(null);
                setError(null);
                // A map point has no name of its own. Offer one the user can
                // overwrite, rather than blocking Save on an empty field.
                // One already suggested is moved along by the effect above.
                if (!name.trim()) {
                  const suggestion = suggestedName(pickedLat, pickedLon);
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

          {entry === "manual" && (
            <div className="site-form">{coordinateFields}</div>
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
          </div>

          <div className="site-form">
            <label className="wide">
              <span>
                Obstruction angle
                {measuredHorizon && (
                  <span className="field-note">
                    {" "}measured, overrides this ·{" "}
                    <button type="button" className="link-button"
                            onClick={() => setMeasuredHorizon(null)}>
                      use one angle instead
                    </button>
                  </span>
                )}
              </span>
              <select
                value={horizon === null ? "" : String(horizon)}
                onChange={(e) => setHorizon(
                  e.target.value === "" ? null : Number(e.target.value))}
                disabled={measuredHorizon !== null}
              >
                <option value="">— choose —</option>
                {OBSTRUCTION_ANGLES.map((deg) => (
                  <option key={deg} value={String(deg)}>{deg}°</option>
                ))}
              </select>
            </label>
          </div>

          {latValid && lonValid && (
            <details className="horizon-measure-block">
              <summary>Measure the horizon by constellation</summary>
              <HorizonMeasure
                lat={latValue}
                lon={lonValue}
                onMeasured={setMeasuredHorizon}
              />
            </details>
          )}

          {/* Name is the only field that needs a person. The coordinates come
              from the map, the Bortle class from the atlas, and the key is
              derived -- so the rest is here for when it is wrong, not as a
              form to fill in. */}
          <button
            className="link-button advanced-toggle"
            onClick={() => setShowAdvanced(!showAdvanced)}
            aria-expanded={showAdvanced}
          >
            <span className="caret">{showAdvanced ? "▾" : "▸"}</span>
            Advanced settings
          </button>

          {showAdvanced && (
          <>
          <div className="site-form">
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
            {entry !== "manual" && coordinateFields}
            <label>
              <span>
                Bortle class
                {bortleFromAtlas && (
                  <span className="field-note">
                    {" "}from atlas: {atlas?.bortle_decimal?.toFixed(1)},
                    SQM {atlas?.sqm?.toFixed(2)}
                    {/* Which map answered, so a number read off 2014 data
                        and one read off last year's are not mistaken for
                        each other. */}
                    {atlas?.source && <>, {atlas.source}</>}
                  </span>
                )}
              </span>
              <select
                value={bortle}
                onChange={(e) => {
                  setBortle(e.target.value);
                  // From here the atlas stops overwriting it: the observer's
                  // own judgement about their own sky outranks a lookup.
                  setBortleEdited(true);
                }}
              >
                <option value="">— choose —</option>
                {BORTLE_CLASSES.map(([value, label]) => (
                  <option key={value} value={String(value)}>
                    {value} — {label}
                  </option>
                ))}
                <option value="unknown">I don’t know</option>
              </select>
            </label>
          </div>
          </>
          )}

          {keyTaken && (
            <p className="warning">
              A site with the key <code>{normaliseKey(key)}</code> already exists.
            </p>
          )}
          {error && <p className="warning">{error}</p>}

          <div className="site-actions">
            <button className="secondary" onClick={save} disabled={!canSave}>
              {busy ? "Saving…" : editing ? "Save changes" : "Save site"}
            </button>
            {/* Only worth saying where the atlas cannot answer -- inside its
                coverage the field fills itself and there is nothing to
                prompt about. */}
            {!bortleChosen && atlas?.in_coverage === false && (
              <span className="muted small">
                Outside the atlas — set the Bortle class yourself.
              </span>
            )}
          </div>

        </div>
      </div>
    </div>
  );
}
