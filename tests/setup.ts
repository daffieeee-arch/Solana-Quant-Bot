// Vitest setup: globale localStorage-mock (jsdom's native storage faalt in
// headless-testomstandigheden met "SecurityError: localStorage is not available
// for opaque origins"). App.tsx leest/schrijft 'theme' via localStorage.
class LocalStorageMock {
  private store = new Map<string, string>();
  getItem(key: string): string | null { return this.store.has(key) ? this.store.get(key)! : null; }
  setItem(key: string, value: string): void { this.store.set(key, String(value)); }
  removeItem(key: string): void { this.store.delete(key); }
  clear(): void { this.store.clear(); }
  get length(): number { return this.store.size; }
  key(i: number): string | null { return Array.from(this.store.keys())[i] ?? null; }
}

if (typeof globalThis !== 'undefined') {
  (globalThis as { localStorage?: unknown }).localStorage ??= new LocalStorageMock();
  (globalThis as { sessionStorage?: unknown }).sessionStorage ??= new LocalStorageMock();
}