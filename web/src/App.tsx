/**
 * Dashboard shell.
 *
 * Layout, top to bottom: controls, an alert for anything in the next week,
 * the score-and-night panel, then a two-column row with the altitude chart
 * beside the Deep Sky/Solar System/Events panel — so adding something to the chart
 * and seeing it appear happens without scrolling. The history sits below.
 *
 * Chart contents are state, not a fixed list. On each new night the chart is
 * auto-filled with what is actually worth looking at — the Moon, every planet
 * with any chance of being seen, and the highest-scoring deep-sky objects —
 * and every series can then be removed or added back.
 *
 * Nothing about the observer is kept on the server. The sites live in this
 * browser (`sites.ts`) and every request carries the one it is about; the
 * history does too (`history.ts`).
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  api,
  ApiError,
  type AltitudeResponse,
  type EventsResponse,
  type LocationModel,
  type NightResponse,
  type PlanetsResponse,
  type TargetsResponse,
} from "./api";
import { AltitudeChart } from "./components/AltitudeChart";
import { DashboardSkeleton } from "./components/DashboardSkeleton";
import { DEFAULT_FOV, FinderChart, type FinderSubject } from "./components/FinderChart";
import { FinderSlot, FinderSlotProvider, useFinderHost } from "./components/FinderSlot";
import { SPLIT_DEFAULT, SPLIT_MAX, SPLIT_MIN, Splitter } from "./components/Splitter";
import { EventAlert } from "./components/EventAlert";
import { History } from "./components/History";
import { LocationManager } from "./components/LocationManager";
import { LocationPicker } from "./components/LocationPicker";
import { ScorePanel } from "./components/ScorePanel";
import { SkyPanel, type Reveal } from "./components/SkyPanel";
import { formatDate, resolveNightDate, shiftDate, titleCase } from "./format";
import type { HistoryEntry } from "./history";
import { loadSites, saveSites, siteParam, specFromModel, type SiteSpec } from "./sites";
import { suggestTarget } from "./suggest";
import { useMediaQuery } from "./useMediaQuery";

/** The nights the planetary ephemeris (DE440s) can plan, as
 *  `engine.ephem.supported_dates` reads them from it. Fixed data, so fixed
 *  here; the API refuses anything outside them with a 422 as well. */
const FIRST_NIGHT = "1849-12-27";
const LAST_NIGHT = "2150-01-19";

/** Where the dashboard row stacks into one column -- `.dashboard-row` in the
 *  stylesheet. Below it the sky chart goes in the list, above its row. */
const STACKED = "(max-width: 1180px)";

const UNREACHABLE = "Could not reach the planner's server. Check your connection, then reload.";

/** A curve on the altitude chart.
 *
 * `visible` is separate from membership on purpose. Hiding a curve keeps its
 * legend chip and its colour, so it can be brought back with one click rather
 * than hunted down in the Targets list again — and because every series is
 * fetched regardless, toggling costs no round-trip.
 */
interface ChartSeries {
  kind: "body" | "constellation";
  id: string;      // "moon" / "saturn", or an IAU abbreviation like "Cyg"
  label: string;   // what the chart legend will show
  visible: boolean;
}

/** A planet this low is not worth charting even if it technically rises. */
const PLANET_CHART_FLOOR_DEG = 8;

/**
 * Every planet goes on the chart by default. Uranus and Neptune used to be
 * left off as telescope-only objects; they are on now, but the chart draws
 * them thin and faded so they do not compete with the planets you can see
 * by eye.
 */
const CHARTED_PLANETS = ["mercury", "venus", "mars", "jupiter", "saturn",
                         "uranus", "neptune"];

