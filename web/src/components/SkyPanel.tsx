/**
 * One panel, three tabs: Deep Sky, Solar System, Events.
 *
 * Named to match the two dials above them -- the deep-sky score says how good
 * tonight is for the first tab, the planetary score for the second. "Targets"
 * stopped saying anything once the Moon and planets had a tab of their own,
 * and "Planets" stopped being true once the Moon was in it. Deep Sky also
 * holds the famous double stars, which observing guides routinely list
 * alongside it.
 *
 * These were three separate boxes. Folding them into a tab strip keeps the
 * dashboard to a handful of regions and lets this column sit beside the
 * altitude chart, so you can add something to the chart and see it appear
 * without scrolling.
 */

import { useDeferredValue, useMemo, useState } from "react";
import type { FinderSubject } from "./FinderChart";
import { PlanetImage } from "./PlanetImage";
import { TargetImage } from "./TargetImage";
import {
  Fact,
  FactSheet,
  formatAngularSize,
  formatLightMinutes,
  formatLightYears,
  formatRotation,
  formatTrueSize,
  formatYear,
} from "./FactSheet";
import type {
  EventsResponse,
  MoonModel,
  PlanetModel,
  PlanetsResponse,
  TargetModel,
  TargetsResponse,
} from "../api";
import {
  formatDate,
  formatDegrees,
  formatMagnitude,
  formatTime,
  moonPhaseName,
  scoreColor,
  titleCase,
} from "../format";

type Tab = "targets" | "planets" | "events";

interface Props {
  targets: TargetsResponse | null;
  planets: PlanetsResponse | null;
  events: EventsResponse | null;
  timeZone: string;
  charted: string[];
  onToggleChart: (id: string, label: string,
                  kind: "body" | "constellation") => void;
  eventDays: number;
  onEventDaysChange: (days: number) => void;
  showAll: boolean;
  onShowAllChange: (all: boolean) => void;
  popularOnly: boolean;
  onPopularOnlyChange: (only: boolean) => void;
  /** True while /api/targets is in flight. It is much slower than the other
   *  calls, so the tab needs to say so rather than looking empty. */
  targetsPending: boolean;
  /** Opens a finder chart over the altitude chart. */
  onOpenFinder?: (subject: FinderSubject) => void;
}

const EVENT_HORIZONS = [30, 90, 365];

/** Opens this row's finder chart over the altitude chart. */
function FinderButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button className="finder-open" onClick={onClick}
            title="Finder chart for star hopping"
            aria-label={`Finder chart for ${label}`}>
      <svg viewBox="0 0 16 16" aria-hidden="true">
        <circle cx="8" cy="8" r="5.2" />
        <path d="M8 0.8v3.6M8 11.6v3.6M0.8 8h3.6M11.6 8h3.6" />
      </svg>
    </button>
  );
}

// Search expands every matching group at once, so both of these are render
// bounds rather than result limits: the counts shown are always the true
// totals, only the rows drawn are capped.
const SEARCH_ROWS_PER_GROUP = 25;
const SEARCH_GROUPS = 15;

/**
 * A short label whose meaning is not hover-only.
 *
 * These badges carried their explanation in a `title` alone -- 223 of them on
 * a typical page. A title is invisible on touch, never shown to a keyboard
 * user, and unreliable as an accessible name, so "too faint" was a word with
 * no way to find out what it meant unless you had a mouse. The label stays
 * visible and compact; the meaning goes in text that assistive technology
 * reads and the layout ignores. `title` is kept as the mouse affordance, and
 * the key under the table covers sighted touch users.
 */
function Badge({ kind, meaning, children }: {
  kind: string;
  meaning: string;
  children: React.ReactNode;
}) {
  return (
    <span className={`badge badge-${kind}`} title={meaning}>
      {children}
      <span className="visually-hidden"> — {meaning}</span>
    </span>
  );
}

const CONSTELLATION_MEANING: Record<string, string> = {
  tonight: "Comes up during your observing hours",
  late: "Only comes up after your observing hours end",
  none: "Never clears the horizon tonight",
};

