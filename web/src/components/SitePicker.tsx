/**
 * Click-a-point site picker.
 *
 * **Why this exists.** The geocoder can only find places that have names. Most
 * observing sites do not: a pullout eight miles up a forest road is impossible
 * to search for and trivial to click. This was not hypothetical — during
 * testing, "Lone Pine, Calif" returned zero results while fully online, purely
 * because Open-Meteo's geocoder does not parse comma-qualified queries, and
 * "Griffith Observatory" returns nothing at all because it is a landmark
 * rather than a populated place.
 *
 * Coordinates are all the engine needs. Timezone comes from `timezonefinder`
 * server-side, and every other astronomical quantity — twilights, the moon,
 * altitude curves, which targets clear the floor — is geometry from lat/lon.
 * So the backend needs no changes for this: `POST /api/locations` already
 * takes explicit coordinates, which is what the form has always sent.
 *
 * **What a click cannot tell you** is sky darkness. Bortle stays a separate,
 * required decision in the form — coordinates alone say nothing about light
 * pollution. Where a light-pollution map is configured (see the README), the
 * form reads the class from it for the clicked point; a darkness overlay
 * drawn from that same map would be the natural next step, since it is how
 * people actually choose a dark site.
 *
 * **Plain Leaflet, no React wrapper.** react-leaflet is licensed
 * Hippocratic-2.1, which is not OSI-approved and imposes use restrictions that
 * do not belong in an MIT repository. Leaflet itself is BSD-2-Clause, and its
 * imperative API is small enough to drive from one effect.
 *
 * **Tile usage.** Tiles come from OpenStreetMap, whose usage policy permits
 * small personal and portfolio applications provided attribution is shown and
 * tiles are not bulk-downloaded. Leaflet renders the attribution control by
 * default and nothing here prefetches, so both hold. That no-prefetch rule is
 * also why an offline "cache the map before you drive out" feature would need
 * a different tile source, not just more code.
 */

import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import {
  api, type SkyBrightnessCoverage, type SkyBrightnessReading, type SkyGlowClass,
} from "../api";

/** Where the map opens with no coordinates and no atlas: a wide view, which
 *  says "pick anywhere" rather than implying a particular part of the world. */
const DEFAULT_CENTER: [number, number] = [30.0, 0.0];
const DEFAULT_ZOOM = 2;
const PICKED_ZOOM = 11;

const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const TILE_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

/**
 * The light-pollution overlay you pick a dark site off.
 *
 * Where the server has a sky-brightness map with tiles (see
 * scripts/build_skyglow.py), the overlay is that map: sky glow, spreading out
 * from towns into the country around them, and the same numbers the form
 * reads a site's class from. Tiles are drawn once when the map is built and
 * served as files.
 *
 * Without one it falls back to NASA's VIIRS night lights from GIBS, which
 * needs no key and no account. Those show where light leaves the ground, not
 * the sky glow over a site, so they are the second choice.
 *
 * NOTE the axis order. GIBS is WMTS, whose path is TileMatrix/TileRow/TileCol
 * -- z/y/x -- where Leaflet's own convention is z/x/y. Getting this backwards
 * silently returns tiles for the wrong place rather than failing.
 */
const LIGHT_POLLUTION_URL =
  "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_Black_Marble" +
  "/default/2016-01-01/GoogleMapsCompatible_Level8/{z}/{y}/{x}.png";
const LIGHT_POLLUTION_ATTRIBUTION =
  'Night lights: VIIRS Black Marble, NASA <a href="https://nasa-gibs.github.io/gibs-api-docs/">GIBS</a>';

/** GoogleMapsCompatible_Level8 stops at zoom 8; past that Leaflet upscales
 *  what it has rather than requesting tiles that do not exist. Upscaled is
 *  honest here -- the underlying data really is about 500 m per pixel. */
const LIGHT_POLLUTION_MAX_NATIVE_ZOOM = 8;

/** Served by the API from the files the map build wrote. */
const SKY_GLOW_URL = "/api/skybrightness/tiles/{z}/{x}/{y}.png";
const SKY_GLOW_ATTRIBUTION =
  'Sky glow: modelled from <a href="https://eogdata.mines.edu/products/vnl/">EOG VIIRS</a> night lights';

