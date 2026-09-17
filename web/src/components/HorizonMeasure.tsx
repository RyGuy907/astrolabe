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

import { useEffect, useState } from "react";
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
  const [chosen, setChosen] = useState<Record<number, string>>({});
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);

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
    // Retract the profile too. Clearing only the dropdowns would leave the
    // parent holding altitudes measured at the *previous* coordinates while
    // the form shows nothing measured -- the same trap the Bortle field fell
    // into when a site moved out of the atlas.
    onMeasured(null);

    Promise.all(
      DIRECTIONS.map((d) =>
        api.horizonMarks(lat, lon, d.azimuth, controller.signal)
          .then((response) => [d.azimuth, response] as const),
      ),
    )
      .then((pairs) => {
        if (cancelled) return;
        setMarks(Object.fromEntries(pairs.map(([az, r]) => [az, r.marks])));
        setComputedAt(pairs[0]?.[1].at ?? null);
      })
      .catch((error) => {
        if (cancelled || (error as Error)?.name === "AbortError") return;
        setFailed(true);
      })
      .finally(() => !cancelled && setLoading(false));

    return () => {
      cancelled = true;
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
    onMeasured(Object.keys(profile).length ? profile : null);
  }

  if (failed) {
    return (
      <p className="warning">
        Could not work out what is up right now — the coordinates may be
        incomplete, or the API unreachable. The horizon presets below still
        work.
      </p>
    );
  }

  return (
    <div className="horizon-measure">
      <p className="muted small">
        Standing at the site, pick the lowest constellation you can still make
        out in each direction. Anything below it is blocked.
      </p>

      {computedAt && (
        <p className="muted small">
          Positions are for{" "}
          <strong>
            {formatTime(computedAt,
                        Intl.DateTimeFormat().resolvedOptions().timeZone)}
          </strong>{" "}
          — your clock, right now. Reopen this if you have been standing here a
          while; the sky will have moved.
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
        <p className="muted small">
          Nothing low enough to use as a marker right now. Try again after
          dark, or use a preset below.
        </p>
      )}
    </div>
  );
}
