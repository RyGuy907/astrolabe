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

/** Where the map opens when there is nothing else to go on. Deliberately
 *  zoomed out: a wide view says "pick anywhere" rather than implying the user
 *  should be near one particular city. */
const DEFAULT_CENTER: [number, number] = [34.0, -118.2];
const DEFAULT_ZOOM = 5;
const PICKED_ZOOM = 11;

const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const TILE_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

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
      // hostile. Ctrl/Cmd+wheel still zooms, and the +/- control always works.
      scrollWheelZoom: false,
      worldCopyJump: true,
    });

    const tiles = L.tileLayer(TILE_URL, {
      attribution: TILE_ATTRIBUTION,
      maxZoom: 19,
    });
    // Offline or blocked: say so rather than leaving a grey box the user
    // cannot interpret. Same contract as the geocoder degrading to [].
    tiles.on("tileerror", () => setTilesFailed(true));
    tiles.addTo(instance);

    instance.on("click", (event: L.LeafletMouseEvent) => {
      pick.current(
        Number(event.latlng.lat.toFixed(COORD_DP)),
        Number(normaliseLon(event.latlng.lng).toFixed(COORD_DP)),
      );
    });

    map.current = instance;

    // The dialog lays out around the map; Leaflet needs telling once the
    // container has its final size or it renders a partial tile grid.
    const settle = window.setTimeout(() => instance.invalidateSize(), 120);

    return () => {
      window.clearTimeout(settle);
      instance.remove();
      map.current = null;
      marker.current = null;
    };
    // Mount once. Coordinate changes are handled below, so typing in the
    // lat/lon boxes moves the pin instead of rebuilding the map.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
      <p className="muted small">
        Click anywhere to drop a pin, or drag it to adjust. Hold Ctrl and
        scroll to zoom. Coordinates fill in below — the sky darkness at that
        point does not, so set the Bortle class yourself.
      </p>
    </div>
  );
}