/** How an overlay is shown: its tiles run out at `nativeZoom`, and up to
 *  there it is drawn at `full` opacity. */
interface Overlay { nativeZoom: number; full: number }

/** The overlay's opacity at a zoom. Past its native zoom the overlay is the
 *  same coarse pixels stretched -- the satellite sees nothing finer -- while
 *  the street map underneath keeps sharpening. So it eases to a little over
 *  half strength five levels on: still a glow saying where the light is,
 *  with the crisp roads showing through instead of a soft blur over them. */
function overlayOpacity(zoom: number, overlay: Overlay): number {
  const t = Math.min(1, Math.max(0, (zoom - overlay.nativeZoom) / 5));
  return overlay.full * (1 - 0.45 * t);
}

/** Five decimal places is about a metre — far finer than any observing site
 *  needs, and enough that the number stops looking arbitrary. */
const COORD_DP = 5;

interface Props {
  /** Current coordinates as held by the form, if they parse. */
  lat: number | null;
  lon: number | null;
  onPick: (lat: number, lon: number) => void;
  /** The form's sky-brightness reading for these coordinates, once it has
   *  one, shown on the pin; and the name of its Bortle class. */
  reading?: SkyBrightnessReading | null;
  readingLabel?: string | null;
  /** The form's elevation for these coordinates, in metres, once known. */
  elevationM?: number | null;
  /** Changes whenever the map should go to the pin and zoom in on it -- after
   *  "Use my location", where a pin on a whole-country map says nothing. */
  focus?: number;
}

/** Metres to feet, for the pin: US observers read heights in feet. */
const FEET_PER_METRE = 3.28084;

/** What the pin says about the sky where it stands: the class with its
 *  decimal and description, and the SQM behind it. Built as elements rather
 *  than an HTML string, so nothing in it is ever parsed as markup. */
function readingContent(reading: SkyBrightnessReading | null, label: string | null,
                        elevationM: number | null): HTMLElement {
  const box = document.createElement("div");
  const add = (tag: string, className: string, text: string) => {
    const el = document.createElement(tag);
    el.className = className;
    el.textContent = text;
    box.appendChild(el);
  };
  if (reading && (!reading.in_coverage || reading.sqm === null)) {
    add("div", "site-reading-note", "No sky-brightness data here");
  } else if (reading && reading.sqm !== null) {
    add("div", "site-reading-class",
        `Bortle ${(reading.bortle_decimal ?? reading.bortle ?? 0).toFixed(1)}`);
    if (label) add("div", "site-reading-label", label);
    add("div", "site-reading-sqm", `SQM ${reading.sqm.toFixed(2)} mag/arcsec²`);
  }
  if (elevationM !== null) {
    const feet = Math.round(elevationM * FEET_PER_METRE).toLocaleString();
    add("div", "site-reading-sqm", `Elevation ${feet} ft`);
  }
  return box;
}

/** Leaflet's default marker resolves its PNGs by a relative URL that breaks
 *  under a bundler. An inline divIcon avoids the asset problem entirely and
 *  matches the app's palette. */
const PIN = L.divIcon({
  className: "site-pin",
  html: '<span aria-hidden="true"></span>',
  iconSize: [18, 18],
  iconAnchor: [9, 9],
});

/** Longitude can run past 180 once the user pans into a repeated world. */
function normaliseLon(lon: number): number {
  return (((lon + 180) % 360) + 360) % 360 - 180;
}

