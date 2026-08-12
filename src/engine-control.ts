/**
 * Engine control bus: exposes safe, read-only-forward observability plus
 * user-requested toggles (scanner pause/resume + per-provider enable/disable)
 * to the dashboard HTTP layer. The scanner loop reads these each cycle; the
 * dashboard writes them via explicit POST routes. All state is in-memory.
 *
 * Start/stop here controls the in-process paper scanner loop, NOT any live
 * wallet or order flow — the bot has no real execution path.
 */
export type EngineControlState = {
  scannerRunning: boolean;
  /** 0 = no scan in progress; 1 = scanning; 2 = scanning + committing bilan. */
  scannerState: 'idle' | 'scanning' | 'committing';
  providers: Record<string, boolean>;
  lastScanAt?: string;
  cycles: number;
};

export class EngineControl {
  private scannerRunning = true;
  private scannerState: EngineControlState['scannerState'] = 'idle';
  // Bron van waarheid bij startup: leeg — syncProviders vult met de werkelijke
  // provider-set (TRITON; SOLANA_WS alleen als de lane geïnstantieerd is). De
  // oude hardcoded BIRDEYE/GECKO-defaults lieten het controle-paneel providers
  // tonen die niet bestaan (audit T3-B9).
  private providers: Record<string, boolean> = {};
  private cycles = 0;
  private lastScanAt?: string;

  /** Synchroniseer de zichtbare providers met de werkelijke provider-set. */
  syncProviders(providerEnabled: Readonly<Record<string, boolean>>): void {
    const next: Record<string, boolean> = {};
    for (const [name, enabled] of Object.entries(providerEnabled)) {
      next[name] = this.providers[name] ?? enabled;
    }
    this.providers = next;
  }

  setScannerRunning(running: boolean): void {
    this.scannerRunning = running;
  }
  isScannerRunning(): boolean {
    return this.scannerRunning;
  }
  setScannerState(state: EngineControlState['scannerState']): void {
    this.scannerState = state;
  }
  setProviderEnabled(name: string, enabled: boolean): void {
    const key = name.toUpperCase();
    if (key in this.providers) this.providers[key] = enabled;
  }
  noteScan(checkedAt: string): void {
    this.cycles += 1;
    this.lastScanAt = checkedAt;
  }
  state(): EngineControlState {
    return {
      scannerRunning: this.scannerRunning,
      scannerState: this.scannerState,
      providers: { ...this.providers },
      lastScanAt: this.lastScanAt,
      cycles: this.cycles,
    };
  }
}
