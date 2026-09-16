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
 * How a site's sky brightness reads in the header and the site picker.
 *
 * A location with no Bortle is not scored as "unknown" — `engine/targets.py`
 * falls back to `BORTLE_SQM[5]`, which sets the limiting magnitude and the
 * contrast test and so changes which objects appear at all. The fallback is
 * reasonable; letting it happen invisibly is not, so a missing value is
 * labelled as an assumption everywhere the value is shown.
 */
function bortleLabel(location: LocationModel): string {
  return location.bortle === null
    ? "Bortle 5 (assumed)"
    : `Bortle ${location.bortle}`;
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
  // "Visible tonight" lists only what passes the observability filters;
  // "All targets" drops the filtering entirely and badges each row instead.
  const [showAllTargets, setShowAllTargets] = useState(false);
  const [loading, setLoading] = useState(true);
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
        if (!first) throw new Error("no locations configured");
        setLocationKey(first.key);
        setDate(resolveNightDate(first.timezone));
      })
      .catch((e) => {
        setError(
          e instanceof ApiError
            ? `API error: ${e.message}`
            : "Could not reach the API. Is it running on port 8000?",
        );
        setLoading(false);
      });
  }, []);

  const location = locations.find((l) => l.key === locationKey);

  // --- the night's data ---
  useEffect(() => {
    if (!locationKey || !date) return;
    let cancelled = false;
    setLoading(true);
    setError(null);

    Promise.all([
      api.night(date, locationKey),
      // Always the unfiltered superset: the "visible tonight" view is
      // derived from it in SkyPanel, so switching modes costs no round-trip.
      // Two separate fetches would double a ~3.5 s server computation.
      api.targets(date, locationKey, 1000, 25, "constellation", "brightness",
                  true),
      api.planets(date, locationKey, false),
    ])
      .then(([nightData, targetsData, planetsData]) => {
        if (cancelled) return;
        setNight(nightData);
        setTargets(targetsData);
        setPlanets(planetsData);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(
          e instanceof ApiError
            ? `API error: ${e.message}`
            : "Could not reach the API. Is it running on port 8000?",
        );
      })
      .finally(() => !cancelled && setLoading(false));

    return () => {
      cancelled = true;
    };
  }, [date, locationKey]);

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
                constellations.join(",") || undefined)
      .then((data) => !cancelled && setAltitude(data))
      .catch(() => !cancelled && setAltitude(null));

    return () => {
      cancelled = true;
    };
  }, [series, date, locationKey]);

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

  const chartedIds = series.map((s) => s.id);
  const hiddenLabels = series.filter((s) => !s.visible).map((s) => s.label);
  const visibleCount = series.length - hiddenLabels.length;

  return (
    <div className="app">
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
              {location.bortle === null ? (
                <span
                  className="tag tag-warn"
                  title="No Bortle class set for this site, so targets are filtered as SQM 20.4 — a suburban sky. That changes which objects appear at all."
                >
                  Bortle 5 (assumed)
                </span>
              ) : (
                `Bortle ${location.bortle}`
              )}
              {location.horizon_is_generic && (
                <span className="tag tag-warn" title="Built-in preset, not a survey">
                  generic horizon
                </span>
              )}
            </p>
          )}
        </div>

        <div className="controls">
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

          <button className="secondary" onClick={() => setManagingSites(true)}>
            Sites…
          </button>

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

          <button className="secondary" onClick={goToTonight} disabled={isTonight}>
            Tonight
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
      {loading && !night && <div className="panel muted">Loading…</div>}

      {night && location && (
        <>
          <EventAlert
            events={events}
            fromDate={date}
            timeZone={location.timezone}
            onOpenEvents={() =>
              skyPanelRef.current?.scrollIntoView({ behavior: "smooth", block: "center" })
            }
          />

          <ScorePanel score={night.score} window={night.window} />

          <div className="dashboard-row">
            <section className="panel chart-panel">
              <div className="panel-head">
                <h3>Altitude through the night</h3>
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
        </>
      )}

      <footer className="muted small">
        Ephemeris DE440s · catalog OpenNGC (CC-BY-SA) · weather Open-Meteo and
        7Timer · meteor showers IMO Working List. All times shown in the
        observing site's timezone.
      </footer>
    </div>
  );
}
