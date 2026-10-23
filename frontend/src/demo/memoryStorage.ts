/**
 * Browsers that block site data throw from any access to `localStorage`, and MSW reads it while
 * it loads, which would leave the demo a blank page. Swap in an in-memory store when that
 * happens: the theme choice and the tour's dismissal then last until the page is reloaded.
 */
export function ensureStorage(): void {
  try {
    void window.localStorage;
    return;
  } catch {
    // Blocked: fall through to the replacement.
  }
  const items = new Map<string, string>();
  const memory: Storage = {
    get length() {
      return items.size;
    },
    clear: () => items.clear(),
    getItem: (key) => items.get(key) ?? null,
    key: (index) => [...items.keys()][index] ?? null,
    removeItem: (key) => void items.delete(key),
    setItem: (key, value) => void items.set(key, String(value)),
  };
  Object.defineProperty(window, "localStorage", { configurable: true, value: memory });
}