function TargetRow({ target, timeZone, showAll, onFinder }: {
  target: TargetModel;
  timeZone: string;
  showAll: boolean;
  onFinder?: () => void;
}) {
  // Per row, and closed by default. The image is only requested once a row is
  // opened, so browsing a 270-row list costs nothing.
  const [open, setOpen] = useState(false);
  //: Component magnitudes are only set for the curated doubles, and a couple
  //: of the usual rows do not apply to a pair of stars.
  const isDouble = target.component_mags !== null;
  return (
    <>
    <tr className={target.visible_tonight && !target.too_faint ? undefined : "dim"}>
      <td>
        {target.score === null ? (
          <span className="score-pill score-pill-none">—</span>
        ) : (
          <span className="score-pill" style={{ background: scoreColor(target.score) }}>
            {target.score.toFixed(0)}
          </span>
        )}
      </td>
      <td>
        <button
          className="target-name"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          title={open ? "Hide the survey image" : "Show a survey image"}
        >
          <span className="caret">{open ? "▾" : "▸"}</span>
          <strong>{target.display_name}</strong>
        </button>
        {onFinder && <FinderButton label={target.display_name} onClick={onFinder} />}
        <span className="muted"> · {target.object_type}</span>
        {/* "visible late" is the badge that changes plans, so it shows in
            both modes. "visible tonight" only earns space in the unfiltered
            view, where it separates rows from the ones that never come up. */}
        {target.visible_late && (
          <Badge
            kind="late"
            meaning="Only comes up after your observing hours end"
          >
            visible late
          </Badge>
        )}
        {showAll && target.visible_tonight && !target.visible_late &&
          !target.too_faint && (
            <span className="badge badge-visible">visible tonight</span>
          )}
        {/* Up and pointable, but the sky will beat you. Shown rather than
            filtered so the limit is visible, and sorted last. */}
        {target.too_faint && (
          <Badge
            kind="faint"
            meaning="Up tonight, but below the detection threshold for this sky"
          >
            too faint
          </Badge>
        )}
        {target.notes.length > 0 && (
          <div className="target-notes">{target.notes.join(" · ")}</div>
        )}
      </td>
      <td>{target.magnitude === null ? "—" : target.magnitude.toFixed(1)}</td>
      <td>
        {target.peak_altitude_deg === null
          ? "—"
          : formatDegrees(target.peak_altitude_deg)}
      </td>
      <td className="nowrap">
        {target.best_window
          ? `${formatTime(target.best_window.start, timeZone)}-${formatTime(
              target.best_window.end, timeZone)}`
          : "—"}
      </td>
    </tr>
    {open && (
      <tr className="target-detail">
        <td colSpan={5}>
          <TargetImage
            name={target.display_name}
            raDeg={target.ra_deg}
            decDeg={target.dec_deg}
            sizeArcmin={target.size_arcmin}
          >
            {/* The column beside the cutout held one line of attribution and
                300px of nothing. Every row here is optional: reference data is
                curated for the objects anyone opens, and a blank
                "Discovered —" would read as a failure rather than as the edge
                of what is known. */}
            <FactSheet>
              <Fact label="Type">{target.object_type}</Fact>
              {target.constellation && (
                <Fact label="In">{target.constellation}</Fact>
              )}
              {target.distance_ly !== null && (
                <Fact label="Distance">
                  {formatLightYears(target.distance_ly)}
                </Fact>
              )}
              {/* A double star's apparent size *is* its separation, and the
                  row below says so in the units people use for it. */}
              {target.size_arcmin !== null && !isDouble && (
                <Fact label="Apparent size">
                  {formatAngularSize(target.size_arcmin)}
                </Fact>
              )}
              {target.diameter_ly !== null && (
                <Fact label="True size">
                  {formatTrueSize(target.diameter_ly)} across
                </Fact>
              )}
              {target.separation_arcsec !== null && (
                <Fact label="Separation">
                  {target.separation_arcsec.toFixed(1)}″
                </Fact>
              )}
              {target.component_mags && (
                <Fact label="Components">{target.component_mags}</Fact>
              )}
              {target.magnitude !== null && target.component_mags === null && (
                <Fact label="Magnitude">{target.magnitude.toFixed(1)}</Fact>
              )}
              {/* Surface brightness describes a glow spread over an area.
                  Two point sources do not have one, and the number the
                  formula produces for a pair is meaningless. */}
              {target.surface_brightness !== null && !isDouble && (
                <Fact label="Surface brightness">
                  {target.surface_brightness.toFixed(1)} mag/arcsec²
                </Fact>
              )}
              {target.discovered_by && (
                <Fact label="Discovered by">
                  {target.discovered_by}
                  {target.discovered_year !== null &&
                    `, ${formatYear(target.discovered_year)}`}
                </Fact>
              )}
              {target.about && (
                <div className="fact-about">{target.about}</div>
              )}
            </FactSheet>
          </TargetImage>
        </td>
      </tr>
    )}
    </>
  );
}

/**
 * One planet, expandable to a drawing of tonight's disc and its facts.
 *
 * Same shape as `TargetRow`: closed by default, and the detail row spans the
 * table. Unlike a target there is nothing to fetch — the disc is drawn from
 * numbers already in the response, so opening one costs nothing.
 */
