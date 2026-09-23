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
import { api } from "../api";

/** Where the map opens with no coordinates and no atlas: a wide view, which
 *  says "pick anywhere" rather than implying a particular part of the world. */
const DEFAULT_CENTER: [number, number] = [30.0, 0.0];
const DEFAULT_ZOOM = 2;
const PICKED_ZOOM = 11;

const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const TILE_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

/**
 * VIIRS night lights, as an overlay you can pick a dark site off.
 *
 * This is the same VIIRS data as the light-pollution atlases, delivered as
 * ordinary map tiles by NASA's GIBS rather than as a multi-gigabyte GeoTIFF.
 * That matters: the two datasets HANDOFF item 2 suggested vendoring have both
 * become gated -- Falchi is behind a human-reviewed request form and is
 * CC BY-NC, and EOG's VIIRS download now redirects to an OAuth login. GIBS
 * needs no key and no account, so nothing has to be vendored, downsampled, or
 * relicensed.
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

/** The overlay's opacity at a zoom. Past its native zoom the night lights
 *  are the same ~500 m pixels stretched -- every GIBS night-lights layer,
 *  Black Marble and the daily VIIRS ones alike, stops at level 8, as the
 *  sensor itself does -- while the street map underneath keeps sharpening.
 *  So the overlay eases from 0.75 at level 8 to 0.4 by level 13: still a
 *  glow saying where the light is, with the crisp roads showing through
 *  instead of a soft blur over them. */
function lightsOpacity(zoom: number): number {
  const t = Math.min(1, Math.max(0, (zoom - LIGHT_POLLUTION_MAX_NATIVE_ZOOM) / 5));
  return 0.75 - 0.35 * t;
}

/** Five decimal places is about a metre — far finer than any observing site
 *  needs, and enough that the number stops looking arbitrary. */
const COORD_DP = 5;

interface Props {
  /** Current coordinates as held by the form, if they parse. */
  lat: number | null;
  lon: number | null;
  onPick: (lat: number, lon: number) => void;
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

export function SitePicker({ lat, lon, onPick }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const marker = useRef<L.Marker | null>(null);
  const [tilesFailed, setTilesFailed] = useState(false);
  const [showLights, setShowLights] = useState(true);
  const lightsLayer = useRef<L.TileLayer | null>(null);

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

    // Partly transparent so the coastline and roads underneath stay readable;
    // the point is to place the glow against geography you recognise.
    lightsLayer.current = L.tileLayer(LIGHT_POLLUTION_URL, {
      attribution: LIGHT_POLLUTION_ATTRIBUTION,
      maxNativeZoom: LIGHT_POLLUTION_MAX_NATIVE_ZOOM,
      maxZoom: 19,
      opacity: lightsOpacity(instance.getZoom()),
      className: "light-pollution-tiles",
    });
    instance.on("zoom", () => lightsLayer.current?.setOpacity(lightsOpacity(instance.getZoom())));

    instance.on("click", (event: L.LeafletMouseEvent) => {
      pick.current(
        Number(event.latlng.lat.toFixed(COORD_DP)),
        Number(normaliseLon(event.latlng.lng).toFixed(COORD_DP)),
      );
    });

    map.current = instance;
    if (showLights) lightsLayer.current.addTo(instance);

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
      api.skyBrightness()
        .then((coverage) => {
          const box = coverage.bounds;
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
      </div>

      <p className="muted small">
        Click anywhere to drop a pin, or drag it to adjust. Hold Ctrl and
        scroll to zoom.
      </p>
    </div>
  );
}
