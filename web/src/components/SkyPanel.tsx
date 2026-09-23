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
 *
 * **One target open at a time.** Opening a target -- its details, or its sky
 * chart -- closes whatever target and constellation were open before, so the
 * list never fills with a trail of dropdowns. Where the layout is one column
 * the chart sits in the list, directly above its target's row (see
 * `FinderSlot`), and the three read as one block: chart, row, details. Once a
 * chart is open it follows: opening another target's details moves it there,
 * and picking an object on the chart moves the block to that object's row,
 * keeping the chart where it was on screen. Every target opened either way
 * goes into the history.
 */

import {
  useDeferredValue, useEffect, useLayoutEffect, useMemo, useRef, useState,
} from "react";
import type { FinderSubject } from "./FinderChart";
import { FinderSlot } from "./FinderSlot";
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
import { history } from "../history";
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

/** An object picked on the chart or suggested: open its row. `seq` changes
 *  on every request, so the same object twice works. `keepTop` is where the
 *  chart was on screen when the pick was made, so an inline chart moving to
 *  the new row can stay put under the finger that picked. */
export interface Reveal {
  kind: "target" | "body";
  id: string;
  seq: number;
  keepTop?: number;
}

interface Props {
  targets: TargetsResponse | null;
  planets: PlanetsResponse | null;
  events: EventsResponse | null;
  /** Why events failed to load, if they did. */
  eventsError?: string | null;
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
  /** The sky chart's subject, if a chart is open. */
  finder: FinderSubject | null;
  /** The chart is on screen now, not just open behind the altitude tab. */
  finderShown: boolean;
  /** One-column layout: the chart goes in the list, above its row. */
  chartInline: boolean;
  onOpenFinder: (subject: FinderSubject) => void;
  onCloseFinder: () => void;
  /** A row's details were opened. An open chart follows it there. */
  onFocus: (subject: FinderSubject) => void;
  reveal?: Reveal | null;
  /** The night and site a history entry is filed under. */
  night: string;
  siteKey: string;
  siteName: string;
}

/** The nearest ancestor that scrolls vertically, or null when it is the page. */
function scrollBox(el: HTMLElement): HTMLElement | null {
  for (let p = el.parentElement; p; p = p.parentElement) {
    const overflow = getComputedStyle(p).overflowY;
    if ((overflow === "auto" || overflow === "scroll") && p.scrollHeight > p.clientHeight) {
      return p;
    }
  }
  return null;
}

function viewOf(box: HTMLElement | null) {
  return box
    ? box.getBoundingClientRect()
    : { top: 0, bottom: window.innerHeight, height: window.innerHeight };
}

/**
 * Bring a row and its open dropdown into view together, scrolling only the
 * list's own box. `scrollIntoView` would scroll every ancestor, the page
 * included, dragging the chart on the left down with it.
 *
 * `center` puts the pair in the middle of the box -- for an object picked on
 * the chart, which may be anywhere in the list. Otherwise it moves only as
 * far as it must: opening a row you just clicked shouldn't throw it about.
 * Either way, a pair taller than the box shows from its top.
 *
 * A move of more than about a box's height is made at once, not animated: a
 * smooth scroll across thousands of pixels of list is slow and blurs past
 * rows the eye can't read anyway, and the row's flash says where it landed.
 * Short moves, where the eye can follow, glide.
 */
function bringIntoView(row: HTMLTableRowElement, center: boolean) {
  const detail = row.nextElementSibling instanceof HTMLElement &&
    row.nextElementSibling.classList.contains("target-detail")
    ? row.nextElementSibling : null;
  const top = row.getBoundingClientRect().top;
  const bottom = (detail ?? row).getBoundingClientRect().bottom;
  const box = scrollBox(row);
  const view = viewOf(box);
  const margin = 12;
  const room = view.bottom - view.top - 2 * margin;
  let delta: number;
  if (bottom - top > room) delta = top - (view.top + margin);
  else if (center) delta = (top + bottom) / 2 - (view.top + view.bottom) / 2;
  else if (top < view.top + margin) delta = top - (view.top + margin);
  else if (bottom > view.bottom - margin) delta = bottom - (view.bottom - margin);
  else return;
  if (Math.abs(delta) < 1) return;
  const behavior: ScrollBehavior = Math.abs(delta) > view.bottom - view.top ? "instant" : "smooth";
  if (box) box.scrollBy({ top: delta, behavior });
  else window.scrollBy({ top: delta, behavior });
}

