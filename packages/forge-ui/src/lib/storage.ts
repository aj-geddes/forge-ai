/**
 * A safe wrapper around the browser `localStorage` API.
 *
 * `localStorage` is not always available or safe to touch at module import
 * time:
 * - During SSR, or in some test/runtime environments, `window` may not
 *   exist at all.
 * - Newer Node.js versions (22+) expose an experimental global
 *   `localStorage` that resolves to `undefined` unless the process was
 *   started with `--localstorage-file`. In test runners (e.g. vitest+jsdom)
 *   this global can shadow the real jsdom `Storage` implementation, so
 *   `window.localStorage` may be `undefined` even though the environment
 *   otherwise looks like a browser.
 * - Real browsers can throw `SecurityError` for opaque origins, disabled
 *   storage, or private-browsing quota limits.
 *
 * All reads/writes are wrapped so callers never need their own try/catch,
 * and modules that read persisted state at import time (e.g. zustand
 * stores) remain safe to import in any environment.
 */
function getWindowSafely(): (Window & typeof globalThis) | undefined {
  return typeof window === "undefined" ? undefined : window;
}

export const safeStorage = {
  getItem(key: string): string | null {
    try {
      return getWindowSafely()?.localStorage?.getItem(key) ?? null;
    } catch {
      return null;
    }
  },

  setItem(key: string, value: string): void {
    try {
      getWindowSafely()?.localStorage?.setItem(key, value);
    } catch {
      // Ignore write failures (quota exceeded, disabled storage, SSR, etc.)
    }
  },

  removeItem(key: string): void {
    try {
      getWindowSafely()?.localStorage?.removeItem(key);
    } catch {
      // Ignore removal failures.
    }
  },
};
