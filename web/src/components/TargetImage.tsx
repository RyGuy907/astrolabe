/**
 * A survey cutout for one target, framed from the catalogue's own numbers.
 *
 * **Why this needs no backend work.** CDS's hips2fits service takes a centre
 * and a field of view and returns a JPEG, so the URL is computed here from
 * `ra_deg`, `dec_deg` and `size_arcmin` — all of which `TargetModel` already
 * carries. No API key, no library, and the bytes come from CDS rather than
 * from our own server.
 *
 * **Why DSS2 colour.** It covers the whole sky. PanSTARRS is sharper and
 * deeper but stops around −30° declination, and this engine is
 * latitude-general — a southern site would hit silent holes. If a
 * survey-picker is ever added, PanSTARRS is the one to offer above that
 * declination.
 *
 * **The honest caveat, which is the point of the caption.** DSS2 is
 * photographic. M31 here is a bright spiral with dust lanes; M31 in an 8-inch
 * Dobsonian is a grey oval smudge. Showing survey imagery next to a target
 * list invites exactly the disappointment this project works to avoid
 * elsewhere, so the image is labelled for what it is rather than left to imply
 * it is a preview of the eyepiece.
 *
 * Attribution is a condition of use and is stated in the caption, the footer
 * and THIRD_PARTY_NOTICES.md: "This research made use of hips2fits, a service
 * provided by CDS."
 */

import { useEffect, useState } from "react";
import { loadCachedImage } from "../imageCache";

const HIPS2FITS = "https://alasky.cds.unistra.fr/hips-image-services/hips2fits";

/** Whole-sky photographic survey. See the note above on PanSTARRS. */
const SURVEY = "CDS/P/DSS2/color";

/** Rendered size. Small enough to be polite to a free service, large enough
 *  to be worth looking at. */
const PIXELS = 320;

/** Asked for on high-density screens -- every phone -- where a 320-pixel
 *  cutout shown 320 points wide is drawn from a third of the pixels the
 *  screen has and looks soft. Still a modest request. */
const PIXELS_DENSE = 480;

function requestPixels(): number {
  return typeof window !== "undefined" && window.devicePixelRatio >= 1.5
    ? PIXELS_DENSE : PIXELS;
}

/** Show this much sky around the object itself, so it sits in context rather
 *  than filling the frame edge to edge. */
const FRAMING = 2.5;

/** Used when the catalogue has no angular size, which is common for the
 *  fainter entries. Half a degree is a reasonable "point at it and see". */
const DEFAULT_FOV_DEG = 0.5;

/** Keep tiny objects from being blown up into a few pixels of noise, and keep
 *  the largest from requesting an absurd field. */
const MIN_FOV_DEG = 0.1;
const MAX_FOV_DEG = 5.0;

export function cutoutFov(sizeArcmin: number | null): number {
  if (sizeArcmin === null || !Number.isFinite(sizeArcmin) || sizeArcmin <= 0) {
    return DEFAULT_FOV_DEG;
  }
  const degrees = (sizeArcmin * FRAMING) / 60;
  return Math.min(MAX_FOV_DEG, Math.max(MIN_FOV_DEG, degrees));
}

export function cutoutUrl(raDeg: number, decDeg: number,
                          sizeArcmin: number | null, pixels = PIXELS): string {
  const params = new URLSearchParams({
    hips: SURVEY,
    width: String(pixels),
    height: String(pixels),
    fov: cutoutFov(sizeArcmin).toFixed(4),
    projection: "TAN",
    coordsys: "icrs",
    ra: raDeg.toFixed(6),
    dec: decDeg.toFixed(6),
    format: "jpg",
  });
  return `${HIPS2FITS}?${params.toString()}`;
}

interface Props {
  name: string;
  raDeg: number;
  decDeg: number;
  sizeArcmin: number | null;
  /** Rendered in the column beside the cutout, above the caption. That column
   *  held one line of attribution and 300px of nothing, which is where the
   *  object's facts now go. */
  children?: React.ReactNode;
}

export function TargetImage({ name, raDeg, decDeg, sizeArcmin,
                              children }: Props) {
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading");
  const [src, setSrc] = useState<string | null>(null);
  const [fromCache, setFromCache] = useState(false);
  const fov = cutoutFov(sizeArcmin);
  const url = cutoutUrl(raDeg, decDeg, sizeArcmin, requestPixels());

  // Goes through the browser cache rather than straight to <img src>, because
  // hips2fits sends no caching headers and re-renders every request: measured
  // 1363 ms cold against 3 ms from the cache. See src/imageCache.ts for why
  // this is a cache and not a mirror.
  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    const controller = new AbortController();

    setState("loading");
    setSrc(null);

    loadCachedImage(url, controller.signal)
      .then((image) => {
        if (cancelled) {
          URL.revokeObjectURL(image.objectUrl);
          return;
        }
        objectUrl = image.objectUrl;
        setSrc(image.objectUrl);
        setFromCache(image.fromCache);
        setState("ready");
      })
      .catch((error) => {
        if (cancelled || (error as Error)?.name === "AbortError") return;
        setState("failed");
      });

    return () => {
      cancelled = true;
      controller.abort();
      // Object URLs are held by the document until revoked; a list the user
      // opens and closes repeatedly would otherwise leak a blob each time.
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [url]);

  return (
    <figure className="target-image">
      {state === "loading" && (
        <div className="target-image-failed target-image-loading" role="status">
          <p className="muted small">Loading survey image…</p>
        </div>
      )}
      {state === "ready" && src && (
        <img
          src={src}
          width={PIXELS}
          height={PIXELS}
          // Deliberately NOT loading="lazy". Browsing a 270-row list costs
          // nothing because the row renders this component only once it is
          // expanded -- measured at zero image requests before any row is
          // opened -- so lazy loading buys nothing here. It also actively
          // broke the load once: inside the target list's own scrolling
          // container the intersection heuristic never fired, and the image
          // sat at complete=false indefinitely.
          decoding="async"
          alt={
            `Sky survey image of ${name}, ${fov.toFixed(2)} degrees across, ` +
            `centred on right ascension ${raDeg.toFixed(3)} degrees and ` +
            `declination ${decDeg.toFixed(3)} degrees.`
          }
          onError={() => setState("failed")}
        />
      )}
      {state === "failed" && (
        // Same contract as the geocoder degrading to an empty list: say what
        // happened rather than leaving a broken frame.
        <div className="target-image-failed">
          <p className="muted small">
            Could not load the survey image — you may be offline, or the CDS
            service may be unreachable. Everything else on this page works
            without it.
          </p>
        </div>
      )}

      <div className="target-image-side">
        {children}
        <figcaption className="muted small">
          DSS2, {fov.toFixed(2)}° across · hips2fits /{" "}
          <a href="https://cds.unistra.fr/" target="_blank" rel="noreferrer">
            CDS
          </a>
          {fromCache && " · cached"}
        </figcaption>
      </div>
    </figure>
  );
}
