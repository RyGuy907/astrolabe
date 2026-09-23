/**
 * localStorage, guarded.
 *
 * Everything this app keeps between visits -- the observing sites, the
 * history, the layout preferences -- lives in the browser, because the server
 * stores nothing. localStorage can throw (a private window, blocked site
 * data, a full quota), so every access goes through here and a failure reads
 * as "nothing saved" rather than breaking the page.
 */

export function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

/** False when the write failed, so a caller that must not lose data can say so. */
export function writeJson(key: string, value: unknown): boolean {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}

/** True when the key has ever been written -- told apart from an empty value. */
export function hasKey(key: string): boolean {
  try {
    return window.localStorage.getItem(key) !== null;
  } catch {
    return false;
  }
}
