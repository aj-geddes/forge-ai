import "@testing-library/jest-dom/vitest";

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

/**
 * Minimal spec-compliant in-memory `Storage` polyfill.
 *
 * Node 22+ ships an experimental global `localStorage` that resolves to
 * `undefined` unless the process is started with `--localstorage-file`.
 * Vitest's jsdom environment only copies a `window` property from jsdom
 * when it is not already present on Node's `globalThis`, so this broken
 * Node global shadows jsdom's real `Storage` implementation and
 * `window.localStorage` ends up unusable in tests. Install a working
 * polyfill so tests can exercise real persistence behavior.
 */
class MemoryStorage implements Storage {
  #store = new Map<string, string>();

  get length(): number {
    return this.#store.size;
  }

  clear(): void {
    this.#store.clear();
  }

  getItem(key: string): string | null {
    return this.#store.has(key) ? (this.#store.get(key) ?? null) : null;
  }

  key(index: number): string | null {
    return Array.from(this.#store.keys())[index] ?? null;
  }

  removeItem(key: string): void {
    this.#store.delete(key);
  }

  setItem(key: string, value: string): void {
    this.#store.set(key, String(value));
  }
}

function isUsableStorage(storage: Storage | undefined): boolean {
  if (!storage) return false;
  try {
    const probeKey = "__forge_storage_probe__";
    storage.setItem(probeKey, "1");
    storage.removeItem(probeKey);
    return true;
  } catch {
    return false;
  }
}

if (typeof window !== "undefined" && !isUsableStorage(window.localStorage)) {
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    enumerable: true,
    value: new MemoryStorage(),
  });
}
