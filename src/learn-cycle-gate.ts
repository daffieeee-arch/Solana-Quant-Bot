import { mkdirSync, readFileSync, renameSync, statSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

type LearnSchedulerState = {
  schemaVersion: 2;
  interval: number;
  observedCompletedTrades: number;
  lastProcessedMilestone: number;
};
const MAX_SCHEDULER_STATE_BYTES = 64 * 1024;

export class PersistentLearnCycleGate {
  private readonly statePath: string;
  private observedCompletedTrades: number;
  private lastProcessedMilestone: number;
  private inFlight?: Promise<void>;
  private retryNotBeforeMs = 0;

  constructor(
    dataDir: string,
    private readonly interval: number,
    private readonly analyze: () => Promise<void>,
    private readonly retryDelayMs = 30_000,
  ) {
    if (!Number.isInteger(interval) || interval <= 0) throw new Error('Learn cycle interval must be a positive integer');
    if (!Number.isInteger(retryDelayMs) || retryDelayMs < 0) throw new Error('Retry delay must be a non-negative integer');
    mkdirSync(dataDir, { recursive: true });
    this.statePath = join(dataDir, 'learn-scheduler-v2.json');
    const state = this.loadState();
    this.observedCompletedTrades = state.observedCompletedTrades;
    this.lastProcessedMilestone = state.lastProcessedMilestone;
  }

  resume(): Promise<void> {
    return this.scheduleDrain();
  }

  observeCompleted(total: number): Promise<void> {
    if (!Number.isInteger(total) || total < 0) throw new Error('Completed trade total must be a non-negative integer');
    if (total < this.observedCompletedTrades) throw new Error('Completed trade total cannot move backwards');
    if (total > this.observedCompletedTrades) {
      this.commitState(total, this.lastProcessedMilestone);
    }
    return this.scheduleDrain();
  }

  private scheduleDrain(): Promise<void> {
    if (!this.inFlight) this.inFlight = this.runDrainOwner();
    return this.inFlight;
  }

  private async runDrainOwner(): Promise<void> {
    try {
      while (true) {
        const status = await this.drain();
        if (status === 'backoff') return;
        if (this.lastProcessedMilestone + this.interval > this.observedCompletedTrades) return;
      }
    } finally {
      this.inFlight = undefined;
    }
  }

  private async drain(): Promise<'idle' | 'backoff'> {
    while (this.lastProcessedMilestone + this.interval <= this.observedCompletedTrades) {
      if (Date.now() < this.retryNotBeforeMs) return 'backoff';
      const nextMilestone = this.lastProcessedMilestone + this.interval;
      try {
        await this.analyze();
      } catch (error) {
        this.retryNotBeforeMs = Date.now() + this.retryDelayMs;
        throw error;
      }
      this.commitState(this.observedCompletedTrades, nextMilestone);
      this.retryNotBeforeMs = 0;
    }
    return 'idle';
  }

  private loadState(): LearnSchedulerState {
    try {
      if (statSync(this.statePath).size > MAX_SCHEDULER_STATE_BYTES) {
        throw new Error('Learning scheduler state exceeds its byte limit');
      }
      const parsed = JSON.parse(readFileSync(this.statePath, 'utf8')) as Partial<LearnSchedulerState>;
      const valid = parsed.schemaVersion === 2
        && parsed.interval === this.interval
        && Number.isInteger(parsed.observedCompletedTrades)
        && (parsed.observedCompletedTrades ?? -1) >= 0
        && Number.isInteger(parsed.lastProcessedMilestone)
        && (parsed.lastProcessedMilestone ?? -1) >= 0
        && (parsed.lastProcessedMilestone ?? 0) % this.interval === 0
        && (parsed.lastProcessedMilestone ?? 0) <= (parsed.observedCompletedTrades ?? -1);
      if (!valid) throw new Error('Invalid learning scheduler state');
      return parsed as LearnSchedulerState;
    } catch (error) {
      if (typeof error === 'object' && error !== null && 'code' in error && error.code === 'ENOENT') {
        return { schemaVersion: 2, interval: this.interval, observedCompletedTrades: 0, lastProcessedMilestone: 0 };
      }
      throw error;
    }
  }

  private commitState(observedCompletedTrades: number, lastProcessedMilestone: number): void {
    const state: LearnSchedulerState = {
      schemaVersion: 2,
      interval: this.interval,
      observedCompletedTrades,
      lastProcessedMilestone,
    };
    const serialized = JSON.stringify(state, null, 2);
    if (Buffer.byteLength(serialized, 'utf8') > MAX_SCHEDULER_STATE_BYTES) {
      throw new Error('Learning scheduler state exceeds its byte limit');
    }
    const temporaryPath = `${this.statePath}.tmp`;
    writeFileSync(temporaryPath, serialized, { mode: 0o600 });
    renameSync(temporaryPath, this.statePath);
    this.observedCompletedTrades = observedCompletedTrades;
    this.lastProcessedMilestone = lastProcessedMilestone;
  }
}
