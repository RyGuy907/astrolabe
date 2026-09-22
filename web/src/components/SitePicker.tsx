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
 * pollution. Wiring that up automatically is HANDOFF item 2 (vendor the Falchi
 * atlas and read the pixel); when that lands, this component is where the
 * value should surface, and the atlas would also make a darkness overlay
 * possible, which is how people actually choose a dark site.
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
      // The dialog is a scrolling column; swallowing the wheel inside it is
      // hostile, so a plain scroll scrolls the dialog. A trackpad pinch (and
      // Ctrl+wheel) zooms -- handled below -- and the +/- control always works.
      scrollWheelZoom: false,
      worldCopyJump: true,
      // Fractional zoom, so a pinch follows the fingers smoothly instead of
      // stepping a whole level at a time. The +/- buttons still step by one.
      zoomSnap: 0,
      zoomDelta: 1,
    });

    // A trackpad pinch arrives as a wheel event with ctrlKey set (Chrome,
    // Edge, Firefox and Safari all do this), as does Ctrl+wheel on a mouse.
    // Left alone, the browser zooms the whole page. Here it zooms the map
    // about the point between the fingers, like the sky chart does.
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey) return;              // a plain scroll scrolls the dialog
      event.preventDefault();
      const pixels = event.deltaMode === 1 ? event.deltaY * 16 : event.deltaY;
      // ~100 px per mouse-wheel notch is one zoom level; a pinch sends many
      // small deltas, which add up to the same feel.
      const zoom = Math.min(instance.getMaxZoom(), Math.max(instance.getMinZoom(),
        instance.getZoom() - pixels / 100));
      instance.setZoomAround(instance.mouseEventToContainerPoint(event), zoom,
                             { animate: false });
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
      opacity: 0.75,
      className: "light-pollution-tiles",
    });

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
