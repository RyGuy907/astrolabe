/**
 * One panel, three tabs: Targets, Planets, Events.
 *
 * These were three separate boxes. Folding them into a tab strip keeps the
 * dashboard to a handful of regions and lets this column sit beside the
 * altitude chart, so you can add something to the chart and see it appear
 * without scrolling.
 */

import { useDeferredValue, useMemo, useState } from "react";
import { TargetImage } from "./TargetImage";
import type {
  EventsResponse,
  PlanetsResponse,
  TargetModel,
  TargetsResponse,
} from "../api";
import {
  formatDate,
  formatDegrees,
  formatMagnitude,
  formatTime,
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
  /** True while /api/targets is in flight. It is much slower than the other
   *  calls, so the tab needs to say so rather than looking empty. */
  targetsPending: boolean;
}

const EVENT_HORIZONS = [30, 90, 365];

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
  tonight: "Clears the altitude floor before midnight",
  late: "Only clears the altitude floor after midnight",
  none: "Never clears the altitude floor tonight",
};

function TargetRow({ target, timeZone, showAll }: {
  target: TargetModel;
  timeZone: string;
  showAll: boolean;
}) {
  // Per row, and closed by default. The image is only requested once a row is
  // opened, so browsing a 270-row list costs nothing.
  const [open, setOpen] = useState(false);
  const designation = target.messier ? `M${target.messier}` : target.name;
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
        <span className="muted"> · {target.object_type}</span>
        {/* "visible late" is the badge that changes plans, so it shows in
            both modes. "visible tonight" only earns space in the unfiltered
            view, where it separates rows from the ones that never come up. */}
        {target.visible_late && (
          <Badge
            kind="late"
            meaning="Only clears the altitude floor after local midnight"
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
      <td className="muted">{designation}</td>
    </tr>
    {open && (
      <tr className="target-detail">
        <td colSpan={6}>
          <TargetImage
            name={target.display_name}
            raDeg={target.ra_deg}
            decDeg={target.dec_deg}
            sizeArcmin={target.size_arcmin}
          />
        </td>
      </tr>
    )}
    </>
  );
}

export function SkyPanel({
  targets, planets, events, timeZone, charted, onToggleChart,
  eventDays, onEventDaysChange, showAll, onShowAllChange, targetsPending,
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
  const { filteredGroups, matchTotals } = useMemo(() => {
    const groups: Record<string, TargetModel[]> = {};
    const totals: Record<string, number> = {};
    if (targets) {
      for (const [key, allRows] of Object.entries(targets.groups)) {
        const scoped = showAll
          ? allRows
          : [...allRows]
              .filter((t) => t.visible_tonight)
              .sort((a, b) => {
                if (a.too_faint !== b.too_faint) return a.too_faint ? 1 : -1;
                return (a.magnitude ?? 99) - (b.magnitude ?? 99);
              });
        const kept = query ? scoped.filter(matchesTarget) : scoped;
        if (kept.length > 0) {
          totals[key] = kept.length;
          // While searching, every matching group is expanded at once, so
          // an unbounded render is what made a broad query cost seconds.
          // Browsing one group by hand stays uncapped.
          groups[key] = query ? kept.slice(0, SEARCH_ROWS_PER_GROUP) : kept;
        }
      }
    }
    return { filteredGroups: groups, matchTotals: totals };
  }, [targets, showAll, query, haystacks]);

  const visibleCount = targets
    ? Object.values(targets.groups)
        .flat()
        .filter((t) => t.visible_tonight && !t.too_faint).length
    : 0;

  const allMatchingGroups = Object.keys(filteredGroups);
  const groupNames = query
    ? allMatchingGroups.slice(0, SEARCH_GROUPS)
    : allMatchingGroups;
  const hiddenGroupCount = allMatchingGroups.length - groupNames.length;
  const matchCount = Object.values(matchTotals)
    .reduce((total, count) => total + count, 0);

  // While searching, open every group that matched — otherwise a hit inside a
  // collapsed constellation is invisible and the search looks broken.
  const isGroupOpen = (name: string, index: number) =>
    query ? true : (openGroups[name] ?? index === 0);

  const eventCount = events
    ? events.showers.length + events.lunar_eclipses.length + events.conjunctions.length
    : 0;

  return (
    <section className="panel sky-panel" aria-labelledby="sky-heading">
      <h2 id="sky-heading" className="visually-hidden">
        Targets, planets and events
      </h2>
      <div className="tabs" role="tablist">
        <button
          role="tab"
          aria-selected={tab === "targets"}
          className={tab === "targets" ? "on" : ""}
          onClick={() => setTab("targets")}
        >
          Targets{targets ? ` (${targets.total_passing})` : ""}
        </button>
        <button
          role="tab"
          aria-selected={tab === "planets"}
          className={tab === "planets" ? "on" : ""}
          onClick={() => setTab("planets")}
        >
          Planets{planets ? ` (${planets.planets.filter((p) => p.observable).length})` : ""}
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
                ? "Search planets…"
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
          <p className="muted" role="status">
            Computing tonight's targets… this one takes a few seconds — every
            catalogued object is sampled across the night.
          </p>
        )}

        {tab === "targets" && !targets && !targetsPending && (
          <p className="muted">No target list for this night.</p>
        )}

        {tab === "targets" && targets && (
          <>
            <div className="event-controls">
              <span className="muted small">
                by constellation · floor {targets.min_altitude_deg}° ·{" "}
                {targets.using_true_dark ? "true dark" : "moon up part of night"}
              </span>
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
                  All targets
                </button>
              </div>
            </div>
            <p className="muted small tab-note">
              + charts the whole region. Brightest first
              {showAll
                ? ", every catalogued object including ones not up tonight."
                : `. ${visibleCount} recommended, ` +
                  `${targets.total_too_faint} up but too faint for this sky.`}
              {query && ` ${matchCount} match${matchCount === 1 ? "" : "es"}.`}
            </p>

            {/* The API returns this and the CLI prints it on every run; the
                web UI dropped it on the floor. It is the honesty note saying a
                horizon profile is a generic assumption, or that it is below
                the floor and changing nothing -- exactly the kind of caveat
                this project refuses to hide elsewhere. */}
            {targets.horizon_warning && (
              <p className="note">{targets.horizon_warning}</p>
            )}

            {/* A visible key, so the badges can be decoded without a mouse.
                Repeating each explanation on all 200-odd rows would bury the
                table; saying it once will not. */}
            <p className="muted small badge-key">
              <span className="badge badge-late">visible late</span> only clears
              the floor after midnight ·{" "}
              <span className="badge badge-faint">too faint</span> up, but below
              what this sky will show ·{" "}
              <span className="muted small">wide</span> spans enough sky that one
              curve is a rough summary ·{" "}
              <span className="muted small">gap</span> dips below the floor and
              returns, so the window is not continuous
            </p>

            {query && groupNames.length === 0 && !isStale && (
              <p className="muted">Nothing matches “{search.trim()}”.</p>
            )}
            {hiddenGroupCount > 0 && (
              <p className="muted small">
                Showing the top {SEARCH_GROUPS} constellations;{" "}
                {hiddenGroupCount} more also match. Narrow the search to see them.
              </p>
            )}

            {groupNames.map((group, index) => {
              const rows = filteredGroups[group];
              const open = isGroupOpen(group, index);
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
                            <th>Best window</th>
                            <th>ID</th>
                          </tr>
                        </thead>
                        <tbody>
                          {rows.map((target) => (
                            <TargetRow
                              key={target.name}
                              target={target}
                              timeZone={timeZone}
                              showAll={showAll}
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
                  <th>Planet</th>
                  <th>Mag</th>
                  <th>Size</th>
                  <th>Peak</th>
                  <th>Apparition</th>
                </tr>
              </thead>
              <tbody>
                {planets.planets
                  .filter((planet) =>
                    !query || planet.name.toLowerCase().includes(query) ||
                    planet.trend.toLowerCase().includes(query))
                  .map((planet) => (
                  <tr
                    key={planet.name}
                    className={planet.observable ? undefined : "dim"}
                  >
                    <td>
                      <button
                        className={`chip ${charted.includes(planet.name) ? "chip-on" : ""}`}
                        onClick={() =>
                          onToggleChart(planet.name, planet.name, "body")
                        }
                        title="Toggle on the altitude chart"
                        aria-label={
                          charted.includes(planet.name)
                            ? `Remove ${titleCase(planet.name)} from the chart`
                            : `Chart ${titleCase(planet.name)}`
                        }
                        aria-pressed={charted.includes(planet.name)}
                      >
                        {charted.includes(planet.name) ? "✓" : "+"}
                      </button>
                    </td>
                    <td>
                      <strong>{titleCase(planet.name)}</strong>
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
                          next above {planets.min_altitude_deg}° on{" "}
                          {formatDate(planet.next_visible_date)}
                        </div>
                      )}
                      {!planet.observable && planet.best_altitude_deg !== null && (
                        <div className="target-notes">
                          never clears {planets.min_altitude_deg}° within a year;
                          best {planet.best_altitude_deg.toFixed(0)}°
                        </div>
                      )}
                    </td>
                  </tr>
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
                <p className="muted small">
                  Estimated rate accounts for radiant altitude, sky brightness and
                  moonlight, so it sits well below the quoted ZHR.
                </p>

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
