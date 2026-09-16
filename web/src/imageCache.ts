/**
 * Caches survey cutouts in the viewer's own browser.
 *
 * **Why it is needed.** CDS's hips2fits renders each cutout on demand and
 * returns it with no caching headers at all -- no Cache-Control, no ETag, no
 * Expires, no Last-Modified. Measured: three identical requests took 1.56 s,
 * 1.32 s and 1.59 s, with no warm path. A browser has nothing to revalidate
 * against, so every view of the same object pays that again and puts another
 * render on a free academic service.
 *
 * **Why a cache and not a mirror.** DSS2 is not open data. The plates are
 * copyright AURA, and STScI's terms state that there is "no bulk
 * redistribution of data without an agreement in place with STScI". Copying
 * the catalogue into object storage and serving it would be exactly that.
 * Storage was never the obstacle -- the whole catalogue at this size is around
 * 320 MB, well under a penny a month -- the licence is. Caching in the
 * viewer's own browser redistributes nothing: it is the browser cache those
 * missing headers prevented, and each viewer fetches from CDS once per object
 * rather than once per view.
 *
 * **Why here and not in a service worker.** A service worker would be
 * transparent to `<img src>` and would also cover offline navigation, and it
 * was the first implementation. It could not be verified: registration is
 * blocked outright in some embedded browsers, including the one this was
 * developed against, which fails with "an unknown error occurred when fetching
 * the script" even though the script serves a clean 200. The Cache API is
 * available in the page in the same environments and measured 1363 ms cold
 * against 3 ms warm, so the benefit is identical where it matters. It also
 * avoids a sticky cache layer with its own update lifecycle, which is a real
 * cost in a project that has already lost time to stale caches.
 *
 * Everything here degrades to "just fetch it normally": the Cache API throws
 * in private windows and when site data is blocked, and none of those failures
 * should stop an image loading.
 */

const CACHE_NAME = "astro-survey-images-v1";

/** Roughly 300 cutouts at ~26 KB is about 8 MB: generous for a night's
 *  planning, small enough not to squat on the origin's storage quota. */
const MAX_ENTRIES = 300;

function cacheAvailable(): boolean {
  try {
    return typeof caches !== "undefined";
  } catch {
    return false;
  }
}

/** Oldest-first trim. `cache.keys()` returns insertion order, a good enough
 *  stand-in for least-recently-added without tracking timestamps. */
async function trim(cache: Cache): Promise<void> {
  const keys = await cache.keys();
  if (keys.length <= MAX_ENTRIES) return;
  await Promise.all(
    keys.slice(0, keys.length - MAX_ENTRIES).map((key) => cache.delete(key)),
  );
}

export interface CachedImage {
  /** Object URL to hand to an <img>. Revoke it when done. */
  objectUrl: string;
  /** True when it came from the cache rather than the network. */
  fromCache: boolean;
}

/**
 * Fetch an image, serving it from the browser cache when it has been seen
 * before. Throws only if the image genuinely cannot be fetched.
 */
export async function loadCachedImage(
  url: string,
  signal?: AbortSignal,
): Promise<CachedImage> {
  if (!cacheAvailable()) {
    const response = await fetch(url, { signal });
    if (!response.ok) throw new Error(`image request failed: ${response.status}`);
    return { objectUrl: URL.createObjectURL(await response.blob()), fromCache: false };
  }

  let cache: Cache | null = null;
  try {
    cache = await caches.open(CACHE_NAME);
    const hit = await cache.match(url);
    if (hit) {
      return { objectUrl: URL.createObjectURL(await hit.blob()), fromCache: true };
    }
  } catch {
    cache = null;   // blocked storage; fall through to a plain fetch
  }

  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(`image request failed: ${response.status}`);

  if (cache) {
    try {
      await cache.put(url, response.clone());
      await trim(cache);
    } catch {
      /* quota, private mode, or a racing writer: the image still loads */
    }
  }

  return { objectUrl: URL.createObjectURL(await response.blob()), fromCache: false };
}

/** How many cutouts are currently held. Used by the UI to say so. */
export async function cachedImageCount(): Promise<number | null> {
  if (!cacheAvailable()) return null;
  try {
    return (await (await caches.open(CACHE_NAME)).keys()).length;
  } catch {
    return null;
  }
}
