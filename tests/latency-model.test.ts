import { describe, it, expect } from 'vitest';
import {
  sampleLatencyMs,
  confirmationDriftMultiplier,
  confirmationFillAt,
  normalizeLatencyConfig,
  DEFAULT_LATENCY,
} from '../src/latency-model.js';

const rng0 = () => 0;
const rng1 = () => 1;

describe('sampleLatencyMs', () => {
  it('is bounded between minMs and minMs+jitterMs', () => {
    const lo = sampleLatencyMs(DEFAULT_LATENCY, rng0);
    const hi = sampleLatencyMs(DEFAULT_LATENCY, rng1);
    expect(lo).toBeGreaterThanOrEqual(DEFAULT_LATENCY.minMs);
    expect(hi).toBeLessThanOrEqual(DEFAULT_LATENCY.minMs + DEFAULT_LATENCY.jitterMs);
  });

  it('never returns negative even for weird config', () => {
    expect(sampleLatencyMs({ minMs: -5, jitterMs: -3, maxAdverseDriftBps: 10 }, rng0)).toBeGreaterThanOrEqual(0);
  });
});

describe('confirmationDriftMultiplier', () => {
  it('with rng=0 yields no drift (multiplier 1)', () => {
    expect(confirmationDriftMultiplier({ ...DEFAULT_LATENCY, maxAdverseDriftBps: 20 }, rng0)).toBe(1);
  });

  it('with rng=1 yields exactly max drift upward', () => {
    expect(confirmationDriftMultiplier({ ...DEFAULT_LATENCY, maxAdverseDriftBps: 20 }, rng1)).toBe(1 + 20 / 10_000);
  });

  it('clamps maxAdverseDriftBps to <=1000', () => {
    expect(confirmationDriftMultiplier({ ...DEFAULT_LATENCY, maxAdverseDriftBps: 5000 }, rng1)).toBe(1 + 1000 / 10_000);
  });
});

describe('confirmationFillAt', () => {
  it('yields a later timestamp than the observed quote', () => {
    const observed = '2026-08-06T10:00:00.000Z';
    const filledAt = confirmationFillAt(observed, DEFAULT_LATENCY, rng0);
    expect(Date.parse(filledAt)).toBeGreaterThan(Date.parse(observed));
  });
});

describe('normalizeLatencyConfig', () => {
  it('fills defaults from missing fields', () => {
    const c = normalizeLatencyConfig(undefined);
    expect(c).toEqual(DEFAULT_LATENCY);
  });

  it('clamps invalid values to defaults', () => {
    const c = normalizeLatencyConfig({ minMs: -10, maxAdverseDriftBps: 5000 });
    expect(c.minMs).toBe(DEFAULT_LATENCY.minMs);
    expect(c.maxAdverseDriftBps).toBe(DEFAULT_LATENCY.maxAdverseDriftBps);
  });
});
