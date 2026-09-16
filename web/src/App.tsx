/**
 * Dashboard shell.
 *
 * Layout, top to bottom: controls, an alert for anything in the next week,
 * the score-and-night panel, then a two-column row with the altitude chart
 * beside the Targets/Planets/Events panel — so adding something to the chart
 * and seeing it appear happens without scrolling. Conditions and the log sit
 * below.
 *
 * Chart contents are state, not a fixed list. On each new night the chart is
 * auto-filled with what is actually worth looking at — the Moon, every planet
 * with any chance of being seen, and the highest-scoring deep-sky objects —
 * and every series can then be removed or added back.
 */

import { useEffect, useRef, useState } from "react";
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
import { ConditionsStrip } from "./components/ConditionsStrip";
import { EventAlert } from "./components/EventAlert";
import { LocationManager } from "./components/LocationManager";
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
 * Altitude floors offered in the UI.
 *
 * 25 deg is the engine default and a sensible one -- below it, extinction and
 * seeing degrade fast. But it was hardcoded here, which had two consequences
 * worth fixing. Obstruction-horizon presets are compared against this floor
 * (`max(min_altitude, horizon)`), so with it pinned at 25 the shallower
 * presets could never change a result; and planets low in twilight, which is
 * where Mercury and Venus live, were filtered out with no way to see them.
 *
 * Each option names what it costs, because lowering the floor is a trade, not
 * an improvement.
 */
const ALTITUDE_FLOORS: { deg: number; label: string; note: string }[] = [
  { deg: 10, label: "10°", note: "includes low twilight objects; heavy extinction and poor seeing" },
  { deg: 15, label: "15°", note: "generous; still hazy near the horizon" },
  { deg: 20, label: "20°", note: "slightly below the default" },
  { deg: 25, label: "25° (default)", note: "the engine's default; a good general floor" },
  { deg: 30, label: "30°", note: "strict; only well-placed objects" },
  { deg: 40, label: "40°", note: "very strict; near-zenith work only" },
];

/**
 * How a site's sky brightness reads in the header and the site picker.
 *
 * Three cases, kept distinct because they are three different degrees of
 * knowing. An observer's own class is a measurement. An atlas value is a
 * lookup. "Assumed" means nothing is known and `engine/targets.py` falls back
 * to Bortle 5 — which sets the limiting magnitude and the contrast test, so it
 * changes which objects appear at all. That fallback is reasonable; letting it
 * happen invisibly is not.
 */
function bortleLabel(location: LocationModel): string {
  switch (location.sky_source) {
    case "observer":
      return `Bortle ${location.bortle}`;
    case "atlas":
      return `Bortle ${location.effective_bortle} (from atlas)`;
    default:
      return "Bortle 5 (assumed)";
  }
}

/**
 * Naked-eye planets only. Uranus and Neptune are computed and listed in the
 * Planets tab, but they are telescope-only objects and charting them by
 * default just adds two curves nobody is planning around.
 */
