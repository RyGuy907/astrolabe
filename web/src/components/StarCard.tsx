/**
 * What a clicked star is: a small card over the sky chart.
 *
 * The figures come from `engine.stars`, which also says how far each can be
 * trusted; the card passes that on in words rather than printing a size
 * estimate with the same confidence as a measured distance.
 */

import { useEffect, useState } from "react";
import { api, ApiError, type StarProfile } from "../api";

interface Props {
  index: number;
  /** Where the star was clicked, in chart pixels, and the chart's size. */
  x: number;
  y: number;
  width: number;
  height: number;
  onClose: () => void;
}

const CARD_W = 250;
const CARD_H = 230;

/** Roughly the colour a star of this temperature looks. */
function swatch(k: number | null): string {
  if (!k) return "#d8dcff";
  const stops: [number, string][] = [
    [3000, "#ffb46b"], [4000, "#ffcf98"], [5000, "#ffe4c4"], [5800, "#fff2e4"],
    [7000, "#f8f6ff"], [10000, "#d4deff"], [20000, "#b0c4ff"],
  ];
  return (stops.find(([t]) => k <= t) ?? stops[stops.length - 1])[1];
}

/** How a size or output was arrived at, as a short note after it. */
const HOW: Record<string, string> = {
  measured: "measured", gaia: "Gaia estimate", estimated: "estimate",
};
const TEMP_HOW: Record<string, string> = {
  spectrum: "from its spectrum", type: "from its type", colour: "from its colour",
};

/** "8.61", "1,400", "92,000": three significant figures at most. */
const figure = (n: number) =>
  n.toLocaleString(undefined, { maximumSignificantDigits: n < 10 ? 2 : 3 });

export function StarCard({ index, x, y, width, height, onClose }: Props) {
  const [star, setStar] = useState<StarProfile | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setStar(null);
    setError(null);
    const controller = new AbortController();
    api.star(index, controller.signal).then(setStar).catch((e) => {
      if ((e as Error)?.name !== "AbortError") {
        setError(e instanceof ApiError ? e.message : String(e));
      }
    });
    return () => controller.abort();
  }, [index]);

  // Beside the star, kept inside the chart: right of it unless that runs off
  // the edge, and never covering the star itself.
  const left = x + 16 + CARD_W <= width ? x + 16 : Math.max(8, x - 16 - CARD_W);
  const top = Math.max(8, Math.min(y - 24, height - CARD_H - 8));

  const title = star
    ? star.name ?? (star.hip ? `HIP ${star.hip}` : "Unnamed star")
    : "…";

  return (
    <div className="star-card" style={{ left, top, width: CARD_W }}
         role="dialog" aria-label={`About ${title}`}
         onPointerDown={(e) => e.stopPropagation()}>
      <button className="star-card-close" onClick={onClose} aria-label="Close">×</button>
      <h4>
        <span className="star-swatch"
              style={{ background: swatch(star?.temperature_k ?? null),
                       color: swatch(star?.temperature_k ?? null) }} />
        {title}
      </h4>
      {error && <p className="warning small">{error}</p>}
      {!star && !error && <p className="muted small">Looking it up…</p>}
      {star && (
        <>
          <p className="star-kind">
            {star.kind}
            {star.spectral_type && <span className="muted"> · {star.spectral_type}</span>}
          </p>
          <dl>
            <dt>Distance</dt>
            <dd>
              {star.distance_ly
                ? <>{star.distance_quality === "precise" ? "" : "≈ "}{figure(star.distance_ly)} light-years
                    <span className="muted">
                      {" · "}{star.distance_source === "gaia" ? "Gaia" : "Hipparcos"}
                      {star.distance_quality !== "precise" && `, ${star.distance_quality}`}
                    </span></>
                : <span className="muted">Not measured</span>}
            </dd>
            {star.radius_sun && (<>
              <dt>Size</dt>
              <dd>
                {star.radius_source === "measured" ? "" : "≈ "}{figure(star.radius_sun)}× the Sun's width
                <span className="muted"> · {HOW[star.radius_source ?? "estimated"]}</span>
              </dd>
            </>)}
            {star.luminosity_sun && (<>
              <dt>Output</dt>
              <dd>
                ≈ {figure(star.luminosity_sun)}× the Sun's light
                {star.luminosity_source === "gaia" &&
                  <span className="muted"> · Gaia estimate</span>}
              </dd>
            </>)}
            {star.temperature_k && (<>
              <dt>Surface</dt>
              <dd>
                {star.temperature_k.toLocaleString()} K
                {star.temperature_source &&
                  <span className="muted"> · {TEMP_HOW[star.temperature_source]}</span>}
              </dd>
            </>)}
            <dt>Age</dt>
            <dd>
              {star.age
                ? <>{star.age[0].toUpperCase() + star.age.slice(1)}
                    {star.age_basis === "upper limit" &&
                      <span className="muted"> — stars this massive don't usually last longer</span>}</>
                : <span className="muted">Unknown — a single star's light doesn't reveal it</span>}
            </dd>
            <dt>Magnitude</dt>
            <dd>{star.magnitude.toFixed(2)}</dd>
          </dl>
        </>
      )}
    </div>
  );
}