/** Centre a revealed row, and keep it centred for a moment while its dropdown
 *  settles (reference facts or an image arriving late) -- until the user
 *  scrolls, which always wins. Returns a cleanup. */
function centreWhileSettling(row: HTMLTableRowElement): () => void {
  bringIntoView(row, true);
  const detail = row.nextElementSibling;
  const box = scrollBox(row) ?? window;
  let last = detail?.getBoundingClientRect().height ?? 0;
  const observer = new ResizeObserver(() => {
    const now = detail?.getBoundingClientRect().height ?? 0;
    if (Math.abs(now - last) > 2) { last = now; bringIntoView(row, true); }
  });
  if (detail) observer.observe(detail);
  const stop = () => {
    observer.disconnect();
    window.clearTimeout(timer);
    for (const type of ["wheel", "touchstart", "keydown", "pointerdown"]) {
      box.removeEventListener(type, stop);
    }
  };
  const timer = window.setTimeout(stop, 2500);
  for (const type of ["wheel", "touchstart", "keydown", "pointerdown"]) {
    box.addEventListener(type, stop, { passive: true });
  }
  return stop;
}

/**
 * Put an inline chart, and the row under it, on screen.
 *
 * After a pick on the chart (`keepTop` set) the chart goes back exactly
 * where it was: it has moved in the page -- to another row, maybe another
 * constellation, with the old one folded away above it -- and without this
 * the finger that picked would be left over whatever slid in underneath.
 * Everything else about the block is below the chart, so it never shifts it.
 *
 * Otherwise, opened from the list or suggested: the chart's top goes to the
 * top of the screen, unless the chart and its row are on screen already. At
 * once for a long way, gliding for a short one, as for a row.
 */
function anchorChart(chartRow: HTMLElement, row: HTMLElement, keepTop?: number) {
  const box = scrollBox(chartRow);
  const scroller = box ?? window;
  if (keepTop !== undefined) {
    const slot = chartRow.querySelector(".finder-slot") ?? chartRow;
    const delta = slot.getBoundingClientRect().top - keepTop;
    if (Math.abs(delta) >= 1) scroller.scrollBy({ top: delta, behavior: "instant" });
    return;
  }
  const view = viewOf(box);
  const margin = 8;
  const top = chartRow.getBoundingClientRect().top;
  const bottom = row.getBoundingClientRect().bottom;
  if (top >= view.top + margin && bottom <= view.bottom - margin) return;
  const delta = top - (view.top + margin);
  if (Math.abs(delta) < 1) return;
  scroller.scrollBy({
    top: delta,
    behavior: Math.abs(delta) > view.bottom - view.top ? "instant" : "smooth",
  });
}

const EVENT_HORIZONS = [30, 90, 365];

/** Opens this row's sky chart -- beside the lists, or above the row where
 *  the layout is one column -- and closes it again. */
function FinderButton({ label, on, onClick }: {
  label: string; on: boolean; onClick: () => void;
}) {
  return (
    <button className={`finder-open ${on ? "on" : ""}`} onClick={onClick}
            title={on ? "Close the sky chart" : "Sky chart, for star hopping"}
            aria-label={on ? `Close the sky chart for ${label}` : `Sky chart for ${label}`}
            aria-pressed={on}>
      <svg viewBox="0 0 16 16" aria-hidden="true">
        <circle cx="8" cy="8" r="5.2" />
        <path d="M8 0.8v3.6M8 11.6v3.6M0.8 8h3.6M11.6 8h3.6" />
      </svg>
    </button>
  );
}