const NAKED_EYE_PLANETS = ["mercury", "venus", "mars", "jupiter", "saturn"];

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
  const [minAltitude, setMinAltitude] = useState(25);

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
  // "All targets" drops the filtering entirely and badges each row instead.
  const [showAllTargets, setShowAllTargets] = useState(false);
  // Tracked per request, not as one flag. The three calls differ by an order
  // of magnitude -- /api/night answers in ~0.2 s, /api/planets in ~0.8 s warm,
  // /api/targets in ~5 s -- so a single flag meant the whole dashboard waited
  // on the slowest one.
  const [pending, setPending] = useState({
    night: true, targets: true, planets: true,
  });
  const [error, setError] = useState<string | null>(null);
  const [managingSites, setManagingSites] = useState(false);

  // Which (date, location) the chart was last auto-filled for. Without this the
  // auto-fill would fight the user every time they removed a series.
  const autoFilledFor = useRef<string>("");
  const skyPanelRef = useRef<HTMLDivElement>(null);

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
    setNight(null);
    setTargets(null);
    setPlanets(null);
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

    api.night(date, locationKey)
      .then((data) => !cancelled && setNight(data))
      .catch(fail)
      .finally(() => settle("night"));

    api.planets(date, locationKey, false, minAltitude)
      .then((data) => !cancelled && setPlanets(data))
      .catch(fail)
      .finally(() => settle("planets"));

    // Always the unfiltered superset: the "visible tonight" view is derived
    // from it in SkyPanel, so switching modes costs no round-trip. Two
    // separate fetches would double a ~5 s server computation.
    api.targets(date, locationKey, 1000, minAltitude, "constellation",
                "brightness", true)
      .then((data) => !cancelled && setTargets(data))
      .catch(fail)
      .finally(() => settle("targets"));

    return () => {
      cancelled = true;
    };
  }, [date, locationKey, minAltitude]);

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
  // regions are added deliberately from the Targets tab rather than guessed at.
  useEffect(() => {
    if (!planets) return;
    const signature = `${date}|${locationKey}`;
    if (autoFilledFor.current === signature) return;
    autoFilledFor.current = signature;

    const auto: ChartSeries[] = [
      { kind: "body", id: "moon", label: "moon", visible: true },
    ];

    for (const planet of planets.planets) {
      if (!NAKED_EYE_PLANETS.includes(planet.name)) continue;
      if (planet.peak_altitude_deg < PLANET_CHART_FLOOR_DEG) continue;
      auto.push({ kind: "body", id: planet.name, label: planet.name,
                  visible: true });
    }

    setSeries(auto);
  }, [planets, date, locationKey]);

  // --- fetch the curves whenever the series list changes ---
  useEffect(() => {
    if (!locationKey || !date) return;
    let cancelled = false;

    const bodies = series.filter((s) => s.kind === "body").map((s) => s.id);
    const constellations = series
      .filter((s) => s.kind === "constellation")
      .map((s) => s.id);
    if (bodies.length === 0 && constellations.length === 0) {
      setAltitude(null);
      return;
    }

    api
      .altitude(date, locationKey, bodies.join(","), undefined,
                constellations.join(",") || undefined, minAltitude)
      .then((data) => !cancelled && setAltitude(data))
      .catch(() => !cancelled && setAltitude(null));

    return () => {
      cancelled = true;
    };
  }, [series, date, locationKey, minAltitude]);

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

  function goToTonight() {
    if (!location) return;
    setDate(resolveNightDate(location.timezone));
    setIsTonight(true);
  }

  function changeDate(next: string) {
    setDate(next);
    setIsTonight(location ? next === resolveNightDate(location.timezone) : false);
  }

  /** Add or drop a series, from the Targets/Planets tables. */
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
          <h1>Astro Night Planner</h1>
          {night && location && (
            <p className="muted">
              <strong>{formatDate(date, location.timezone)}</strong>
              {isTonight && <span className="tag">tonight</span>}
              {" "}{location.name} · {location.lat.toFixed(3)},{" "}
              {location.lon.toFixed(3)} · {location.elevation_m.toFixed(0)} m
              {" · "}
              {location.sky_source === "assumed" ? (
                <span
                  className="tag tag-warn"
                  title="No Bortle class set for this site, so targets are filtered as SQM 20.4 — a suburban sky. That changes which objects appear at all."
                >
                  Bortle 5 (assumed)
                  <span className="visually-hidden">
                    {" "}— no Bortle class is set for this site, so targets are
                    filtered as SQM 20.4, a suburban sky. That changes which
                    objects appear at all.
                  </span>
                </span>
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
              {location.horizon_is_generic && (
                <span className="tag tag-warn" title="Built-in preset, not a survey">
                  generic horizon
                  <span className="visually-hidden">
                    {" "}— a built-in preset, not a survey of this site
                  </span>
                </span>
              )}
            </p>
          )}
        </div>

        <div className="controls">
          {/* Before any site exists these controls have nothing to act on: an
              empty picker, a date for nowhere, a floor filtering nothing. The
              only useful control is the one that adds a site, so it is the
              only one shown. */}
          {hasSites && (
          <label>
            <span>Location</span>
            <select
              value={locationKey}
              onChange={(e) => {
                setLocationKey(e.target.value);
                const next = locations.find((l) => l.key === e.target.value);
                if (next && isTonight) setDate(resolveNightDate(next.timezone));
              }}
            >
              {locations.map((l) => (
                <option key={l.key} value={l.key}>
                  {l.name} · {bortleLabel(l)}
                </option>
              ))}
            </select>
          </label>
          )}

          <button className="secondary" onClick={() => setManagingSites(true)}>
            Sites…
          </button>

          {hasSites && (
          <>
          <label>
            <span>Night of</span>
            <div className="date-row">
              <button onClick={() => changeDate(shiftDate(date, -1))} aria-label="Previous night">
                ‹
              </button>
              <input type="date" value={date} onChange={(e) => changeDate(e.target.value)} />
              <button onClick={() => changeDate(shiftDate(date, 1))} aria-label="Next night">
                ›
              </button>
            </div>
          </label>

          <label>
            <span>Altitude floor</span>
            <select
              value={String(minAltitude)}
              onChange={(e) => setMinAltitude(Number(e.target.value))}
              title="Objects below this altitude are excluded"
            >
              {ALTITUDE_FLOORS.map((floor) => (
                <option key={floor.deg} value={String(floor.deg)}>
                  {floor.label}
                </option>
              ))}
            </select>
          </label>

          <button className="secondary" onClick={goToTonight} disabled={isTonight}>
            Tonight
          </button>
          </>
          )}

          <button
            className="secondary night-vision-toggle"
            onClick={() => setNightVision((on) => !on)}
            aria-pressed={nightVision}
            title="Red palette that preserves dark adaptation at the eyepiece"
          >
            Night vision
          </button>
        </div>
      </header>

      {managingSites && (
        <LocationManager
          locations={locations}
          onClose={() => setManagingSites(false)}
          onCreated={(key) => {
            void refreshLocations(key);
            setManagingSites(false);
          }}
          onDeleted={() => void refreshLocations()}
        />
      )}

      {error && <div className="panel error">{error}</div>}

      {/* First run. Nothing is preconfigured, so the dashboard asks where you
          are instead of assuming somewhere and quietly being wrong about the
          darkness, the horizon and half the target list. */}
      {!error && locations.length === 0 && !pending.night && (
        <section className="panel empty-state">
          <h2>Where are you observing?</h2>
          <p>
            No sites yet. This planner does not assume one: when it gets dark,
            what clears your horizon, and which objects are bright enough for
            your sky all depend on where you are standing, so a default
            belonging to somebody else would be worse than no answer.
          </p>
          <button className="secondary" onClick={() => setManagingSites(true)}>
            Add an observing site
          </button>
          <p className="muted small">
            Pick a point on a map, search for a place by name, or type
            coordinates. Everything else follows from that.
          </p>
        </section>
      )}

      {pending.night && !night && locations.length > 0 && (
        <div className="panel muted">Loading…</div>
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

          {minAltitude !== 25 && (
            <p className="panel note">
              Altitude floor set to <strong>{minAltitude}°</strong> instead of
              the default 25°:{" "}
              {ALTITUDE_FLOORS.find((f) => f.deg === minAltitude)?.note}.
              {minAltitude < 25 && location.horizon_name !== "flat" && (
                <>
                  {" "}Your <strong>{location.horizon_name}</strong> horizon
                  profile reaches {location.horizon_max_deg.toFixed(0)}°, so it
                  {location.horizon_max_deg > minAltitude
                    ? " is now the binding constraint in the directions it covers."
                    : " still sits below this floor and is not affecting these results."}
                </>
              )}
            </p>
          )}

          <ScorePanel score={night.score} window={night.window} />

          <div className="dashboard-row">
            <section className="panel chart-panel">
              <div className="panel-head">
                <h2>Altitude through the night</h2>
                <span className="muted small">
                  {visibleCount} of {series.length} shown · click a name to
                  hide it, × to remove it · add constellations from Targets
                </span>
              </div>
              {altitude ? (
                <AltitudeChart
                  data={altitude}
                  timeZone={location.timezone}
                  hidden={hiddenLabels}
                  onToggle={toggleVisibleByLabel}
                  onRemove={removeByLabel}
                />
              ) : (
                <p className="muted">
                  Nothing charted. Add targets or planets from the panel beside
                  this one.
                </p>
              )}
            </section>

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
                targetsPending={pending.targets}
              />
            </div>
          </div>

          <ConditionsStrip
            slots={night.score.slots}
            timeZone={location.timezone}
            weatherAvailable={night.score.weather_available}
          />

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