export default function App() {
  //: The sites as this browser keeps them, and as the server resolved them
  //: -- timezone, sky source, horizon peak -- in the same order.
  const [sites, setSites] = useState<SiteSpec[]>([]);
  const [locations, setLocations] = useState<LocationModel[]>([]);
  //: False until the saved sites have been read and resolved.
  const [sitesReady, setSitesReady] = useState(false);
  const [locationKey, setLocationKey] = useState<string>("");
  const [date, setDate] = useState<string>("");
  const [isTonight, setIsTonight] = useState(true);

  const [night, setNight] = useState<NightResponse | null>(null);
  const [targets, setTargets] = useState<TargetsResponse | null>(null);
  const [planets, setPlanets] = useState<PlanetsResponse | null>(null);
  const [events, setEvents] = useState<EventsResponse | null>(null);
  const [eventsError, setEventsError] = useState<string | null>(null);
  const [altitude, setAltitude] = useState<AltitudeResponse | null>(null);

  const [series, setSeries] = useState<ChartSeries[]>([]);
  const [eventDays, setEventDays] = useState(90);
  //: The hours the observer plans to be outside, as [startIso, endIso], or
  //: null for the server's default of astronomical dusk to 01:00 local.
  //: Everything downstream -- scores, temperature and cloud summaries, which
  //: targets count as visible -- is computed over this, so it is state here
  //: rather than inside the panel that edits it.
  const [session, setSession] = useState<[string, string] | null>(null);

  // Persisted because the one thing worse than a bright screen at the eyepiece
  // is a bright screen at the eyepiece every time you reload. localStorage can
  // throw (private windows, blocked site data), so every access is guarded and
  // the app renders correctly when it fails.
  const [nightVision, setNightVision] = useState<boolean>(() => {
    try {
      return window.localStorage.getItem("astro:night-vision") === "on";
    } catch {
      return false;
    }
  });

  useEffect(() => {
    const root = document.documentElement;
    if (nightVision) root.setAttribute("data-night-vision", "on");
    else root.removeAttribute("data-night-vision");
    try {
      window.localStorage.setItem(
        "astro:night-vision", nightVision ? "on" : "off",
      );
    } catch {
      /* a preference we could not save is not worth breaking the page over */
    }
  }, [nightVision]);
  // "Visible tonight" lists only what passes the observability filters;
  // "All objects" drops the filtering entirely and badges each row instead.
  const [showAllTargets, setShowAllTargets] = useState(false);
  // Narrows either view to engine/showpieces.py's curated list.
  const [popularOnly, setPopularOnly] = useState(false);
  // Tracked per request, not as one flag. The three calls differ by an order
  // of magnitude -- /api/night answers in ~0.2 s, /api/planets in ~0.8 s warm,
  // /api/targets in ~5 s -- so a single flag meant the whole dashboard waited
  // on the slowest one.
  const [pending, setPending] = useState({
    night: true, targets: true, planets: true,
  });
  const [error, setError] = useState<string | null>(null);
  const [managingSites, setManagingSites] = useState(false);
  //: The key being edited, or null when the form is adding a new site.
  const [editingSite, setEditingSite] = useState<string | null>(null);

  // Which (date, location) the chart was last auto-filled for. Without this the
  // auto-fill would fight the user every time they removed a series.
  const autoFilledFor = useRef<string>("");
  // Which (date, site) is currently on screen, so a session change can be
  // told apart from a move to a different night.
  const loadedSubject = useRef<string>("");
  const skyPanelRef = useRef<HTMLDivElement>(null);
  const rowRef = useRef<HTMLDivElement>(null);

  const location = locations.find((l) => l.key === locationKey);
  const site = sites.find((s) => s.key === locationKey);
  //: The `site` parameter for every request about the open site. A string,
  //: so effects keyed on it rerun when the site is edited, not only swapped.
  const siteArg = useMemo(() => (site ? siteParam(site) : ""), [site]);

  // --- the sky chart ---
  // One chart, portalled into whichever slot wants it: the panel beside the
  // lists on a wide screen, or the list itself -- above the object's row --
  // where the layout is one column. See FinderSlot.
  const stacked = useMediaQuery(STACKED);
  const { host: finderHost, slots: finderSlots, inline: chartInline } = useFinderHost();

  // The finder chart open over the altitude chart, if any, and which of the
  // two tabs is showing. Kept apart so flipping back to the altitude chart
  // does not lose the finder.
  const [finder, setFinder] = useState<FinderSubject | null>(null);
  const [leftTab, setLeftTab] = useState<"altitude" | "finder">("altitude");
  const sameSubject = (a: FinderSubject | null, b: FinderSubject) =>
    !!a && a.kind === b.kind && a.id === b.id;
  function openFinder(subject: FinderSubject) {
    // Already this object's, behind the altitude tab: just show it, rather
    // than starting it over at the object's best time.
    if (!sameSubject(finder, subject)) setFinder(subject);
    setLeftTab("finder");
  }
  function closeFinder() {
    setFinder(null);
    setLeftTab("altitude");
  }
  /** Another row was opened: an open chart goes with it. */
  function followWithFinder(subject: FinderSubject) {
    setFinder((current) => (current && !sameSubject(current, subject) ? subject : current));
  }
  // An object clicked on the sky chart: the chart moves to it, and its row
  // opens in the lists. Inline, the chart is kept where it is on screen as it
  // moves to the new row.
  const [reveal, setReveal] = useState<Reveal | null>(null);
  const nextReveal = (subject: { kind: "target" | "body"; id: string },
                      keepTop?: number) =>
    setReveal((current) => ({ kind: subject.kind, id: subject.id,
                              seq: (current?.seq ?? 0) + 1, keepTop }));
  function selectOnChart(subject: FinderSubject) {
    const keepTop = chartInline && finderHost.isConnected
      ? finderHost.getBoundingClientRect().top : undefined;
    setFinder(subject);
    nextReveal(subject, keepTop);
  }
  //: Whether the chart is on screen, rather than open behind a tab or --
  //: inline -- above a row that is not in the list just now.
  const finderShown = finder !== null && (stacked ? chartInline : leftTab === "finder");

  // "Suggest a target": one of tonight's best, anywhere in the sky, opened
  // on the sky chart and in the list -- only showpieces when the list is
  // set to popular objects only. The choosing is in suggest.ts.
  const suggested = useRef<Set<string>>(new Set());
  const allTargets = useMemo(
    () => (targets ? Object.values(targets.groups).flat() : []), [targets]);
  const targetIndex = useMemo(
    () => new Map(allTargets.map((t) => [t.name, t])), [allTargets]);
  function suggest() {
    const now = new Date().toISOString();
    const session = targets?.session;
    const inNight = !!session && session.start <= now && now <= session.end;
    // The one open now is never the answer, even on a fresh round.
    const openNow = finder?.kind === "target" ? finder.id : null;
    const ask = () => suggestTarget({
      targets: allTargets, popularOnly,
      exclude: openNow ? new Set([...suggested.current, openNow]) : suggested.current,
      now: inNight ? now : undefined,
    });
    let pick = ask();
    if (!pick && suggested.current.size) {
      // Every good one offered already: start the round again.
      suggested.current = new Set();
      pick = ask();
    }
    if (!pick) return;
    suggested.current.add(pick.name);
    setFinder({
      kind: "target", id: pick.name, label: pick.display_name,
      ra: pick.ra_deg, dec: pick.dec_deg,
      // Now if the night is on, else at its best.
      at: inNight ? now : pick.peak_time ?? session?.start ?? now,
      // A fresh suggestion is shown at the chart's usual width, not at
      // whatever zoom the last one was left at.
      fov: DEFAULT_FOV,
    });
    nextReveal({ kind: "target", id: pick.name });
    setLeftTab("finder");
  }
  const canSuggest = !pending.targets && allTargets.some((t) =>
    t.visible_tonight && !t.too_faint && !t.visible_late && t.score !== null &&
    (!popularOnly || t.showpiece));
  const suggestButton = (
    <button className="suggest-button" onClick={suggest} disabled={!canSuggest}
            title={popularOnly
              ? "One of tonight's best well-known objects, opened on the sky chart"
              : "One of tonight's best targets, opened on the sky chart"}>
      Suggest a target
    </button>
  );

  // --- the history ---
  /** Whether a history entry's object is in the lists now, to be opened. */
  function canOpenFromHistory(entry: HistoryEntry): boolean {
    if (entry.kind === "target") return targetIndex.has(entry.objectId);
    return entry.objectId === "moon"
      ? !!planets?.moon
      : !!planets?.planets.some((p) => p.name === entry.objectId);
  }
  /** Open an entry's object again: its row, and the chart if one is open. */
  function openFromHistory(entry: HistoryEntry) {
    if (finder) {
      const target = targetIndex.get(entry.objectId);
      if (entry.kind === "target" && target) {
        setFinder({ kind: "target", id: target.name, label: target.display_name,
                    ra: target.ra_deg, dec: target.dec_deg,
                    at: target.peak_time ?? targets?.session?.start ?? new Date().toISOString() });
      } else if (entry.kind === "body") {
        const peak = entry.objectId === "moon" ? planets?.moon?.peak_time
          : planets?.planets.find((p) => p.name === entry.objectId)?.peak_time;
        setFinder({ kind: "body", id: entry.objectId,
                    label: entry.objectId === "moon" ? "Moon" : titleCase(entry.objectId),
                    at: peak ?? new Date().toISOString() });
      }
    }
    nextReveal({ kind: entry.kind, id: entry.objectId });
    // On a wide screen the list scrolls inside its own box, which may be
    // off the page from down here. A window scroll, not scrollIntoView: that
    // is thrown off course by the list's own box scrolling to the row at the
    // same moment.
    const panel = skyPanelRef.current;
    if (!stacked && panel) {
      window.scrollTo({ top: window.scrollY + panel.getBoundingClientRect().top - 16,
                        behavior: "smooth" });
    }
  }

  // A finder drawn for one night's best time means nothing on another night
  // or from another site -- nor do custom observing hours, which are absolute
  // times: carried to the next night they fell outside it, the server quietly
  // used the defaults, and the panel still showed them as custom.
  // The last pick goes too: the lists are rebuilt for the new night, and a
  // reveal still standing would open that object again there -- and file it
  // in the new night's history.
  useEffect(() => {
    closeFinder();
    setReveal(null);
    suggested.current = new Set();
    setSession(null);
  }, [date, siteArg]);

  // The chart's share of the chart/lists row, as the divider between them
  // leaves it. Remembered per browser, like night vision: a layout choice,
  // not data, so localStorage is the right place, and every access is
  // guarded because it can throw.
  const [split, setSplit] = useState<number>(() => {
    try {
      const saved = Number(window.localStorage.getItem("astro:split"));
      return saved >= SPLIT_MIN && saved <= SPLIT_MAX ? saved : SPLIT_DEFAULT;
    } catch {
      return SPLIT_DEFAULT;
    }
  });
  useEffect(() => {
    try {
      window.localStorage.setItem("astro:split", String(split));
    } catch {
      /* a layout preference is not worth breaking the page over */
    }
  }, [split]);
  const splitStyle = {
    "--split-a": `${split}fr`,
    "--split-b": `${1 - split}fr`,
  } as React.CSSProperties;

  // --- the sites, from this browser ---
  // On a browser's first visit there are none saved, and the server's own
  // configured sites (a self-hosted install's locations.local.yaml, or sites
  // an older version stored) are brought over. After that the browser's list
  // is the list. Each is resolved on load, so it learns its timezone and
  // picks up whatever the atlas now says.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const saved = loadSites();
      let specs: SiteSpec[];
      let selected: string | null = null;
      if (saved) {
        specs = saved.sites;
        selected = saved.selected;
      } else {
        try {
          specs = (await api.locations()).map(specFromModel);
          saveSites(specs, null);
        } catch {
          specs = [];
        }
      }
      const results = await Promise.allSettled(specs.map((spec) => api.resolveSite(spec)));
      if (cancelled) return;
      // A site the server refuses is left out rather than taking the others
      // with it; no answer at all means the server is not there.
      const unreachable = results.find((r) =>
        r.status === "rejected" && !(r.reason instanceof ApiError));
      if (unreachable) {
        setError(UNREACHABLE);
        // The per-request effect below never runs without a site, so clear
        // the pending flags here or the shell shows its skeleton forever
        // underneath the error.
        setPending({ night: false, targets: false, planets: false });
        setSitesReady(true);
        return;
      }
      const kept = specs.flatMap((spec, i) => {
        const result = results[i];
        if (result.status === "fulfilled") return [{ spec, model: result.value }];
        console.warn(`Saved site ${spec.key} was refused:`, result.reason);
        return [];
      });
      const models = kept.map((k) => k.model);
      setSites(kept.map((k) => k.spec));
      setLocations(models);
      setSitesReady(true);
      // An empty list is the ordinary first run, not a failure: nothing
      // ships preconfigured, because every answer this planner gives depends
      // on where the observer is standing. The dashboard stays empty and
      // asks for a site rather than inventing one.
      const first = models.find((m) => m.key === selected) ?? models[0];
      if (!first) {
        setPending({ night: false, targets: false, planets: false });
        return;
      }
      setLocationKey(first.key);
      setDate(resolveNightDate(first.timezone));
    })();
    return () => { cancelled = true; };
  }, []);

  // The site open now is remembered, so a reload comes back to it.
  useEffect(() => {
    if (sitesReady && locationKey) saveSites(sites, locationKey);
  }, [sites, locationKey, sitesReady]);

  // --- the night's data ---
  useEffect(() => {
    if (!siteArg || !date) return;
    let cancelled = false;
    setError(null);

    // Drop the previous night's numbers instead of leaving them on screen.
    // They used to sit there under the *new* site's name and date for as long
    // as the slowest call took, which reads as wrong data rather than as
    // loading -- the header would say "Sydney" above Lone Pine's sunset.
    //
    // Only when the site or the date actually moved, though. Changing the
    // observing hours refetches the same night for the same place, and
    // blanking it unmounted the panel the hours were changed in: the
    // disclosure snapped shut on every Apply. Stale-for-a-second numbers from
    // the same night are not the failure this guards against.
    const subject = `${date}|${siteArg}`;
    if (loadedSubject.current !== subject) {
      loadedSubject.current = subject;
      setNight(null);
      setTargets(null);
      setPlanets(null);
    }
    setPending({ night: true, targets: true, planets: true });

    const fail = (e: unknown) => {
      if (cancelled) return;
      setError(e instanceof ApiError ? `API error: ${e.message}` : UNREACHABLE);
    };
    const settle = (key: "night" | "targets" | "planets") => {
      if (!cancelled) setPending((current) => ({ ...current, [key]: false }));
    };

    api.night(date, siteArg, session ?? undefined)
      .then((data) => !cancelled && setNight(data))
      .catch(fail)
      .finally(() => settle("night"));

    api.planets(date, siteArg, false)
      .then((data) => !cancelled && setPlanets(data))
      .catch(fail)
      .finally(() => settle("planets"));

    // Always the unfiltered superset: the "visible tonight" view is derived
    // from it in SkyPanel, so switching modes costs no round-trip. Two
    // separate fetches would double a ~5 s server computation.
    api.targets(date, siteArg, 1000, 0, "constellation",
                "brightness", true, session ?? undefined)
      .then((data) => !cancelled && setTargets(data))
      .catch(fail)
      .finally(() => settle("targets"));

    return () => {
      cancelled = true;
    };
  }, [date, siteArg, session]);

  // --- events, on their own horizon ---
  useEffect(() => {
    if (!siteArg || !date) return;
    let cancelled = false;
    // Cleared first, so a new night or look-ahead shows as loading rather
    // than leaving the last list up under the new choice.
    setEvents(null);
    setEventsError(null);
    api
      .events(date, siteArg, eventDays)
      .then((data) => !cancelled && setEvents(data))
      .catch((e) => !cancelled &&
        setEventsError(e instanceof Error ? e.message : "Could not load events"));
    return () => {
      cancelled = true;
    };
  }, [date, siteArg, eventDays]);

  // --- auto-fill the chart: the bodies that are up while you are out ---
  // The Moon and every planet that clears the floor at some point during the
  // observing session -- not merely at some point in the night. A planet that
  // sets before dusk or rises after you pack up is left off entirely; it can
  // still be added from the Solar System tab. Decided from the candidates'
  // curves before anything is charted, so nothing is drawn and then removed.
  //
  // A new night or site starts the chart afresh. New hours re-decide only the
  // bodies, keeping any constellations that were added.
  useEffect(() => {
    const hours = night?.session;
    if (!planets || !hours || !location) return;
    const signature = `${date}|${siteArg}|${hours.start}|${hours.end}`;
    if (autoFilledFor.current === signature) return;
    const sameNight = autoFilledFor.current.startsWith(`${date}|${siteArg}|`);
    autoFilledFor.current = signature;

    const candidates = ["moon", ...planets.planets
      .filter((p) => CHARTED_PLANETS.includes(p.name) &&
                     p.peak_altitude_deg >= PLANET_CHART_FLOOR_DEG)
      .map((p) => p.name)];
    const start = new Date(hours.start).getTime();
    const end = new Date(hours.end).getTime();
    const floor = Math.max(PLANET_CHART_FLOOR_DEG, location.horizon_max_deg);

    let cancelled = false;
    let settled = false;
    api.altitude(date, siteArg, candidates.join(","), undefined, undefined, 0)
      .then((data) => {
        if (cancelled) return;
        settled = true;
        const up = data.series
          .filter((s) => s.points.some((p) => {
            const t = new Date(p.time).getTime();
            return t >= start && t <= end && p.altitude_deg >= floor;
          }))
          .map((s): ChartSeries =>
            ({ kind: "body", id: s.label, label: s.label, visible: true }));
        setSeries((current) => sameNight
          ? [...up, ...current.filter((s) => s.kind !== "body")]
          : up);
      })
      // The chart is a convenience; failing to fill it is not an error to
      // report over the rest of the dashboard. Allow a retry next render.
      .catch(() => { if (!cancelled) autoFilledFor.current = ""; });
    return () => {
      cancelled = true;
      // Torn down before the answer came -- a dependency changed without the
      // night doing so. Un-mark it, or the rerun would skip and leave the
      // chart empty.
      if (!settled) autoFilledFor.current = "";
    };
  }, [planets, night?.session, date, siteArg, location]);

  // --- fetch the curves whenever what is charted changes ---
  // Keyed on membership, not on the series objects: showing or hiding a
  // curve does not change what needs fetching, and depending on the whole
  // list refetched every curve on every toggle -- which, now that the app
  // sets default visibility itself, would have fetched everything twice.
  const members = useMemo(() => ({
    bodies: series.filter((s) => s.kind === "body").map((s) => s.id),
    constellations: series.filter((s) => s.kind === "constellation")
      .map((s) => s.id),
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [series.map((s) => `${s.kind}:${s.id}`).join("|")]);

  useEffect(() => {
    if (!siteArg || !date) return;
    let cancelled = false;

    const { bodies, constellations } = members;
    if (bodies.length === 0 && constellations.length === 0) {
      setAltitude(null);
      return;
    }

    api
      .altitude(date, siteArg, bodies.join(","), undefined,
                constellations.join(",") || undefined, 0)
      .then((data) => !cancelled && setAltitude(data))
      .catch(() => !cancelled && setAltitude(null));

    return () => {
      cancelled = true;
    };
  }, [members, date, siteArg]);

  /** Keep a site the form checked: a new one at the end of the list, an
   *  edited one where it was (under its new key, if that changed). Then open
   *  it. */
  function saveSite(model: LocationModel, replacing: string | null) {
    const spec = specFromModel(model);
    const at = replacing ? sites.findIndex((s) => s.key === replacing) : -1;
    const keep = (key: string) => key !== spec.key && key !== replacing;
    const nextSites = sites.filter((s) => keep(s.key));
    const nextModels = locations.filter((l) => keep(l.key));
    const index = at >= 0 ? Math.min(at, nextSites.length) : nextSites.length;
    nextSites.splice(index, 0, spec);
    nextModels.splice(index, 0, model);
    setSites(nextSites);
    setLocations(nextModels);
    saveSites(nextSites, spec.key);
    setLocationKey(spec.key);
    if (isTonight || !date) setDate(resolveNightDate(model.timezone));
  }

  /** Remove a site from the picker row, then re-point the dashboard. */
  function deleteLocation(key: string) {
    const nextSites = sites.filter((s) => s.key !== key);
    const nextModels = locations.filter((l) => l.key !== key);
    setSites(nextSites);
    setLocations(nextModels);
    if (key === locationKey) {
      const next = nextModels[0];
      setLocationKey(next ? next.key : "");
      if (next) setDate(resolveNightDate(next.timezone));
      else {
        setNight(null);
        setPending({ night: false, targets: false, planets: false });
      }
    }
    saveSites(nextSites, key === locationKey ? nextModels[0]?.key ?? null : locationKey);
  }

  function changeDate(next: string) {
    // Clearing the date field sends "", and a date outside the ephemeris
    // can't be planned; either would leave the arrows throwing on "".
    if (!next || next < FIRST_NIGHT || next > LAST_NIGHT) return;
    setDate(next);
    setIsTonight(location ? next === resolveNightDate(location.timezone) : false);
  }

  /** Add or drop a series, from the Deep Sky/Solar System tables. */
  function toggleSeries(id: string, label: string,
                        kind: "body" | "constellation") {
    setSeries((current) =>
      current.some((s) => s.id === id)
        ? current.filter((s) => s.id !== id)
        : [...current, { kind, id, label, visible: true }],
    );
  }

  /** Show/hide a curve, keeping its legend chip. Chart addresses by label. */
  function toggleVisibleByLabel(label: string) {
    setSeries((current) =>
      current.map((s) =>
        s.label === label ? { ...s, visible: !s.visible } : s,
      ),
    );
  }

  /** Show or hide several curves at once, keeping their legend chips. */
  function setVisibleByLabels(labels: string[], visible: boolean) {
    setSeries((current) =>
      current.map((s) => (labels.includes(s.label) ? { ...s, visible } : s)),
    );
  }

  /** Drop a curve entirely. */
  function removeByLabel(label: string) {
    setSeries((current) => current.filter((s) => s.label !== label));
  }

  /** Nothing in the toolbar means anything until a site exists. */
  const hasSites = locations.length > 0;

  const chartedIds = series.map((s) => s.id);
  const hiddenLabels = series.filter((s) => !s.visible).map((s) => s.label);
  const visibleCount = series.length - hiddenLabels.length;
  //: Where the layout is one column the chart is in the list, so the panel
  //: here is only ever the altitude chart.
  const panelFinder = finder !== null && !stacked;

  return (
    <div className="app">
      {/* First thing in the tab order. The page has ~115 focusable elements in
          one flat sequence -- most of them target rows -- so without this,
          reaching the history by keyboard means tabbing past all of
          them. */}
      <a className="skip-link" href="#main">
        Skip to tonight's plan
      </a>

      <header className="app-head">
        <div className="brand">
          <h1>Astrolabe</h1>
          {location && date && (
            <p className="muted">
              <strong>{formatDate(date, location.timezone)}</strong>
              {isTonight && <span className="tag">tonight</span>}
              {/* A site named for its coordinates would print them twice. */}
              {" "}{location.name}
              {!/^Site -?\d+\.\d+, -?\d+\.\d+$/.test(location.name) && (
                <> · {location.lat.toFixed(3)}, {location.lon.toFixed(3)}</>
              )}
              {" · "}{location.elevation_m.toFixed(0)} m
              {" · "}
              {location.sky_source === "assumed" ? (
                // Two words, not a lecture. The atlas answers for anywhere it
                // covers and the form will not save a site without a class,
                // so this is now a rare state rather than the common one --
                // but a site with no class really is assumed, and showing it
                // as though it were a known Bortle 5 would be a lie.
                <span className="tag">Bortle 5 (assumed)</span>
              ) : location.sky_source === "atlas" ? (
                <span
                  className="tag"
                  title={`Read from the configured light-pollution atlas: SQM ${location.sqm?.toFixed(2)}. You did not set a Bortle class for this site, so this is what target filtering uses.`}
                >
                  Bortle {(location.bortle_decimal ?? location.effective_bortle).toFixed(1)} (from atlas)
                  <span className="visually-hidden">
                    {" "}— read from the light-pollution atlas at SQM{" "}
                    {location.sqm?.toFixed(2)}, not a class you set
                  </span>
                </span>
              ) : (
                `Bortle ${location.bortle}`
              )}
            </p>
          )}
        </div>

        {/* Everything that steers the dashboard, in one group on the right.
            Adding, editing and deleting a site all live on the picker's own
            rows, so there is no second "Sites…" button and no separate
            "Tonight" button -- the date stepper already goes there. */}
        <div className="controls">
          <LocationPicker
            locations={locations}
            selected={locationKey}
            onSelect={(key) => {
              setLocationKey(key);
              const next = locations.find((l) => l.key === key);
              if (next && isTonight) setDate(resolveNightDate(next.timezone));
            }}
            onAdd={() => {
              setEditingSite(null);
              setManagingSites(true);
            }}
            onEdit={(key) => {
              setEditingSite(key);
              setManagingSites(true);
            }}
            onDelete={deleteLocation}
          />

          {hasSites && (
            <div className="date-row">
              <button onClick={() => changeDate(shiftDate(date, -1))}
                      aria-label="Previous night">
                ‹
              </button>
              <input type="date" value={date} min={FIRST_NIGHT} max={LAST_NIGHT}
                     aria-label="Night of"
                     onChange={(e) => changeDate(e.target.value)} />
              <button onClick={() => changeDate(shiftDate(date, 1))}
                      aria-label="Next night">
                ›
              </button>
            </div>
          )}

          {/* A switch, not a button: it is a state you leave on, and the
              track shows which state at a glance. role="switch" makes a
              screen reader announce it as on or off rather than pressed. */}
          <button
            className="night-vision-toggle"
            role="switch"
            onClick={() => setNightVision((on) => !on)}
            aria-checked={nightVision}
            aria-label="Night vision"
            title="Red palette that preserves dark adaptation at the eyepiece"
          >
            <span className="switch-track" aria-hidden="true">
              <span className="switch-thumb" />
            </span>
            {/* Hidden on a phone, where the switch sits by the title; the
                button's aria-label keeps its name either way. */}
            <span className="night-vision-label" aria-hidden="true">Night vision</span>
          </button>
        </div>

      </header>

      {managingSites && (
        <LocationManager
          locations={locations}
          editingKey={editingSite}
          onClose={() => {
            setManagingSites(false);
            setEditingSite(null);
          }}
          onSaved={(model) => {
            saveSite(model, editingSite);
            setManagingSites(false);
            setEditingSite(null);
          }}
        />
      )}

      {error && <div className="panel error" role="alert">{error}</div>}

      {/* First run. Nothing is preconfigured, so the dashboard asks where you
          are instead of assuming somewhere and quietly being wrong about the
          darkness, the horizon and half the target list. */}
      {!error && sitesReady && locations.length === 0 && (
        <section className="panel empty-state">
          <h2>Where are you observing?</h2>
          <button
            className="secondary"
            onClick={() => {
              setEditingSite(null);
              setManagingSites(true);
            }}
          >
            Add an observing site
          </button>
          <p className="muted small">
            Your sites and history are kept in this browser, not on a server.
          </p>
        </section>
      )}

      {/* The dashboard's outline while the night loads, not a one-line
          "Loading…" -- that collapsed the page and then snapped a full
          screen of dashboard in underneath it on every site or date change. */}
      {!error && !night && (!sitesReady || (pending.night && locations.length > 0)) && (
        <DashboardSkeleton splitStyle={splitStyle} />
      )}

      {night && location && (
        <main id="main" tabIndex={-1}>
          <EventAlert
            events={events}
            fromDate={date}
            timeZone={location.timezone}
            onOpenEvents={() =>
              skyPanelRef.current?.scrollIntoView({ behavior: "smooth", block: "center" })
            }
          />

          <ScorePanel
            score={night.score}
            window={night.window}
            session={night.session}
            onSessionChange={setSession}
          />

          <FinderSlotProvider value={finderSlots}>
          <div className="dashboard-row resizable" ref={rowRef} style={splitStyle}>
            <section className="panel chart-panel">
              {panelFinder ? (
                // A finder open: the head becomes a pair of tabs, and the
                // finder covers the altitude chart until it is closed.
                <div className="panel-head left-tabs" role="tablist">
                  <button role="tab" aria-selected={leftTab === "altitude"}
                          className={`left-tab ${leftTab === "altitude" ? "on" : ""}`}
                          onClick={() => setLeftTab("altitude")}>
                    Altitude
                  </button>
                  <span className={`left-tab ${leftTab === "finder" ? "on" : ""}`}
                        role="presentation">
                    <button role="tab" aria-selected={leftTab === "finder"}
                            onClick={() => setLeftTab("finder")}>
                      {finder.label}
                    </button>
                    <button className="left-tab-close" onClick={closeFinder}
                            aria-label="Close the sky chart">
                      ×
                    </button>
                  </span>
                  <span className="panel-head-end">{suggestButton}</span>
                </div>
              ) : (
                <div className="panel-head">
                  <h2>Altitude</h2>
                  <span className="muted small">
                    {visibleCount} of {series.length} shown
                  </span>
                  <span className="panel-head-end">{suggestButton}</span>
                </div>
              )}
              {panelFinder && leftTab === "finder" ? (
                <FinderSlot kind="panel" />
              ) : altitude ? (
                <AltitudeChart
                  data={altitude}
                  timeZone={location.timezone}
                  hidden={hiddenLabels}
                  onToggle={toggleVisibleByLabel}
                  onRemove={removeByLabel}
                  onSetVisible={setVisibleByLabels}
                  session={night.session}
                  obstructionDeg={location.horizon_max_deg}
                  obstructionVaries={!location.horizon_is_generic}
                />
              ) : (
                <p className="muted">
                  Nothing charted. Add constellations from Deep Sky or the
                  Moon and planets from Solar System, with the + beside each.
                </p>
              )}
            </section>

            <Splitter rowRef={rowRef} split={split} onChange={setSplit} />

            <div ref={skyPanelRef}>
              <SkyPanel
                targets={targets}
                planets={planets}
                events={events}
                eventsError={eventsError}
                timeZone={location.timezone}
                charted={chartedIds}
                onToggleChart={toggleSeries}
                eventDays={eventDays}
                onEventDaysChange={setEventDays}
                showAll={showAllTargets}
                onShowAllChange={setShowAllTargets}
                popularOnly={popularOnly}
                onPopularOnlyChange={setPopularOnly}
                targetsPending={pending.targets}
                finder={finder}
                finderShown={finderShown}
                chartInline={stacked}
                onOpenFinder={openFinder}
                onCloseFinder={closeFinder}
                onFocus={followWithFinder}
                reveal={reveal}
                night={date}
                siteKey={location.key}
                siteName={location.name}
              />
            </div>
          </div>
          </FinderSlotProvider>

          {/* The one sky chart, placed by whichever slot above holds it. */}
          {finder && createPortal(
            <FinderChart
              subject={finder}
              site={siteArg}
              timeZone={location.timezone}
              nightStart={night.window.sunset}
              nightEnd={night.window.sunrise}
              onSelect={selectOnChart}
            />,
            finderHost,
          )}

          <History
            tonight={resolveNightDate(location.timezone)}
            onOpen={openFromHistory}
            canOpen={canOpenFromHistory}
          />
        </main>
      )}

      <footer className="muted small">
        Ephemeris DE440s · catalog OpenNGC (CC-BY-SA) · weather Open-Meteo and
        7Timer · meteor showers IMO Working List · survey images via hips2fits,
        a service provided by CDS · map tiles ©{" "}
        <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">
          OpenStreetMap
        </a>{" "}
        contributors. All times shown in the observing site's timezone. Your
        sites and history stay in this browser.
      </footer>
    </div>
  );
}
