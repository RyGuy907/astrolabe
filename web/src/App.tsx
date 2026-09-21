/**
 * Dashboard shell.
 *
 * Layout, top to bottom: controls, an alert for anything in the next week,
 * the score-and-night panel, then a two-column row with the altitude chart
 * beside the Deep Sky/Solar System/Events panel — so adding something to the chart
 * and seeing it appear happens without scrolling. Conditions and the log sit
 * below.
 *
 * Chart contents are state, not a fixed list. On each new night the chart is
 * auto-filled with what is actually worth looking at — the Moon, every planet
 * with any chance of being seen, and the highest-scoring deep-sky objects —
 * and every series can then be removed or added back.
 */

import { useEffect, useMemo, useRef, useState } from "react";
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
import { SPLIT_DEFAULT, SPLIT_MAX, SPLIT_MIN, Splitter } from "./components/Splitter";
import { EventAlert } from "./components/EventAlert";
import { LocationManager } from "./components/LocationManager";
import { LocationPicker } from "./components/LocationPicker";
import { ObservationLog } from "./components/ObservationLog";
import { ScorePanel } from "./components/ScorePanel";
import { SkyPanel } from "./components/SkyPanel";
import { formatDate, resolveNightDate, shiftDate } from "./format";

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
  const [locations, setLocations] = useState<LocationModel[]>([]);
  const [locationKey, setLocationKey] = useState<string>("");
  const [date, setDate] = useState<string>("");
  const [isTonight, setIsTonight] = useState(true);

  const [night, setNight] = useState<NightResponse | null>(null);
  const [targets, setTargets] = useState<TargetsResponse | null>(null);
  const [planets, setPlanets] = useState<PlanetsResponse | null>(null);
  const [events, setEvents] = useState<EventsResponse | null>(null);
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
  const [deletingSite, setDeletingSite] = useState(false);

  // Which (date, location) the chart was last auto-filled for. Without this the
  // auto-fill would fight the user every time they removed a series.
  const autoFilledFor = useRef<string>("");
  // Which (date, site) is currently on screen, so a session change can be
  // told apart from a move to a different night.
  const loadedSubject = useRef<string>("");
  const skyPanelRef = useRef<HTMLDivElement>(null);
  const rowRef = useRef<HTMLDivElement>(null);

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

  useEffect(() => {
    api
      .locations()
      .then((list) => {
        setLocations(list);
        const first = list[0];
        // An empty list is the ordinary first run, not a failure: nothing
        // ships preconfigured, because every answer this planner gives
        // depends on where the observer is standing. The dashboard stays
        // empty and asks for a site rather than inventing one.
        if (!first) {
          setPending({ night: false, targets: false, planets: false });
          return;
        }
        setLocationKey(first.key);
        setDate(resolveNightDate(first.timezone));
      })
      .catch((e) => {
        setError(
          e instanceof ApiError
            ? `API error: ${e.message}`
            : "Could not reach the API. Is it running on port 8000?",
        );
        // The per-request effect below never runs without a location, so
        // clear the pending flags here or the shell shows "Loading..."
        // forever underneath the error.
        setPending({ night: false, targets: false, planets: false });
      });
  }, []);

  const location = locations.find((l) => l.key === locationKey);

  // --- the night's data ---
  useEffect(() => {
    if (!locationKey || !date) return;
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
    const subject = `${date}|${locationKey}`;
    if (loadedSubject.current !== subject) {
      loadedSubject.current = subject;
      setNight(null);
      setTargets(null);
      setPlanets(null);
    }
    setPending({ night: true, targets: true, planets: true });

    const fail = (e: unknown) => {
      if (cancelled) return;
      setError(
        e instanceof ApiError
          ? `API error: ${e.message}`
          : "Could not reach the API. Is it running on port 8000?",
      );
    };
    const settle = (key: "night" | "targets" | "planets") => {
      if (!cancelled) setPending((current) => ({ ...current, [key]: false }));
    };

    api.night(date, locationKey, session ?? undefined)
      .then((data) => !cancelled && setNight(data))
      .catch(fail)
      .finally(() => settle("night"));

    api.planets(date, locationKey, false)
      .then((data) => !cancelled && setPlanets(data))
      .catch(fail)
      .finally(() => settle("planets"));

    // Always the unfiltered superset: the "visible tonight" view is derived
    // from it in SkyPanel, so switching modes costs no round-trip. Two
    // separate fetches would double a ~5 s server computation.
    api.targets(date, locationKey, 1000, 0, "constellation",
                "brightness", true, session ?? undefined)
      .then((data) => !cancelled && setTargets(data))
      .catch(fail)
      .finally(() => settle("targets"));

    return () => {
      cancelled = true;
    };
  }, [date, locationKey, session]);

  // --- events, on their own horizon ---
  useEffect(() => {
    if (!locationKey || !date) return;
    let cancelled = false;
    api
      .events(date, locationKey, eventDays)
      .then((data) => !cancelled && setEvents(data))
      .catch(() => !cancelled && setEvents(null));
    return () => {
      cancelled = true;
    };
  }, [date, locationKey, eventDays]);

  // --- auto-fill the chart for each new night ---
  // Just the Moon and the naked-eye planets that are actually up. Deep-sky
  // regions are added deliberately from the Deep Sky tab rather than guessed at.
  useEffect(() => {
    if (!planets) return;
    const signature = `${date}|${locationKey}`;
    if (autoFilledFor.current === signature) return;
    autoFilledFor.current = signature;

    const auto: ChartSeries[] = [
      { kind: "body", id: "moon", label: "moon", visible: true },
    ];

    for (const planet of planets.planets) {
      if (!CHARTED_PLANETS.includes(planet.name)) continue;
      if (planet.peak_altitude_deg < PLANET_CHART_FLOOR_DEG) continue;
      auto.push({ kind: "body", id: planet.name, label: planet.name,
                  visible: true });
    }

    setSeries(auto);
  }, [planets, date, locationKey]);

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
    if (!locationKey || !date) return;
    let cancelled = false;

    const { bodies, constellations } = members;
    if (bodies.length === 0 && constellations.length === 0) {
      setAltitude(null);
      return;
    }

    api
      .altitude(date, locationKey, bodies.join(","), undefined,
                constellations.join(",") || undefined, 0)
      .then((data) => !cancelled && setAltitude(data))
      .catch(() => !cancelled && setAltitude(null));

    return () => {
      cancelled = true;
    };
  }, [members, date, locationKey]);

  // --- default visibility: what is actually up while you are out ---
  // A body on the chart that stays below the floor for the whole observing
  // session starts hidden -- its chip stays, greyed, one click from coming
  // back. Decided again only when the night, the site or the hours change,
  // so a curve the observer shows or hides by hand stays that way.
  const visibilityDecidedFor = useRef("");
  useEffect(() => {
    const hours = night?.session;
    if (!altitude || !hours || !location) return;
    const signature = `${date}|${locationKey}|${hours.start}|${hours.end}`;
    if (visibilityDecidedFor.current === signature) return;

    const bodies = series.filter((s) => s.kind === "body");
    const byLabel = new Map(altitude.series.map((s) => [s.label, s]));
    // Wait for curves that match what is charted, not a stale response.
    if (bodies.length === 0 || !bodies.every((s) => byLabel.has(s.label))) return;
    visibilityDecidedFor.current = signature;

    const start = new Date(hours.start).getTime();
    const end = new Date(hours.end).getTime();
    const floor = Math.max(PLANET_CHART_FLOOR_DEG, location.horizon_max_deg);
    const upDuringSession = new Set(
      bodies
        .filter((s) => byLabel.get(s.label)!.points.some((p) => {
          const t = new Date(p.time).getTime();
          return t >= start && t <= end && p.altitude_deg >= floor;
        }))
        .map((s) => s.label),
    );
    setSeries((current) => current.map((s) =>
      s.kind === "body" ? { ...s, visible: upDuringSession.has(s.label) } : s));
  // `series` is read, not watched: a toggle must not re-decide the defaults.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [altitude, night?.session, date, locationKey, location]);

  /** Reload the site list after an add or a delete.
   *
   * `select` is the key to switch to: the new site after a create, or nothing
   * after deleting a site that was not the active one. Deleting the *active*
   * site leaves `locationKey` pointing at something that no longer exists, so
   * fall back to the first remaining site rather than leaving the dashboard
   * fetching a 404.
   */
  async function refreshLocations(select?: string) {
    let list: LocationModel[];
    try {
      list = await api.locations();
    } catch (e) {
      setError(
        e instanceof ApiError
          ? `API error: ${e.message}`
          : "Could not reach the API. Is it running on port 8000?",
      );
      return;
    }
    setLocations(list);
    const target =
      (select ? list.find((l) => l.key === select) : undefined) ??
      list.find((l) => l.key === locationKey) ??
      list[0];
    if (!target || target.key === locationKey) return;
    setLocationKey(target.key);
    if (isTonight) setDate(resolveNightDate(target.timezone));
  }

  /** Remove a site from the picker row, then re-point the dashboard. */
  async function deleteLocation(key: string) {
    setDeletingSite(true);
    try {
      await api.deleteLocation(key);
      const list = await api.locations();
      setLocations(list);
      if (key === locationKey) {
        const next = list[0];
        setLocationKey(next ? next.key : "");
        if (next) setDate(resolveNightDate(next.timezone));
        else setPending({ night: false, targets: false, planets: false });
      }
    } catch (e) {
      setError(e instanceof ApiError
        ? `Could not delete that site: ${e.message}`
        : "Could not reach the API. Is it running on port 8000?");
    } finally {
      setDeletingSite(false);
    }
  }

  function changeDate(next: string) {
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

  return (
    <div className="app">
      {/* First thing in the tab order. The page has ~115 focusable elements in
          one flat sequence -- most of them target rows -- so without this,
          reaching the observation log by keyboard means tabbing past all of
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
              {" "}{location.name} · {location.lat.toFixed(3)},{" "}
              {location.lon.toFixed(3)} · {location.elevation_m.toFixed(0)} m
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
                  Bortle {location.effective_bortle} (from atlas)
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
            busy={deletingSite}
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
            onDelete={(key) => void deleteLocation(key)}
          />

          {hasSites && (
            <div className="date-row">
              <button onClick={() => changeDate(shiftDate(date, -1))}
                      aria-label="Previous night">
                ‹
              </button>
              <input type="date" value={date}
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
            title="Red palette that preserves dark adaptation at the eyepiece"
          >
            <span className="switch-track" aria-hidden="true">
              <span className="switch-thumb" />
            </span>
            Night vision
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
          onCreated={(key) => {
            void refreshLocations(key);
            setManagingSites(false);
            setEditingSite(null);
          }}
        />
      )}

      {error && <div className="panel error">{error}</div>}

      {/* First run. Nothing is preconfigured, so the dashboard asks where you
          are instead of assuming somewhere and quietly being wrong about the
          darkness, the horizon and half the target list. */}
      {!error && locations.length === 0 && !pending.night && (
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
        </section>
      )}

      {/* The dashboard's outline while the night loads, not a one-line
          "Loading…" -- that collapsed the page and then snapped a full
          screen of dashboard in underneath it on every site or date change. */}
      {!error && pending.night && !night && locations.length > 0 && (
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

          <div className="dashboard-row resizable" ref={rowRef} style={splitStyle}>
            <section className="panel chart-panel">
              <div className="panel-head">
                <h2>Altitude</h2>
                <span className="muted small">
                  {visibleCount} of {series.length} shown
                </span>
              </div>
              {altitude ? (
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
                  Moon and planets from Solar System, in the panel beside this
                  one.
                </p>
              )}
            </section>

            <Splitter rowRef={rowRef} split={split} onChange={setSplit} />

            <div ref={skyPanelRef}>
              <SkyPanel
                targets={targets}
                planets={planets}
                events={events}
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
              />
            </div>
          </div>

          <ObservationLog
            date={date}
            locationKey={locationKey}
            timeZone={location.timezone}
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
        contributors. All times shown in the observing site's timezone.
      </footer>
    </div>
  );
}
