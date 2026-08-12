import { describe, expect, it } from 'vitest';
import { mkdtemp, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { PersistentLearnCycleGate } from '../src/learn-cycle-gate.js';

describe('PersistentLearnCycleGate', () => {
  it('checks the scheduler-state byte cap before parsing it', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'learn-cycle-gate-cap-'));
    await writeFile(join(directory, 'learn-scheduler-v2.json'), JSON.stringify({
      schemaVersion: 2,
      interval: 100,
      observedCompletedTrades: 0,
      lastProcessedMilestone: 0,
      padding: 'x'.repeat(65_536),
    }));

    expect(() => new PersistentLearnCycleGate(directory, 100, async () => {})).toThrow(/scheduler state.*byte/i);
  });

  it('persists milestones across restart and serializes analyses', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'learn-cycle-gate-'));
    let active = 0;
    let maxActive = 0;
    let calls = 0;
    const analyze = async () => {
      active += 1;
      maxActive = Math.max(maxActive, active);
      calls += 1;
      await new Promise<void>((resolve) => setTimeout(resolve, 2));
      active -= 1;
    };

    const gate = new PersistentLearnCycleGate(directory, 100, analyze);
    await Promise.all([gate.observeCompleted(100), gate.observeCompleted(200)]);
    expect(calls).toBe(2);
    expect(maxActive).toBe(1);

    let restartCalls = 0;
    const restarted = new PersistentLearnCycleGate(directory, 100, async () => { restartCalls += 1; });
    await restarted.resume();
    expect(restartCalls).toBe(0);
    await restarted.observeCompleted(300);
    expect(restartCalls).toBe(1);
  });

  it('processes every missed milestone in a direct 300-trade jump', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'learn-cycle-gate-'));
    let calls = 0;
    const gate = new PersistentLearnCycleGate(directory, 100, async () => { calls += 1; });
    await gate.observeCompleted(300);
    expect(calls).toBe(3);
  });

  it('does not lose a milestone queued during idle-drain cleanup', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'learn-cycle-gate-'));
    let calls = 0;
    const gate = new PersistentLearnCycleGate(directory, 100, async () => { calls += 1; });
    const idleResume = gate.resume();
    const queuedMilestone = gate.observeCompleted(100);
    await Promise.all([idleResume, queuedMilestone]);
    expect(calls).toBe(1);
  });

  it('reconciles a missing scheduler sidecar and retries a failed analysis on a later quiet cycle', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'learn-cycle-gate-'));
    let calls = 0;
    const gate = new PersistentLearnCycleGate(directory, 100, async () => {
      calls += 1;
      if (calls === 1) throw new Error('temporary analysis failure');
    }, 0);
    await expect(gate.observeCompleted(100)).rejects.toThrow('temporary analysis failure');
    await gate.observeCompleted(100);
    expect(calls).toBe(2);
  });

  it('does not spin the event loop while a production-style retry backoff is active', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'learn-cycle-gate-'));
    let calls = 0;
    const gate = new PersistentLearnCycleGate(directory, 100, async () => {
      calls += 1;
      throw new Error('temporary analysis failure');
    }, 50);
    await expect(gate.observeCompleted(100)).rejects.toThrow('temporary analysis failure');
    let timerFired = false;
    const timer = new Promise<void>((resolve) => setTimeout(() => { timerFired = true; resolve(); }, 5));
    await gate.observeCompleted(100);
    await timer;
    expect(timerFired).toBe(true);
    expect(calls).toBe(1);
  });

  it('does not advance in-memory observed total when its durable commit fails', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'learn-cycle-gate-'));
    const gate = new PersistentLearnCycleGate(directory, 100, async () => {});
    const internal = gate as unknown as {
      observedCompletedTrades: number;
      commitState(observed: number, watermark: number): void;
    };
    internal.commitState = () => { throw new Error('simulated rename failure'); };
    expect(() => gate.observeCompleted(100)).toThrow('simulated rename failure');
    expect(internal.observedCompletedTrades).toBe(0);
  });

  it('does not advance in-memory watermark when its durable commit fails after analysis', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'learn-cycle-gate-'));
    let releaseAnalysis!: () => void;
    const gate = new PersistentLearnCycleGate(directory, 100, async () => {
      await new Promise<void>((resolve) => { releaseAnalysis = resolve; });
    });
    const pending = gate.observeCompleted(100);
    const internal = gate as unknown as {
      lastProcessedMilestone: number;
      commitState(observed: number, watermark: number): void;
    };
    internal.commitState = () => { throw new Error('simulated watermark rename failure'); };
    releaseAnalysis();
    await expect(pending).rejects.toThrow('simulated watermark rename failure');
    expect(internal.lastProcessedMilestone).toBe(0);
  });
});
