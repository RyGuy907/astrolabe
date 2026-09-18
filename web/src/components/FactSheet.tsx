/**
 * The facts beside the picture.
 *
 * Every row is optional and a missing one is simply absent, because the
 * reference data behind this is hand-curated for the showpieces and nobody
 * has written down who discovered the ten-thousandth anonymous NGC galaxy.
 * An empty "Discovered —" row would be worse than no row: it reads as a
 * failure rather than as a boundary of what is known.
 */

import type { ReactNode } from "react";

export function Fact({ label, children }: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="fact">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

export function FactSheet({ children }: { children: ReactNode }) {
  return <dl className="fact-sheet">{children}</dl>;
}

/**
 * A year, with BCE spelled out.
 *
 * Ptolemy catalogued the Ptolemy Cluster around 130 AD and Hipparchus the
 * Double Cluster around 130 BC, which are stored as 130 and -130 and would
 * otherwise print identically.
 */
export function formatYear(year: number): string {
  return year < 0 ? `${Math.abs(year)} BCE` : String(year);
}

/**
 * A distance in light years, in the unit an observer would use.
 *
 * Under a thousand: as written. Up to the edge of the Local Group: thousands,
 * comma-grouped, because "22,200 light years" is a quantity people have a
 * feel for. Beyond that: millions, since nobody means anything by 23,000,000.
 */
export function formatLightYears(ly: number): string {
  if (ly >= 1_000_000) {
    const millions = ly / 1_000_000;
    return `${millions >= 10 ? millions.toFixed(0) : millions.toFixed(1)} million ly`;
  }
  return `${Math.round(ly).toLocaleString("en-GB")} ly`;
}

/** An angular size in arcminutes, dropping to arcseconds when that reads better. */
export function formatAngularSize(arcmin: number): string {
  if (arcmin < 1) return `${(arcmin * 60).toFixed(0)}″`;
  if (arcmin >= 60) return `${(arcmin / 60).toFixed(1)}°`;
  return `${arcmin.toFixed(1)}′`;
}

/**
 * A distance in AU, also expressed as light travel time.
 *
 * The second form is the one that means something: Jupiter at 4.2 AU is "35
 * light minutes", which is how long the light you are looking at has been on
 * its way.
 */
export function formatLightMinutes(au: number): string {
  const minutes = au * 8.317;        // 1 AU = 499.0 light seconds
  if (minutes < 1) return `${(minutes * 60).toFixed(0)} light seconds`;
  if (minutes >= 60) {
    return `${Math.floor(minutes / 60)} h ${Math.round(minutes % 60)} min at light speed`;
  }
  return `${minutes.toFixed(0)} light minutes`;
}

/**
 * A rotation period in hours as a day length.
 *
 * Negative means retrograde — Venus and Uranus turn backwards — and that is
 * worth saying rather than hiding behind an absolute value, since Venus's day
 * being longer than its year and running the wrong way is the interesting
 * fact about it.
 */
export function formatRotation(hours: number): string {
  const magnitude = Math.abs(hours);
  const retrograde = hours < 0 ? ", retrograde" : "";
  if (magnitude >= 48) {
    return `${(magnitude / 24).toFixed(1)} Earth days${retrograde}`;
  }
  return `${magnitude.toFixed(1)} hours${retrograde}`;
}
