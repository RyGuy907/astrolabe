/**
 * Measure a horizon by naming what you can see, not by estimating an angle.
 *
 * Asking "how high is that ridge?" gets a guess. Asking "what is the lowest
 * constellation you can make out due north?" gets an answer, and the altitude
 * follows from the sky — which the engine already knows exactly. Whatever sits
 * below the lowest thing you can see is blocked, so its altitude is how high
 * the obstruction reaches in that direction.
 *
 * That matters because it changes the *kind* of value produced.
 * `engine/horizon.py` flags every built-in preset `is_generic`, and the UI
 * repeats that warning everywhere, because a preset is an assumption about
 * terrain rather than a survey of anywhere. An explicit azimuth→altitude map
 * is treated as measured and carries no such flag. This is a way to produce
 * one without a clinometer.
 *
 * Four directions rather than eight: the profile interpolates between the
 * points it has, and eight questions is a chore where four is a minute's work
 * standing outside. "Clear to the horizon" is a real answer and is offered as
 * one — plenty of sites genuinely have an open aspect somewhere.
 */

import { useEffect, useRef, useState } from "react";
import { api, type SkyMark } from "../api";
import { formatTime } from "../format";

/** The bearings asked about. North first because it is the one people can
 *  point to without thinking. */
const DIRECTIONS: { azimuth: number; label: string }[] = [
  { azimuth: 0, label: "North" },
  { azimuth: 90, label: "East" },
  { azimuth: 180, label: "South" },
  { azimuth: 270, label: "West" },
];

/** How long the coordinates must sit still before asking what is up. */
const SETTLE_MS = 350;

/** Sentinel for "nothing is in the way at all". */
const CLEAR = "clear";

interface Props {
  lat: number;
  lon: number;
  /** Called with an azimuth→altitude map, or null when nothing is measured. */
  onMeasured: (profile: Record<number, number> | null) => void;
}

export function HorizonMeasure({ lat, lon, onMeasured }: Props) {
  const [marks, setMarks] = useState<Record<number, SkyMark[]>>({});
  //: The instant the altitudes were computed for, straight from the API.
  //: Shown because the answers are only true for that moment -- the sky
  //: turns, and a list left open for an hour is quietly wrong.
  const [computedAt, setComputedAt] = useState<string | null>(null);
  //: The site's zone, from the server: "Positions set for" is a time at the
  //: site, which may not be where this browser is.
  const [siteZone, setSiteZone] = useState<string | null>(null);
  const [chosen, setChosen] = useState<Record<number, string>>({});
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  //: Whether the profile the parent holds came from this component. Only
  //: then is it this component's to retract. The parent may be holding a
  //: horizon measured on an earlier night and loaded with the site -- and
  //: since this mounts as soon as the form opens, retracting
  //: unconditionally wiped that saved survey before anyone touched it.
  const produced = useRef(false);

  // One request per direction, for the coordinates currently in the form.
  // Which constellations sit low in a direction depends on where and when, so
  // this cannot be precomputed or cached across sites.
  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    setLoading(true);
    setFailed(false);
    setMarks({});
    setChosen({});
    setComputedAt(null);
    // Retract a profile measured here at the *previous* coordinates, or the
    // parent would hold it while the form shows nothing measured -- the same
    // trap the Bortle field fell into when a site moved out of the atlas.
    if (produced.current) {
      onMeasured(null);
      produced.current = false;
    }

    // Waits for the coordinates to settle. Typing a latitude changes it on
    // every keystroke, and each change fired four requests -- the thing this
    // project has a standing rule against.
    const timer = window.setTimeout(() => Promise.all(
      DIRECTIONS.map((d) =>
        api.horizonMarks(lat, lon, d.azimuth, controller.signal)
          .then((response) => [d.azimuth, response] as const),
      ),
    )
      .then((pairs) => {
        if (cancelled) return;
        setMarks(Object.fromEntries(pairs.map(([az, r]) => [az, r.marks])));
        setComputedAt(pairs[0]?.[1].at ?? null);
        setSiteZone(pairs[0]?.[1].timezone ?? null);
      })
      .catch((error) => {
        if (cancelled || (error as Error)?.name === "AbortError") return;
        setFailed(true);
      })
      .finally(() => !cancelled && setLoading(false)), SETTLE_MS);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      controller.abort();
    };
    // `onMeasured` is deliberately absent: it is a setState from the parent,
    // stable across renders, and listing it would only risk a refetch loop if
    // a caller ever passed an inline closure.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lat, lon]);

  function choose(azimuth: number, value: string) {
    const next = { ...chosen, [azimuth]: value };
    setChosen(next);

    // Build the profile from every direction answered so far. Unanswered ones
    // are left out rather than assumed flat: interpolating between what was
    // actually measured is honest, inventing a zero is not.
    const profile: Record<number, number> = {};
    for (const [azimuthKey, choice] of Object.entries(next)) {
      if (!choice) continue;
      const azimuthNumber = Number(azimuthKey);
      if (choice === CLEAR) {
        profile[azimuthNumber] = 0;
        continue;
      }
      const mark = (marks[azimuthNumber] ?? [])
        .find((m) => m.abbreviation === choice);
      if (mark) profile[azimuthNumber] = Math.round(mark.altitude_deg * 10) / 10;
    }
    const any = Object.keys(profile).length > 0;
    produced.current = any;
    onMeasured(any ? profile : null);
  }

  if (failed) {
    return (
      <p className="warning">Could not work out what is up right now.</p>
    );
  }

  return (
    <div className="horizon-measure">
      {computedAt && (
        <p className="muted small">
          Positions set for{" "}
          <strong>
            {formatTime(computedAt,
                        siteZone ?? Intl.DateTimeFormat().resolvedOptions().timeZone)}
          </strong>
        </p>
      )}

      {loading && <p className="muted small">Working out what is up…</p>}

      {!loading && DIRECTIONS.map((direction) => {
        const options = marks[direction.azimuth] ?? [];
        return (
          <label key={direction.azimuth} className="horizon-direction">
            <span>{direction.label}</span>
            <select
              value={chosen[direction.azimuth] ?? ""}
              onChange={(e) => choose(direction.azimuth, e.target.value)}
            >
              <option value="">— not measured —</option>
              <option value={CLEAR}>Clear to the horizon</option>
              {options.map((mark) => (
                <option key={mark.abbreviation} value={mark.abbreviation}>
                  {mark.name} ({mark.altitude_deg.toFixed(0)}°)
                </option>
              ))}
            </select>
          </label>
        );
      })}

      {!loading && DIRECTIONS.every((d) => (marks[d.azimuth] ?? []).length === 0) && (
        <p className="muted small">Nothing low enough to use as a marker right now.</p>
      )}
    </div>
  );
}