/** The inline chart's place in a table: a full-width row above the
 *  object's own, headed so it can be closed from where it is. */
function ChartRow({ span, label, onClose }: {
  span: number; label: string; onClose: () => void;
}) {
  return (
    <tr className="finder-slot-row">
      <td colSpan={span}>
        <div className="finder-inline">
          <div className="finder-inline-head">
            <span className="finder-inline-title">Sky chart</span>
            <button className="finder-inline-close" onClick={onClose}
                    aria-label={`Close the sky chart for ${label}`}>
              Close <span aria-hidden="true">×</span>
            </button>
          </div>
          <FinderSlot kind="inline" />
        </div>
      </td>
    </tr>
  );
}

// Search expands every matching group at once, so both of these are render
// bounds rather than result limits: the counts shown are always the true
// totals, only the rows drawn are capped.
const SEARCH_ROWS_PER_GROUP = 25;
const SEARCH_GROUPS = 15;
/** Constellations shown before the list folds, where it is part of the page. */
const GROUPS_FOLDED = 8;

/**
 * A short label whose meaning is not hover-only.
 *
 * These badges carried their explanation in a `title` alone -- 223 of them on
 * a typical page. A title is invisible on touch, never shown to a keyboard
 * user, and unreliable as an accessible name, so "too faint" was a word with
 * no way to find out what it meant unless you had a mouse. The label stays
 * visible and compact; the meaning goes in text that assistive technology
 * reads and the layout ignores. `title` is kept as the mouse affordance.
 *
 * On touch there is no hover, and a key under the table this once relied on
 * was removed with it -- so the badge is focusable, and tapping or tabbing to
 * it shows its meaning just below it (`.badge[data-meaning]:focus` in the
 * stylesheet).
 */