function PlanetRow({ planet, charted, onToggleChart, minAltitude, onFinder }: {
  planet: PlanetModel;
  charted: string[];
  onToggleChart: (id: string, label: string,
                  kind: "body" | "constellation") => void;
  minAltitude: number;
  onFinder?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const onChart = charted.includes(planet.name);
  const facts = planet.facts;

  return (
    <>
      <tr className={planet.observable ? undefined : "dim"}>
        <td>
          <button
            className={`chip ${onChart ? "chip-on" : ""}`}
            onClick={() => onToggleChart(planet.name, planet.name, "body")}
            title="Toggle on the altitude chart"
            aria-label={
              onChart
                ? `Remove ${titleCase(planet.name)} from the chart`
                : `Chart ${titleCase(planet.name)}`
            }
            aria-pressed={onChart}
          >
            {onChart ? "✓" : "+"}
          </button>
        </td>
        <td>
          <button
            className="target-name"
            onClick={() => setOpen(!open)}
            aria-expanded={open}
            title={open ? "Hide the details" : "Show the details"}
          >
            <span className="caret">{open ? "▾" : "▸"}</span>
            <strong>{titleCase(planet.name)}</strong>
          </button>
          {onFinder && <FinderButton label={titleCase(planet.name)} onClick={onFinder} />}
          {planet.ring_tilt_deg !== null && (
            <div className="target-notes">
              rings {planet.ring_tilt_deg.toFixed(1)}°
            </div>
          )}
        </td>
        <td>{formatMagnitude(planet.magnitude)}</td>
        <td>
          {planet.apparent_diameter_arcsec === null
            ? "—"
            : `${planet.apparent_diameter_arcsec.toFixed(1)}″`}
        </td>
        <td>{formatDegrees(planet.peak_altitude_deg)}</td>
        <td>
          {planet.trend}
          {planet.event_name && (
            <div className="target-notes">
              {planet.event_name} {formatDate(planet.event_date)}
            </div>
          )}
          {!planet.observable && planet.next_visible_date && (
            <div className="target-notes">
              next above {minAltitude}° on{" "}
              {formatDate(planet.next_visible_date)}
            </div>
          )}
          {!planet.observable && planet.best_altitude_deg !== null && (
            <div className="target-notes">
              never clears {minAltitude}° within a year; best{" "}
              {planet.best_altitude_deg.toFixed(0)}°
            </div>
          )}
        </td>
      </tr>

      {open && (
        <tr className="target-detail">
          <td colSpan={6}>
            <div className="detail-split">
              <PlanetImage name={planet.name} />

              <FactSheet>
                {planet.distance_au !== null && (
                  <Fact label="Distance">
                    {planet.distance_au.toFixed(2)} AU ·{" "}
                    {formatLightMinutes(planet.distance_au)}
                  </Fact>
                )}
                {facts && (
                  <Fact label="Diameter">
                    {facts.equatorial_diameter_km.toLocaleString("en-GB")} km
                  </Fact>
                )}
                {planet.apparent_diameter_arcsec !== null && (
                  <Fact label="Apparent size">
                    {planet.apparent_diameter_arcsec.toFixed(1)}″
                  </Fact>
                )}
                {planet.illuminated_fraction !== null && (
                  <Fact label="Illuminated">
                    {(planet.illuminated_fraction * 100).toFixed(0)}%
                  </Fact>
                )}
                {facts && (
                  <Fact label="Day">{formatRotation(facts.rotation_hours)}</Fact>
                )}
                {facts && (
                  <Fact label="Year">
                    {facts.year_earth_years < 1
                      ? `${(facts.year_earth_years * 365.25).toFixed(0)} days`
                      : `${facts.year_earth_years} Earth years`}
                  </Fact>
                )}
                {facts && <Fact label="Moons">{facts.moons}</Fact>}
                {facts?.discovered_by ? (
                  <Fact label="Discovered by">
                    {facts.discovered_by}
                    {facts.discovered_year !== null &&
                      `, ${formatYear(facts.discovered_year)}`}
                  </Fact>
                ) : facts ? (
                  <Fact label="Discovered">
                    Naked-eye; known to every ancient culture
                  </Fact>
                ) : null}
                {facts && <div className="fact-about">{facts.about}</div>}
              </FactSheet>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

/** Light travel time for a distance in km, for a body far closer than an AU. */
function formatLightSeconds(km: number): string {
  return `${(km / 299_792.458).toFixed(2)} light seconds`;
}

/**
 * The Moon, in the same six columns as a planet.
 *
 * The Apparition column is where the two differ: a planet's story is weeks to
 * opposition, and the Moon's is its phase and the next quarter, which is what
 * decides whether it is tonight's target or the thing washing out every
 * other target. Times are the site's clock, formatted here and nowhere else.
 */
function MoonRow({ moon, charted, onToggleChart, minAltitude, timeZone, onFinder }: {
  moon: MoonModel;
  charted: string[];
  onToggleChart: (id: string, label: string,
                  kind: "body" | "constellation") => void;
  minAltitude: number;
  timeZone: string;
  onFinder?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const onChart = charted.includes("moon");
  const phase = moonPhaseName(moon.illuminated_fraction, moon.waxing);
  const facts = moon.facts;

  return (
    <>
      <tr className={moon.observable ? undefined : "dim"}>
        <td>
          <button
            className={`chip ${onChart ? "chip-on" : ""}`}
            onClick={() => onToggleChart("moon", "moon", "body")}
            title="Toggle on the altitude chart"
            aria-label={onChart ? "Remove the Moon from the chart" : "Chart the Moon"}
            aria-pressed={onChart}
          >
            {onChart ? "✓" : "+"}
          </button>
        </td>
        <td>
          <button
            className="target-name"
            onClick={() => setOpen(!open)}
            aria-expanded={open}
            title={open ? "Hide the details" : "Show the details"}
          >
            <span className="caret">{open ? "▾" : "▸"}</span>
            <strong>Moon</strong>
          </button>
          {onFinder && <FinderButton label="the Moon" onClick={onFinder} />}
          <div className="target-notes">
            {(moon.illuminated_fraction * 100).toFixed(0)}% lit
          </div>
        </td>
        <td>{formatMagnitude(moon.magnitude)}</td>
        <td>{formatAngularSize(moon.apparent_diameter_arcsec / 60)}</td>
        <td>{formatDegrees(moon.peak_altitude_deg)}</td>
        <td>
          {phase}
          <div className="target-notes">
            {moon.next_phase_name}{" "}
            {formatTime(moon.next_phase_time, timeZone, true)}
          </div>
          {!moon.observable && moon.notes.length === 0 && (
            <div className="target-notes">
              stays below {minAltitude}° tonight
            </div>
          )}
          {moon.notes.map((note) => (
            <div key={note} className="target-notes">{note}</div>
          ))}
        </td>
      </tr>

      {open && (
        <tr className="target-detail">
          <td colSpan={6}>
            <div className="detail-split">
              <PlanetImage name="moon" />

              <FactSheet>
                <Fact label="Distance">
                  {Math.round(moon.distance_km).toLocaleString("en-GB")} km ·{" "}
                  {formatLightSeconds(moon.distance_km)}
                </Fact>
                <Fact label="Diameter">
                  {facts.diameter_km.toLocaleString("en-GB")} km
                </Fact>
                <Fact label="Apparent size">
                  {formatAngularSize(moon.apparent_diameter_arcsec / 60)}
                </Fact>
                <Fact label="Illuminated">
                  {(moon.illuminated_fraction * 100).toFixed(0)}%
                </Fact>
                <Fact label="Phase">
                  {titleCase(phase)}, {moon.age_days.toFixed(1)} days old
                </Fact>
                {/* In time order, not rise-then-set. These follow the
                    almanac convention -- events in the site's calendar day
                    -- so on a waxing Moon the set comes first, in the small
                    hours, and "rises 16:33 / sets 00:46" would read as a set
                    eight hours after the rise instead of before it. */}
                {(moon.moonrise || moon.moonset) && (
                  <Fact label="Rise and set">
                    {[
                      { verb: "rises", at: moon.moonrise },
                      { verb: "sets", at: moon.moonset },
                    ]
                      .filter((e): e is { verb: string; at: string } => e.at !== null)
                      .sort((a, b) => a.at.localeCompare(b.at))
                      .map((e) => `${e.verb} ${formatTime(e.at, timeZone, true)}`)
                      .join(" · ")}
                  </Fact>
                )}
                <Fact label={`Next ${moon.next_phase_name}`}>
                  {formatTime(moon.next_phase_time, timeZone, true)}
                </Fact>
                <Fact label="Orbit">
                  {facts.sidereal_month_days} days · {facts.synodic_month_days}{" "}
                  days New Moon to New Moon
                </Fact>
                <Fact label="Day">
                  {facts.sidereal_month_days} days, so the same face always
                  points at us
                </Fact>
                <Fact label="Ever visible">
                  {(facts.visible_surface_fraction * 100).toFixed(0)}% of the
                  surface, thanks to libration
                </Fact>
                <div className="fact-about">{facts.about}</div>
              </FactSheet>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

export function SkyPanel({
  targets, planets, events, timeZone, charted, onToggleChart,
  eventDays, onEventDaysChange, showAll, onShowAllChange,
  popularOnly, onPopularOnlyChange, targetsPending, onOpenFinder,
}: Props) {
  const [tab, setTab] = useState<Tab>("targets");
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});
  const [search, setSearch] = useState("");

  // The typed value updates instantly; the expensive filter runs against the
  // deferred one, so keystrokes never wait on a re-render of the list.
  const deferredSearch = useDeferredValue(search);
  const query = deferredSearch.trim().toLowerCase();
  const isStale = search !== deferredSearch;

  // One lowercased haystack per object, built once per payload rather than
  // six toLowerCase() calls per object per keystroke. With 12,371 objects
  // that was ~74k string allocations for every character typed.
  const haystacks = useMemo(() => {
    const map = new Map<string, string>();
    if (!targets) return map;
    for (const [key, rows] of Object.entries(targets.groups)) {
      const label = targets.group_info?.[key]?.label ?? key;
      for (const target of rows) {
        const designation = target.messier ? `m${target.messier}` : "";
        map.set(
          target.name,
          [
            target.display_name, target.name, target.object_type, label,
            designation,
            // "M 13" and "M13" should both find it.
            designation ? `m ${target.messier}` : "",
            // Every other name it goes by. Only the first is on display, so
            // without these "swan nebula" finds nothing and M17 sits there
            // labelled Checkmark.
            ...target.aliases,
          ].join(" ").toLowerCase(),
        );
      }
    }
    return map;
  }, [targets]);

  function matchesTarget(target: TargetModel): boolean {
    if (!query) return true;
    return (haystacks.get(target.name) ?? "").includes(query);
  }

  // The API always sends the unfiltered superset. "Visible tonight" is this
  // same data narrowed here, which is what makes the toggle instant. The
  // ordering mirrors engine.targets._brightness_key: solid targets first,
  // then too-faint ones, brightest within each half.
  const { filteredGroups, matchTotals, worthwhileTotals } = useMemo(() => {
    const groups: Record<string, TargetModel[]> = {};
    const totals: Record<string, number> = {};
    // Rows minus the ones badged too faint. The tab counts these rather than
    // every row: "visible tonight" renders nine thousand objects at an open
    // horizon and all but a few hundred are below what the sky will show, so
    // a row count is a true number that answers nobody's question.
    const worthwhile: Record<string, number> = {};
    if (targets) {
      for (const [key, allRows] of Object.entries(targets.groups)) {
        const popular = popularOnly
          ? allRows.filter((t) => t.showpiece)
          : allRows;
        const scoped = showAll
          ? popular
          : [...popular]
              .filter((t) => t.visible_tonight)
              .sort((a, b) => {
                if (a.too_faint !== b.too_faint) return a.too_faint ? 1 : -1;
                return (a.magnitude ?? 99) - (b.magnitude ?? 99);
              });
        const kept = query ? scoped.filter(matchesTarget) : scoped;
        if (kept.length > 0) {
          totals[key] = kept.length;
          worthwhile[key] = kept.filter((t) => !t.too_faint).length;
          // While searching, every matching group is expanded at once, so
          // an unbounded render is what made a broad query cost seconds.
          // Browsing one group by hand stays uncapped.
          groups[key] = query ? kept.slice(0, SEARCH_ROWS_PER_GROUP) : kept;
        }
      }
    }
    return { filteredGroups: groups, matchTotals: totals,
             worthwhileTotals: worthwhile };
  }, [targets, showAll, popularOnly, query, haystacks]);

  // What the tab counts: targets worth pointing at, under the filters
  // currently set. It used to be `total_passing`, a server-side count that
  // took no notice of the toggles below it and sat at the same number
  // whichever view was selected -- which reads as a broken filter rather than
  // as a different statistic.
  const shownCount = Object.values(worthwhileTotals)
    .reduce((total, count) => total + count, 0);

  const allMatchingGroups = Object.keys(filteredGroups);
  const groupNames = query
    ? allMatchingGroups.slice(0, SEARCH_GROUPS)
    : allMatchingGroups;
  const hiddenGroupCount = allMatchingGroups.length - groupNames.length;

  // A search opens every matching group, because a hit inside a collapsed
  // constellation is invisible and the search looks broken. But it opens
  // them -- it does not nail them open: an explicit click still wins, so a
  // 40-constellation match can be collapsed down to the one you wanted.
  // `openGroups` is keyed per group and only holds groups actually clicked,
  // so the default returns as soon as the query changes.
  //
  // Without a search, every group starts closed. The first used to open by
  // default, which meant whatever sorted first -- Andromeda, alphabetically
  // -- was always open, and it is no more likely to be the one you want.
  const isGroupOpen = (name: string) =>
    openGroups[name] ?? Boolean(query);

  const eventCount = events
    ? events.showers.length + events.lunar_eclipses.length + events.conjunctions.length
    : 0;

  return (
    <section className="panel sky-panel" aria-labelledby="sky-heading">
      <h2 id="sky-heading" className="visually-hidden">
        Deep sky, solar system and events
      </h2>
      <div className="tabs" role="tablist">
        <button
          role="tab"
          aria-selected={tab === "targets"}
          className={tab === "targets" ? "on" : ""}
          onClick={() => setTab("targets")}
        >
          Deep Sky{targets ? ` (${shownCount})` : ""}
        </button>
        <button
          role="tab"
          aria-selected={tab === "planets"}
          className={tab === "planets" ? "on" : ""}
          onClick={() => setTab("planets")}
        >
          Solar System{planets ? ` (${
            planets.planets.filter((p) => p.observable).length +
            (planets.moon?.observable ? 1 : 0)})` : ""}
        </button>
        <button
          role="tab"
          aria-selected={tab === "events"}
          className={tab === "events" ? "on" : ""}
          onClick={() => setTab("events")}
        >
          Events{events ? ` (${eventCount})` : ""}
        </button>
      </div>

      <div className="panel-search">
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={
            tab === "targets"
              ? "Search objects, types or constellations…"
              : tab === "planets"
                ? "Search the Moon and planets…"
                : "Search events…"
          }
          aria-label="Search this panel"
        />
        {query && (
          <button className="link-button" onClick={() => setSearch("")}>
            clear
          </button>
        )}
      </div>

      <div className={`tab-body ${isStale ? "stale" : ""}`}>
        {tab === "targets" && !targets && targetsPending && (
          <p className="muted" role="status">Computing tonight's deep-sky list…</p>
        )}

        {tab === "targets" && !targets && !targetsPending && (
          <p className="muted">No deep-sky list for this night.</p>
        )}

        {tab === "targets" && targets && (
          <>
            <div className="event-controls">
              <div className="segmented">
                <button
                  className={!showAll ? "on" : ""}
                  onClick={() => onShowAllChange(false)}
                  title="Only what is observable tonight"
                >
                  Visible tonight
                </button>
                <button
                  className={showAll ? "on" : ""}
                  onClick={() => onShowAllChange(true)}
                  title="Every catalogued object, badged by visibility"
                >
                  All objects
                </button>
              </div>
              {/* Composes with the toggle beside it rather than replacing it:
                  checked in "All objects" it is the whole showpiece list
                  whether or not tonight cooperates, and in "Visible tonight"
                  it is the part of that list you can actually point at. */}
              <label className="checkbox-field">
                <input
                  type="checkbox"
                  checked={popularOnly}
                  onChange={(e) => onPopularOnlyChange(e.target.checked)}
                />
                <span>Popular objects only</span>
              </label>
            </div>
            {/* The API returns this and the CLI prints it on every run; the
                web UI dropped it on the floor. It is the honesty note saying a
                horizon profile is a generic assumption, or that it is below
                the floor and changing nothing -- exactly the kind of caveat
                this project refuses to hide elsewhere. */}
            {targets.horizon_warning && (
              <p className="note">{targets.horizon_warning}</p>
            )}

            {query && groupNames.length === 0 && !isStale && (
              <p className="muted">Nothing matches “{search.trim()}”.</p>
            )}
            {hiddenGroupCount > 0 && (
              <p className="muted small">
                Showing the top {SEARCH_GROUPS} constellations;{" "}
                {hiddenGroupCount} more also match. Narrow the search to see them.
              </p>
            )}

            {groupNames.map((group) => {
              const rows = filteredGroups[group];
              const open = isGroupOpen(group);
              const info = targets.group_info?.[group];
              const label = info?.label ?? group;
              const isConstellation = info?.is_constellation ?? false;
              const onChart = charted.includes(group);

              return (
                <div key={group} className="group">
                  <div className="group-head-row">
                    {/* The constellation is the chartable unit now: everything
                        in it rises and sets together, so one curve answers
                        "when is this part of the sky up" for the whole list. */}
                    {isConstellation && (
                      <button
                        className={`chip ${onChart ? "chip-on" : ""}`}
                        onClick={() => onToggleChart(group, label, "constellation")}
                        title={
                          onChart
                            ? `Remove ${label} from the chart`
                            : `Chart ${label}`
                        }
                        // The visible text is "+" or a tick, which names
                        // nothing on its own; title is not a reliable
                        // accessible name, so state it explicitly.
                        aria-label={
                          onChart
                            ? `Remove ${label} from the chart`
                            : `Chart ${label}`
                        }
                        aria-pressed={onChart}
                      >
                        {onChart ? "✓" : "+"}
                      </button>
                    )}
                    <button
                      className="group-head"
                      onClick={() =>
                        setOpenGroups({ ...openGroups, [group]: !open })
                      }
                      aria-expanded={open}
                    >
                      <span className="caret">{open ? "▾" : "▸"}</span>
                      {label}
                      <span className="muted">({rows.length})</span>
                      {info?.is_constellation && (
                        <span
                          className={`badge badge-con-${info.visibility}`}
                          title={CONSTELLATION_MEANING[info.visibility]}
                        >
                          {info.visibility === "tonight"
                            ? "visible tonight"
                            : info.visibility === "late"
                              ? "visible late"
                              : "not visible"}
                          {/* After the label, matching the Badge helper, so
                              every badge reads "label - meaning". */}
                          <span className="visually-hidden">
                            {" "}— {CONSTELLATION_MEANING[info.visibility]}
                          </span>
                        </span>
                      )}
                      {/* The window is the actionable part: "up" matters far
                          less than "up between these hours". */}
                      {info?.window_start && info?.window_end && (
                        <span
                          className="muted small nowrap"
                          title={
                            `Above the altitude floor for ${info.hours_up.toFixed(1)} h` +
                            (info.peak_altitude_deg !== null
                              ? `, peaking at ${Math.round(info.peak_altitude_deg)}°`
                              : "") +
                            (info.has_gap
                              ? ". Dips below the floor and returns, so the window is not continuous."
                              : "")
                          }
                        >
                          {formatTime(info.window_start, timeZone)}–
                          {formatTime(info.window_end, timeZone)}
                          {info.peak_altitude_deg !== null &&
                            ` · peak ${Math.round(info.peak_altitude_deg)}°`}
                          {info.has_gap && (
                            <>
                              {" · gap"}
                              <span className="visually-hidden">
                                {" "}— dips below the floor and returns, so the
                                window is not continuous
                              </span>
                            </>
                          )}
                        </span>
                      )}
                      {info?.is_wide && (
                        <span
                          className="muted small"
                          title={`Spans about ${Math.round(info.spread_deg ?? 0)}°, so a single curve is a rough summary`}
                        >
                          wide
                          <span className="visually-hidden">
                            {" "}— spans about {Math.round(info.spread_deg ?? 0)}°,
                            so a single curve is a rough summary
                          </span>
                        </span>
                      )}
                    </button>
                  </div>

                  {open && (
                    <div className="table-scroll">
                      <table className="targets">
                        <thead>
                          <tr>
                            <th>Score</th>
                            <th>Object</th>
                            <th>Mag</th>
                            <th>Peak</th>
                            <th>Window</th>
                          </tr>
                        </thead>
                        <tbody>
                          {rows.map((target) => (
                            <TargetRow
                              key={target.name}
                              target={target}
                              timeZone={timeZone}
                              showAll={showAll}
                              onFinder={onOpenFinder && (() => onOpenFinder({
                                kind: "target", id: target.name,
                                label: target.display_name,
                                // Its best time tonight; for something not up
                                // during the session, the session's start.
                                at: target.peak_time ?? targets?.session?.start
                                    ?? new Date().toISOString(),
                              }))}
                            />
                          ))}
                        </tbody>
                      </table>
                      {query && (matchTotals[group] ?? 0) > rows.length && (
                        <p className="muted small">
                          Showing {rows.length} of {matchTotals[group]} matches
                          in {label}.
                        </p>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </>
        )}

        {tab === "planets" && planets && (
          <div className="table-scroll">
            <table className="targets">
              <thead>
                <tr>
                  <th aria-label="Chart" />
                  <th>Body</th>
                  <th>Mag</th>
                  <th>Size</th>
                  <th>Peak</th>
                  <th>Apparition</th>
                </tr>
              </thead>
              {/* The Moon first, in its own headed tbody: on most nights it
                  is either the target or the reason nothing else is. A second
                  group in the same table, so the columns stay aligned. */}
              {planets.moon && (!query || "moon".includes(query) ||
                moonPhaseName(planets.moon.illuminated_fraction,
                              planets.moon.waxing).includes(query)) && (
                <tbody>
                  <tr className="table-section">
                    <th colSpan={6} scope="rowgroup">Moon</th>
                  </tr>
                  <MoonRow
                    moon={planets.moon}
                    charted={charted}
                    onToggleChart={onToggleChart}
                    minAltitude={planets.min_altitude_deg}
                    timeZone={timeZone}
                    onFinder={onOpenFinder && (() => onOpenFinder({
                      kind: "body", id: "moon", label: "Moon",
                      at: planets.moon!.peak_time ?? new Date().toISOString(),
                    }))}
                  />
                </tbody>
              )}
              <tbody>
                {/* Headed only when the Moon is above it -- otherwise its
                    rows would read as belonging to the Moon's heading. */}
                {planets.moon && (
                  <tr className="table-section">
                    <th colSpan={6} scope="rowgroup">Planets</th>
                  </tr>
                )}
                {planets.planets
                  .filter((planet) =>
                    !query || planet.name.toLowerCase().includes(query) ||
                    planet.trend.toLowerCase().includes(query))
                  .map((planet) => (
                    <PlanetRow
                      key={planet.name}
                      planet={planet}
                      charted={charted}
                      onToggleChart={onToggleChart}
                      minAltitude={planets.min_altitude_deg}
                      onFinder={onOpenFinder && (() => onOpenFinder({
                        kind: "body", id: planet.name, label: titleCase(planet.name),
                        at: planet.peak_time ?? new Date().toISOString(),
                      }))}
                    />
                ))}
              </tbody>
            </table>
          </div>
        )}

        {tab === "events" && (
          <>
            <div className="event-controls">
              <span className="muted small">Looking ahead</span>
              <div className="segmented">
                {EVENT_HORIZONS.map((days) => (
                  <button
                    key={days}
                    className={eventDays === days ? "on" : ""}
                    onClick={() => onEventDaysChange(days)}
                  >
                    {days === 365 ? "1 year" : `${days} days`}
                  </button>
                ))}
              </div>
            </div>

            {!events && <p className="muted">Loading…</p>}

            {events && (
              <>
                <h4>Meteor showers</h4>
                {events.showers.length === 0 ? (
                  <p className="muted">None peaking in this window.</p>
                ) : (
                  <div className="table-scroll">
                    <table className="targets">
                      <tbody>
                        {events.showers
                          .filter((s) => !query ||
                            s.name.toLowerCase().includes(query) ||
                            (s.parent ?? "").toLowerCase().includes(query))
                          .map((shower) => (
                          <tr key={shower.code}>
                            <td className="nowrap">{formatDate(shower.peak_date)}</td>
                            <td>
                              <strong>{shower.name}</strong>
                              {shower.notes.length > 0 && (
                                <div className="target-notes">
                                  {shower.notes.join(" · ")}
                                </div>
                              )}
                            </td>
                            <td className="nowrap">
                              <strong>
                                ~{Math.round(shower.estimated_rate_per_hour)}/h
                              </strong>
                              <div className="target-notes">ZHR {shower.zhr}</div>
                            </td>
                            <td className="nowrap">
                              {formatTime(shower.best_time, timeZone)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <h4>Lunar eclipses</h4>
                {events.lunar_eclipses.length === 0 ? (
                  <p className="muted">None in this window.</p>
                ) : (
                  <div className="table-scroll">
                    <table className="targets">
                      <tbody>
                        {events.lunar_eclipses
                          .filter((e) => !query ||
                            e.kind.toLowerCase().includes(query) ||
                            "lunar eclipse".includes(query))
                          .map((eclipse) => (
                          <tr
                            key={eclipse.time}
                            className={eclipse.visible_from_location ? undefined : "dim"}
                          >
                            <td className="nowrap">
                              {formatDate(eclipse.time, timeZone)}
                            </td>
                            <td className="nowrap">
                              {formatTime(eclipse.time, timeZone)}
                            </td>
                            <td>
                              <strong>{eclipse.kind}</strong>
                            </td>
                            <td>
                              {eclipse.visible_from_location ? (
                                <span className="tag">visible here</span>
                              ) : (
                                <span className="muted">below horizon here</span>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                <h4>Conjunctions</h4>
                {events.conjunctions.length === 0 ? (
                  <p className="muted">None under the thresholds.</p>
                ) : (
                  <div className="table-scroll">
                    <table className="targets">
                      <tbody>
                        {events.conjunctions
                          .filter((c) => !query ||
                            c.body_a.toLowerCase().includes(query) ||
                            c.body_b.toLowerCase().includes(query))
                          .map((conjunction) => (
                          <tr
                            key={`${conjunction.time}-${conjunction.body_a}-${conjunction.body_b}`}
                          >
                            <td className="nowrap">
                              {formatDate(conjunction.time, timeZone)}
                            </td>
                            <td>
                              <strong>
                                {titleCase(conjunction.body_a)} &amp;{" "}
                                {titleCase(conjunction.body_b)}
                              </strong>
                            </td>
                            <td className="nowrap">
                              {conjunction.separation_deg.toFixed(2)}°
                            </td>
                            <td className="muted">
                              {conjunction.separation_deg < 1
                                ? "same low-power field"
                                : "naked-eye pairing"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <p className="muted small">
                  Planet pairs under 5°, Moon–planet pairs under 3°.
                </p>
              </>
            )}
          </>
        )}
      </div>
    </section>
  );
}