export function SitePicker({
  lat, lon, onPick, reading = null, readingLabel = null, elevationM = null, focus = 0,
}: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const marker = useRef<L.Marker | null>(null);
  const [tilesFailed, setTilesFailed] = useState(false);
  const [showLights, setShowLights] = useState(true);
  const lightsLayer = useRef<L.TileLayer | null>(null);
  // Read when the overlay arrives, after the map is built: the checkbox may
  // have been changed in the meantime.
  const showLightsNow = useRef(showLights);
  showLightsNow.current = showLights;
  const [coverage, setCoverage] = useState<SkyBrightnessCoverage | null>(null);

  // `onPick` gets a new identity on every render of the parent, so it is held
  // in a ref and read at event time. Listing it as an effect dependency would
  // tear down and rebuild the whole map on each keystroke elsewhere in the
  // form.
  const pick = useRef(onPick);
  pick.current = onPick;

  useEffect(() => {
    if (!container.current || map.current) return;

    const hasPoint = lat !== null && lon !== null;
    const instance = L.map(container.current, {
      center: hasPoint ? [lat, lon] : DEFAULT_CENTER,
      zoom: hasPoint ? PICKED_ZOOM : DEFAULT_ZOOM,
      // Wheel zoom is handled below rather than by Leaflet, to tune it for
      // trackpads: a pinch and a two-finger scroll both zoom.
      scrollWheelZoom: false,
      worldCopyJump: true,
      // Fractional zoom, so a pinch follows the fingers smoothly instead of
      // stepping a whole level at a time. The +/- buttons still step by one.
      zoomSnap: 0,
      zoomDelta: 1,
    });

    // Every wheel over the map zooms it, about the pointer, like the sky
    // chart: a two-finger scroll, a mouse wheel, or a trackpad pinch (which
    // arrives as a wheel event with ctrlKey set, and would otherwise zoom
    // the whole page). The dialog still scrolls from anywhere off the map.
    //
    // Done the way Leaflet does its own touch pinch, not with setZoomAround.
    // A zoom outside an animation makes every tile layer rebuild its grid
    // and abort all tiles in flight -- on each of the dozens of wheel events
    // a gesture sends, so no new tile ever finished loading and the dark
    // background showed through until the fingers stopped. Moving with
    // {pinch: true} tells the layers to keep the tiles they have, scaled,
    // and fetch the next level alongside; the gesture is then finished as
    // TouchZoom finishes one. `_stop`, `_moveStart`, `_move`, `_animateZoom`
    // and `_limitZoom` are Leaflet internals, stable across 1.x -- the same
    // calls its own handler makes (src/map/handler/Map.TouchZoom.js) -- as is
    // `_onZoomTransitionEnd`, the end of a zoom animation.
    type Internal = L.Map & {
      _stop(): void;
      _moveStart(zoomChanged: boolean, noMoveStart: boolean): void;
      _move(center: L.LatLng, zoom: number, data?: object): void;
      _animateZoom(center: L.LatLng, zoom: number, startAnim: boolean, noUpdate: boolean): void;
      _resetView(center: L.LatLng, zoom: number): void;
      _limitZoom(zoom: number): number;
      _onZoomTransitionEnd(): void;
      _animatingZoom?: boolean;
    };
    const inner = instance as Internal;
    const gesture = { active: false, zoom: 0, center: instance.getCenter(), frame: 0, idle: 0 };
    const finish = () => {
      if (!gesture.active) return;
      gesture.active = false;
      cancelAnimationFrame(gesture.frame);
      const zoom = inner._limitZoom(gesture.zoom);
      if (instance.options.zoomAnimation) inner._animateZoom(gesture.center, zoom, true, false);
      else inner._resetView(gesture.center, zoom);
    };
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const pixels = event.deltaMode === 1 ? event.deltaY * 16 : event.deltaY;
      if (!pixels) return;
      if (!gesture.active) {
        // The last gesture's closing animation still running: finish it now,
        // or its end would later jump the map back to where that one stopped.
        if (inner._animatingZoom) inner._onZoomTransitionEnd();
        inner._stop();
        inner._moveStart(true, false);
        gesture.active = true;
        gesture.zoom = instance.getZoom();
      }
      // A pinch sends small deltas, so a level per ~60 px; a two-finger
      // scroll sends large ones and a mouse notch is ~100, so a level per
      // ~200 px, which keeps a flick from shooting across a dozen levels.
      const perLevel = event.ctrlKey ? 60 : 200;
      gesture.zoom = Math.min(instance.getMaxZoom(), Math.max(instance.getMinZoom(),
        gesture.zoom - pixels / perLevel));
      // Keep the point under the pointer where it is.
      const pointer = instance.mouseEventToContainerPoint(event);
      const offset = pointer.subtract(instance.getSize().divideBy(2));
      const anchor = instance.containerPointToLatLng(pointer);
      gesture.center = instance.unproject(
        instance.project(anchor, gesture.zoom).subtract(offset), gesture.zoom);
      cancelAnimationFrame(gesture.frame);
      gesture.frame = requestAnimationFrame(() =>
        inner._move(gesture.center, gesture.zoom, { pinch: true, round: false }));
      window.clearTimeout(gesture.idle);
      gesture.idle = window.setTimeout(finish, 180);
    };
    const element = container.current;
    element.addEventListener("wheel", onWheel, { passive: false });

    const tiles = L.tileLayer(TILE_URL, {
      attribution: TILE_ATTRIBUTION,
      maxZoom: 19,
      className: "basemap-tiles",
    });
    // Offline or blocked: say so rather than leaving a grey box the user
    // cannot interpret. Same contract as the geocoder degrading to [].
    tiles.on("tileerror", () => setTilesFailed(true));
    tiles.addTo(instance);

    // One request says both which overlay to show and where the map's data
    // is; the opening view below waits on the same answer.
    const coverageRequest = api.skyBrightness().catch(() => null);
    let overlay: Overlay | null = null;
    coverageRequest.then((found) => {
      if (map.current !== instance) return;
      setCoverage(found);
      const tiles = found?.tiles;
      const box = found?.bounds;
      // Partly transparent so the coastline and roads underneath stay
      // readable; the point is to place the glow against geography you
      // recognise. The sky-glow tiles carry their own transparency -- none
      // at all where the sky is pristine -- so they go on at full strength.
      overlay = tiles
        ? { nativeZoom: tiles.max_zoom, full: 1 }
        : { nativeZoom: LIGHT_POLLUTION_MAX_NATIVE_ZOOM, full: 0.75 };
      lightsLayer.current = tiles
        // The version in the URL means a rebuilt map is new URLs, so the
        // browser cannot keep showing the old tiles it has cached.
        ? L.tileLayer(`${SKY_GLOW_URL}?v=${encodeURIComponent(tiles.version)}`, {
            attribution: SKY_GLOW_ATTRIBUTION,
            minNativeZoom: tiles.min_zoom,
            maxNativeZoom: tiles.max_zoom,
            maxZoom: 19,
            // No requests outside the map, where every tile would be empty.
            bounds: box ? L.latLngBounds([box[1], box[0]], [box[3], box[2]]) : undefined,
            opacity: overlayOpacity(instance.getZoom(), overlay),
            className: "light-pollution-tiles",
          })
        : L.tileLayer(LIGHT_POLLUTION_URL, {
            attribution: LIGHT_POLLUTION_ATTRIBUTION,
            maxNativeZoom: LIGHT_POLLUTION_MAX_NATIVE_ZOOM,
            maxZoom: 19,
            opacity: overlayOpacity(instance.getZoom(), overlay),
            className: "light-pollution-tiles",
          });
      if (showLightsNow.current) lightsLayer.current.addTo(instance);
    });
    instance.on("zoom", () => {
      if (overlay) lightsLayer.current?.setOpacity(overlayOpacity(instance.getZoom(), overlay));
    });

    instance.on("click", (event: L.LeafletMouseEvent) => {
      pick.current(
        Number(event.latlng.lat.toFixed(COORD_DP)),
        Number(normaliseLon(event.latlng.lng).toFixed(COORD_DP)),
      );
    });

    map.current = instance;

    // The dialog lays out around the map, so Leaflet starts out believing its
    // container is zero-sized. Everything that depends on the viewport has to
    // wait for invalidateSize(), including fitBounds -- called earlier it is
    // silently a no-op, which is how the map kept opening on the whole world
    // despite the coverage request succeeding.
    const settle = window.setTimeout(() => {
      if (map.current !== instance) return;
      instance.invalidateSize();

      // Open where the light-pollution atlas actually has data. A regional
      // export covers one area, and a map centred outside it means every click
      // falls back to assuming Bortle 5 -- which works, and is a poor first
      // impression. Asking the server rather than hardcoding a region means
      // swapping the raster moves the map with it. Skipped when the form
      // already has coordinates, which beat any default.
      if (hasPoint) return;
      coverageRequest
        .then((found) => {
          const box = found?.bounds;
          if (!box || map.current !== instance) return;
          const [west, south, east, north] = box;
          // animate:false is not a nicety. Leaflet's animated path returns
          // early and finishes on a CSS transitionend, so where frames are
          // not composited -- a hidden tab, a headless pane -- the view never
          // arrives and fitBounds is silently a no-op. An opening view should
          // snap anyway; flying from the whole world to one state is motion
          // for its own sake.
          instance.fitBounds([[south, west], [north, east]],
                             { padding: [12, 12], animate: false });
        })
        .catch(() => {
          /* no atlas, or no server: the wide default view stands */
        });
    }, 150);

    return () => {
      window.clearTimeout(settle);
      element.removeEventListener("wheel", onWheel);
      window.clearTimeout(gesture.idle);
      cancelAnimationFrame(gesture.frame);
      instance.remove();
      map.current = null;
      marker.current = null;
      lightsLayer.current = null;
    };
    // Mount once. Coordinate changes are handled below, so typing in the
    // lat/lon boxes moves the pin instead of rebuilding the map.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const instance = map.current;
    const layer = lightsLayer.current;
    if (!instance || !layer) return;
    if (showLights) layer.addTo(instance);
    else layer.remove();
  }, [showLights]);

  // Keep the pin in step with whatever the form holds, wherever those numbers
  // came from — a click here, a geocoder candidate, or typing.
  useEffect(() => {
    const instance = map.current;
    if (!instance) return;

    if (lat === null || lon === null) {
      marker.current?.remove();
      marker.current = null;
      return;
    }

    const position: [number, number] = [lat, lon];
    if (marker.current) {
      marker.current.setLatLng(position);
    } else {
      marker.current = L.marker(position, {
        icon: PIN,
        draggable: true,
        keyboard: true,
        title: "Drag to adjust",
      }).addTo(instance);
      marker.current.on("dragend", () => {
        const { lat: dragLat, lng } = marker.current!.getLatLng();
        pick.current(
          Number(dragLat.toFixed(COORD_DP)),
          Number(normaliseLon(lng).toFixed(COORD_DP)),
        );
      });
    }

    if (!instance.getBounds().contains(position)) {
      instance.setView(position, Math.max(instance.getZoom(), PICKED_ZOOM));
    }
  }, [lat, lon]);

  useEffect(() => {
    const instance = map.current;
    if (!focus || !instance || lat === null || lon === null) return;
    instance.setView([lat, lon], Math.max(instance.getZoom(), PICKED_ZOOM));
    // Only a new request to focus moves the map; typing coordinates does not.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus]);

  // The reading, on the pin. A tooltip rather than a popup: a popup closes on
  // the next map click, which is exactly when a new reading is wanted. The
  // form drops its reading the moment the coordinates change, so this never
  // shows one pin's answer against another's position.
  useEffect(() => {
    const pin = marker.current;
    if (!pin) return;
    if (!reading && elevationM === null) {
      pin.unbindTooltip();
      return;
    }
    // Above the pin, unless that would run off the top of the map -- the map
    // clips it, so a pin dropped near the top edge would show nothing.
    const instance = map.current;
    const nearTop = instance ? instance.latLngToContainerPoint(pin.getLatLng()).y < 90 : false;
    pin.unbindTooltip();
    pin.bindTooltip(readingContent(reading, readingLabel, elevationM), {
      permanent: true,
      direction: nearTop ? "bottom" : "top",
      offset: nearTop ? [0, 12] : [0, -12],
      className: "site-reading",
    }).openTooltip();
  }, [reading, readingLabel, elevationM, lat, lon]);

  return (
    <div className="site-picker">
      <div
        ref={container}
        className="site-map"
        role="application"
        aria-label="Map - click to choose observing coordinates"
      />
      {tilesFailed && (
        <p className="warning">
          Map tiles could not be loaded, so the map is blank — you are probably
          offline. The coordinate fields below still work; type them in
          directly.
        </p>
      )}
      <div className="site-map-controls">
        <label className="lights-toggle">
          <input
            type="checkbox"
            checked={showLights}
            onChange={(e) => setShowLights(e.target.checked)}
          />
          <span>Show light pollution</span>
        </label>
        {showLights && coverage?.tiles && (coverage.tiles.classes
          ? <BortleKey classes={coverage.tiles.classes} source={coverage.source} />
          : <SkyGlowKey legend={coverage.tiles.legend} source={coverage.source} />)}
      </div>

      <p className="muted small">
        Click anywhere to drop a pin, or drag it to adjust. Scroll or pinch to
        zoom.
      </p>
    </div>
  );
}

/**
 * The overlay's colour key, drawn from the same stops the tiles were coloured
 * by, so the two cannot drift apart. Dark sky on the left, city on the right,
 * with the SQM each colour stands for.
 */
function SkyGlowKey({ legend, source }: {
  legend: [number, number[]][];
  source: string | null;
}) {
  const darkest = legend[0][0];
  const brightest = legend[legend.length - 1][0];
  const at = (sqm: number) => ((darkest - sqm) / (darkest - brightest)) * 100;
  const gradient = legend
    .map(([sqm, [r, g, b, a]]) =>
      `rgba(${r}, ${g}, ${b}, ${(a / 255).toFixed(2)}) ${at(sqm).toFixed(1)}%`)
    .join(", ");
  const ticks = [22, 21, 20, 19, 18, 17].filter((v) => v <= darkest && v >= brightest);
  return (
    <div className="sky-glow-key" role="img"
         aria-label={`Colour key: sky brightness from SQM ${darkest}, darkest, to ${brightest}, brightest`}>
      <div className="sky-glow-bar" style={{ backgroundImage: `linear-gradient(to right, ${gradient})` }} />
      <div className="sky-glow-ticks" aria-hidden="true">
        {ticks.map((v) => <span key={v} style={{ left: `${at(v)}%` }}>{v}</span>)}
      </div>
      <p className="muted small" aria-hidden="true">
        Sky brightness (SQM), higher is darker{source && <> · {source}</>}
      </p>
    </div>
  );
}

/**
 * The overlay's key as the nine Bortle classes, one equal band each, in the
 * colours the tiles were drawn with. Equal bands rather than an SQM scale:
 * classes 2 and 3 are only a tenth and a fifth of a magnitude wide and would
 * be slivers. Class 1 is drawn as the clear map it is.
 */
function BortleKey({ classes, source }: { classes: SkyGlowClass[]; source: string | null }) {
  const range = (c: SkyGlowClass, i: number) => {
    const brighter = classes[i - 1]?.sqm_min;
    if (c.sqm_min === null) return `SQM below ${brighter}`;
    return i === 0 ? `SQM ${c.sqm_min} and darker` : `SQM ${c.sqm_min} to ${brighter}`;
  };
  return (
    <div className="sky-glow-key" role="img"
         aria-label="Colour key: Bortle classes 1, pristine, to 9, inner city">
      <div className="bortle-key">
        {classes.map((c, i) => {
          const [r, g, b, a] = c.rgba;
          // Dark numerals on the pale swatches (8's pink, 9's white).
          const pale = (0.299 * r + 0.587 * g + 0.114 * b) * (a / 255) > 150;
          return (
            <span key={c.bortle} title={`Bortle ${c.bortle}: ${range(c, i)}`}
                  className={pale ? "pale" : undefined}
                  style={{ backgroundColor: `rgba(${r}, ${g}, ${b}, ${(a / 255).toFixed(2)})` }}>
              {c.bortle}
            </span>
          );
        })}
      </div>
      <p className="muted small" aria-hidden="true">
        Bortle class{source && <> · {source}</>}
      </p>
    </div>
  );
}