function Badge({ kind, meaning, children }: {
  kind: string;
  meaning: string;
  children: React.ReactNode;
}) {
  return (
    <span className={`badge badge-${kind}`} title={meaning}
          tabIndex={0} data-meaning={meaning}>
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

/** What every openable row is told by the panel that owns its state. */
interface RowState {
  /** Its details are open. */
  open: boolean;
  onToggle: () => void;
  /** Just revealed: marked for a moment so the eye finds it. */
  flash: boolean;
  rowRef: (el: HTMLTableRowElement | null) => void;
  /** Its sky chart is showing. */
  finderOn: boolean;
  onFinder: () => void;
  /** Its chart is inline, in the row above it. */
  chartHere: boolean;
  onCloseChart: () => void;
}

function rowClass(dim: boolean, state: RowState) {
  return [dim ? "dim" : "", state.flash ? "revealed" : "",
          state.chartHere ? "has-chart" : "", state.open ? "is-open" : ""]
    .filter(Boolean).join(" ") || undefined;
}

function TargetRow({ target, timeZone, showAll, state }: {
  target: TargetModel;
  timeZone: string;
  showAll: boolean;
  state: RowState;
}) {
  // Closed by default. The image is only requested once a row is opened, so
  // browsing a 270-row list costs nothing.
  const { open } = state;
  //: Component magnitudes are only set for the curated doubles, and a couple
  //: of the usual rows do not apply to a pair of stars.
  const isDouble = target.component_mags !== null;
  return (
    <>
    {state.chartHere && (
      <ChartRow span={5} label={target.display_name} onClose={state.onCloseChart} />
    )}
    <tr ref={state.rowRef}
        className={rowClass(!target.visible_tonight || target.too_faint, state)}>
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
          onClick={state.onToggle}
          aria-expanded={open}
          title={open ? "Hide the details" : "Show the details"}
        >
          <span className="caret">{open ? "▾" : "▸"}</span>
          <strong>{target.display_name}</strong>
        </button>
        <FinderButton label={target.display_name} on={state.finderOn}
                      onClick={state.onFinder} />
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
function PlanetRow({ planet, charted, onToggleChart, minAltitude, state }: {
  planet: PlanetModel;
  charted: string[];
  onToggleChart: (id: string, label: string,
                  kind: "body" | "constellation") => void;
  minAltitude: number;
  state: RowState;
}) {
  const { open } = state;
  const onChart = charted.includes(planet.name);
  const facts = planet.facts;

  return (
    <>
      {state.chartHere && (
        <ChartRow span={6} label={titleCase(planet.name)} onClose={state.onCloseChart} />
      )}
      <tr ref={state.rowRef} className={rowClass(!planet.observable, state)}>
        <td>
          <button
            className={`chip ${onChart ? "chip-on" : ""}`}
            onClick={() => onToggleChart(planet.name, planet.name, "body")}
            title="Toggle on the altitude chart"
            aria-label={
              onChart
                ? `Remove ${titleCase(planet.name)} from the altitude chart`
                : `Add ${titleCase(planet.name)} to the altitude chart`
            }
            aria-pressed={onChart}
          >
            {onChart ? "✓" : "+"}
          </button>
        </td>
        <td>
          <button
            className="target-name"
            onClick={state.onToggle}
            aria-expanded={open}
            title={open ? "Hide the details" : "Show the details"}
          >
            <span className="caret">{open ? "▾" : "▸"}</span>
            <strong>{titleCase(planet.name)}</strong>
          </button>
          <FinderButton label={titleCase(planet.name)} on={state.finderOn}
                        onClick={state.onFinder} />
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
function MoonRow({ moon, charted, onToggleChart, minAltitude, timeZone, state }: {
  moon: MoonModel;
  charted: string[];
  onToggleChart: (id: string, label: string,
                  kind: "body" | "constellation") => void;
  minAltitude: number;
  timeZone: string;
  state: RowState;
}) {
  const { open } = state;
  const onChart = charted.includes("moon");
  const phase = moonPhaseName(moon.illuminated_fraction, moon.waxing);
  const facts = moon.facts;

  return (
    <>
      {state.chartHere && (
        <ChartRow span={6} label="the Moon" onClose={state.onCloseChart} />
      )}
      <tr ref={state.rowRef} className={rowClass(!moon.observable, state)}>
        <td>
          <button
            className={`chip ${onChart ? "chip-on" : ""}`}
            onClick={() => onToggleChart("moon", "moon", "body")}
            title="Toggle on the altitude chart"
            aria-label={onChart
              ? "Remove the Moon from the altitude chart"
              : "Add the Moon to the altitude chart"}
            aria-pressed={onChart}
          >
            {onChart ? "✓" : "+"}
          </button>
        </td>
        <td>
          <button
            className="target-name"
            onClick={state.onToggle}
            aria-expanded={open}
            title={open ? "Hide the details" : "Show the details"}
          >
            <span className="caret">{open ? "▾" : "▸"}</span>
            <strong>Moon</strong>
          </button>
          <FinderButton label="the Moon" on={state.finderOn} onClick={state.onFinder} />
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

const rowKey = (kind: "target" | "body", id: string) => `${kind}:${id}`;

/** Where the list should scroll once a row it opened has rendered. */
interface PendingScroll {
  key: string;
  how: "center" | "nearest" | "chart";
  keepTop?: number;
}

export function SkyPanel({
  targets, planets, events, eventsError, timeZone, charted, onToggleChart,
  eventDays, onEventDaysChange, showAll, onShowAllChange,
  popularOnly, onPopularOnlyChange, targetsPending,
  finder, finderShown, chartInline, onOpenFinder, onCloseFinder, onFocus, reveal,
  night, siteKey, siteName,
}: Props) {
  const [tab, setTab] = useState<Tab>("targets");
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});
  const [search, setSearch] = useState("");
  //: The one row whose details are open, as `rowKey` makes it.
  const [openRow, setOpenRow] = useState<string | null>(null);
  //: The row just revealed from the chart, marked for a moment.
  const [flashKey, setFlashKey] = useState<string | null>(null);

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

  // Which constellation each object is filed under, for opening and closing
  // groups around a target.
  const groupOf = useMemo(() => {
    const map = new Map<string, string>();
    for (const [group, rows] of Object.entries(targets?.groups ?? {})) {
      for (const target of rows) map.set(target.name, group);
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
  //
  // Without a search, one constellation is open at a time, and every group
  // starts closed. The first used to open by default, which meant whatever
  // sorted first -- Andromeda, alphabetically -- was always open, and it is
  // no more likely to be the one you want.
  const isGroupOpen = (name: string) =>
    openGroups[name] ?? Boolean(query);

  // Where the lists are part of the page (one column), all 88 constellations
  // stood between the chart and everything below them -- the history was a
  // long scroll away. So the list folds to the first few, which are the best
  // placed tonight, with a button for the rest. An open constellation always
  // shows, wherever it sorts, so a chart or a pick is never folded away. On
  // a wide screen the list is a box of its own and nothing needs folding.
  const [allGroupsShown, setAllGroupsShown] = useState(false);
  const foldable = chartInline && !query && groupNames.length > GROUPS_FOLDED + 2;
  const shownGroups = foldable && !allGroupsShown
    ? groupNames.filter((group, i) => i < GROUPS_FOLDED || isGroupOpen(group))
    : groupNames;
  // Folding the list back up from its foot would leave the page far below
  // it; keep the button where it was on screen instead.
  const foldButton = useRef<HTMLButtonElement>(null);
  const foldFrom = useRef<number | null>(null);
  useLayoutEffect(() => {
    const top = foldFrom.current;
    const button = foldButton.current;
    foldFrom.current = null;
    if (top === null || !button) return;
    const delta = button.getBoundingClientRect().top - top;
    if (Math.abs(delta) >= 1) window.scrollBy({ top: delta, behavior: "instant" });
  }, [allGroupsShown]);
  function toggleFold() {
    if (allGroupsShown) foldFrom.current = foldButton.current?.getBoundingClientRect().top ?? null;
    setAllGroupsShown(!allGroupsShown);
  }

  // --- the history ---
  const record = (kind: "target" | "body", id: string, label: string,
                  detail: string | null) => {
    if (!night || !siteKey) return;
    history.record({ night, kind, objectId: id, label, detail,
                     siteKey, siteName, timeZone });
  };
  const recordTarget = (target: TargetModel) =>
    record("target", target.name, target.display_name,
           [target.object_type, target.constellation].filter(Boolean).join(" · ") || null);
  const recordBody = (name: string) =>
    record("body", name, name === "moon" ? "Moon" : titleCase(name),
           name === "moon" ? "Moon" : "Planet");

  // --- what a chart opened from a row is centred on ---
  const targetSubject = (target: TargetModel): FinderSubject => ({
    kind: "target", id: target.name, label: target.display_name,
    ra: target.ra_deg, dec: target.dec_deg,
    // Its best time tonight; for something not up during the session, the
    // session's start.
    at: target.peak_time ?? targets?.session?.start ?? new Date().toISOString(),
  });
  const bodySubject = (name: string, peak: string | null): FinderSubject => ({
    kind: "body", id: name, label: name === "moon" ? "Moon" : titleCase(name),
    at: peak ?? new Date().toISOString(),
  });

  // --- opening and closing rows ---
  const finderKey = finder ? rowKey(finder.kind, finder.id) : null;
  const pendingScroll = useRef<PendingScroll | null>(null);
  const settling = useRef<(() => void) | null>(null);
  useEffect(() => () => settling.current?.(), []);

  // Each rendered row's element, by key, for scrolling to once it opens.
  const rowEls = useRef(new Map<string, HTMLTableRowElement>());
  const rowRefs = useRef(new Map<string, (el: HTMLTableRowElement | null) => void>());
  const rowRefFor = (key: string) => {
    let ref = rowRefs.current.get(key);
    if (!ref) {
      ref = (el) => {
        if (el) rowEls.current.set(key, el);
        else rowEls.current.delete(key);
      };
      rowRefs.current.set(key, ref);
    }
    return ref;
  };

  /** Open this group alone -- unless a search has every match open. */
  const focusGroup = (group: string | undefined) => {
    if (group && !query) setOpenGroups({ [group]: true });
  };

  /** A row's name: open its details, closing any others. An open chart
   *  follows it. */
  function toggleDetails(key: string, group: string | undefined,
                         subject: FinderSubject, log: () => void) {
    if (openRow === key) {
      setOpenRow(null);
      return;
    }
    setOpenRow(key);
    focusGroup(group);
    const chartMoves = finder !== null && finderKey !== key;
    pendingScroll.current = {
      key, how: chartInline && finder ? "chart" : "nearest",
    };
    log();
    if (chartMoves) onFocus(subject);
  }

  /** A row's chart button: open its chart -- closing any other target's
   *  details -- or close the chart if it is already this row's. */
  function toggleChart(key: string, group: string | undefined,
                       subject: FinderSubject, log: () => void) {
    if (finderKey === key && finderShown) {
      onCloseFinder();
      return;
    }
    if (openRow !== null && openRow !== key) setOpenRow(null);
    focusGroup(group);
    if (chartInline) pendingScroll.current = { key, how: "chart" };
    log();
    onOpenFinder(subject);
  }

  /** A constellation's heading. Opening one closes the one before; closing
   *  one closes what was open inside it -- and a chart in the list with it,
   *  since the chart lives above its row. */
  function toggleGroup(group: string) {
    const open = isGroupOpen(group);
    const next = query ? { ...openGroups, [group]: !open } : open ? {} : { [group]: true };
    setOpenGroups(next);
    const stillOpen = (g: string | undefined) => !!g && (next[g] ?? Boolean(query));
    if (openRow?.startsWith("target:") && !stillOpen(groupOf.get(openRow.slice(7)))) {
      setOpenRow(null);
    }
    if (chartInline && finder?.kind === "target" && !stillOpen(groupOf.get(finder.id))) {
      onCloseFinder();
    }
  }

  function rowState(kind: "target" | "body", id: string, group: string | undefined,
                    subject: () => FinderSubject, log: () => void): RowState {
    const key = rowKey(kind, id);
    return {
      open: openRow === key,
      onToggle: () => toggleDetails(key, group, subject(), log),
      flash: flashKey === key,
      rowRef: rowRefFor(key),
      finderOn: finderKey === key && finderShown,
      onFinder: () => toggleChart(key, group, subject(), log),
      chartHere: chartInline && finderKey === key,
      onCloseChart: onCloseFinder,
    };
  }

  // After the render that drew what was opened, so its height counts, and
  // before the browser paints it: a layout effect, so the list is never seen
  // in between -- the row opening (and the last one closing above it) and
  // the scroll that follows land in one frame, not a jump and then a glide.
  // Runs after every render and consumes the request once its row exists.
  // Declared before the reveal effect below, so a reveal's scroll waits for
  // the render its state changes cause rather than measuring the one before.
  useLayoutEffect(() => {
    const pending = pendingScroll.current;
    if (!pending) return;
    const row = rowEls.current.get(pending.key);
    if (!row) return;
    pendingScroll.current = null;
    settling.current?.();
    settling.current = null;
    const above = row.previousElementSibling;
    const chartRow = above instanceof HTMLElement &&
      above.classList.contains("finder-slot-row") ? above : null;
    if (pending.how === "chart" && chartRow) anchorChart(chartRow, row, pending.keepTop);
    else if (pending.how === "nearest") bringIntoView(row, false);
    else settling.current = centreWhileSettling(row);
  });

  // An object clicked on the sky chart, or suggested: its tab, its group
  // open, the filters loosened if they are what is hiding it, and its
  // details open. A layout effect, so all of that renders before the
  // browser paints -- an inline chart is never seen stranded between rows.
  useLayoutEffect(() => {
    if (!reveal) return;
    const key = rowKey(reveal.kind, reveal.id);
    if (reveal.kind === "body") {
      const known = reveal.id === "moon" ? planets?.moon
        : planets?.planets.find((p) => p.name === reveal.id);
      if (!known) return;
      setTab("planets");
      setSearch("");
      recordBody(reveal.id);
    } else {
      const group = groupOf.get(reveal.id);
      const row = group ? targets?.groups[group]?.find((t) => t.name === reveal.id) : undefined;
      if (!group || !row) return;
      setTab("targets");
      setSearch("");
      if (!showAll && !row.visible_tonight) onShowAllChange(true);
      if (popularOnly && !row.showpiece) onPopularOnlyChange(false);
      setOpenGroups({ [group]: true });
      recordTarget(row);
    }
    setOpenRow(key);
    setFlashKey(key);
    pendingScroll.current = {
      key, how: chartInline ? "chart" : "center", keepTop: reveal.keepTop,
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reveal?.seq]);

  useEffect(() => {
    if (!flashKey) return;
    const done = window.setTimeout(() => setFlashKey(null), 1600);
    return () => window.clearTimeout(done);
  }, [flashKey]);

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

            {shownGroups.map((group) => {
              const rows = filteredGroups[group];
              const open = isGroupOpen(group);
              const info = targets.group_info?.[group];
              const label = info?.label ?? group;
              const isConstellation = info?.is_constellation ?? false;
              const onChart = charted.includes(group);

              return (
                <div key={group} className={`group ${open ? "open" : ""}`}>
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
                            ? `Remove ${label} from the altitude chart`
                            : `Add ${label} to the altitude chart`
                        }
                        // The visible text is "+" or a tick, which names
                        // nothing on its own; title is not a reliable
                        // accessible name, so state it explicitly.
                        aria-label={
                          onChart
                            ? `Remove ${label} from the altitude chart`
                            : `Add ${label} to the altitude chart`
                        }
                        aria-pressed={onChart}
                      >
                        {onChart ? "✓" : "+"}
                      </button>
                    )}
                    <button
                      className="group-head"
                      onClick={() => toggleGroup(group)}
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
                      <table className="targets objects">
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
                              state={rowState("target", target.name, group,
                                              () => targetSubject(target),
                                              () => recordTarget(target))}
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

            {foldable && (
              <button ref={foldButton} className="groups-fold" onClick={toggleFold}
                      aria-expanded={allGroupsShown}>
                <span className="caret" aria-hidden="true">{allGroupsShown ? "▴" : "▾"}</span>
                {allGroupsShown
                  ? "Show fewer constellations"
                  : `Show ${groupNames.length - shownGroups.length} more constellations`}
              </button>
            )}
          </>
        )}

        {tab === "planets" && planets && (
          <div className="table-scroll">
            <table className="targets planets">
              <thead>
                <tr>
                  <th aria-label="On the altitude chart" />
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
                    state={rowState("body", "moon", undefined,
                                    () => bodySubject("moon", planets.moon!.peak_time),
                                    () => recordBody("moon"))}
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
                      state={rowState("body", planet.name, undefined,
                                      () => bodySubject(planet.name, planet.peak_time),
                                      () => recordBody(planet.name))}
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

            {!events && !eventsError && <p className="muted">Loading…</p>}
            {eventsError && (
              <p className="warning small">Couldn't load events: {eventsError}</p>
            )}

            {events && (
              <>
                <h4>Meteor showers</h4>
                {events.showers.length === 0 ? (
                  <p className="muted">None peaking in this window.</p>
                ) : (
                  <div className="table-scroll">
                    <table className="targets showers">
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
                            <td className="nowrap" title="When the radiant is highest in the dark">
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
                    <table className="targets conjunctions">
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
