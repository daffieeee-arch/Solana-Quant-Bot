/**
 * Tier 3 — live-market confirmation latency & price drift modeling.
 *
 * A real transaction is not filled at the instant a quote is observed: it must be
 * built, signed, submitted via gRPC/Cascade, land in a slot, and confirm. During
 * that window the mark price moves. This module models:
 *   - confirmation latency (ms) as a bounded random variable with a floor
 *   - a small adverse price drift applied between the observed quote and the
 *     modeled fill, so paper fills reflect that a real bot is a step behind the
 *     quote it saw.
 *
 * Deterministic when a seeded RNG is supplied (for tests); uses Math.random
 * otherwise.
 */

export type LatencyConfig = {
  /** Minimum confirmation latency in ms (network + client build + submit). */
  minMs: number;
  /** Maximum additional random latency in ms (uniform-ish jitter). */
  jitterMs: number;
  /** Max adverse drift (bps) the price can move during confirmation. 0..1000. */
  maxAdverseDriftBps: number;
};

export const DEFAULT_LATENCY: LatencyConfig = {
  minMs: 250,
  jitterMs: 750,
  maxAdverseDriftBps: 20, // up to 0.2% adverse drift during ~0.25-1s confirmation
};

export type Rng = () => number;

/**
 * Sample a confirmation latency in ms: `minMs + floor(rng() * jitterMs)`.
 * Bounded, never negative.
 */
export function sampleLatencyMs(config: LatencyConfig, rng: Rng = Math.random): number {
  const minMs = Number.isFinite(config.minMs) && config.minMs >= 0 ? Math.round(config.minMs) : 0;
  const jitterMs = Number.isFinite(config.jitterMs) && config.jitterMs >= 0 ? config.jitterMs : 0;
  return minMs + Math.floor(rng() * jitterMs);
}

/**
 * Compute the adverse drift multiplier applied to a fill price over the
 * confirmation window. Returns 1 + drift (for buys, entry is worse/higher) or
 * 1 - drift (for sells, exit is worse/lower). Clamped to maxAdverseDriftBps.
 */
export function confirmationDriftMultiplier(
  config: LatencyConfig,
  rng: Rng = Math.random,
): number {
  const maxBps = Number.isFinite(config.maxAdverseDriftBps) && config.maxAdverseDriftBps >= 0
    ? Math.min(1000, config.maxAdverseDriftBps)
    : 0;
  const driftBps = rng() * maxBps;
  return 1 + driftBps / 10_000;
}

/** Combined: fill timestamp = quoteObservedAt + latency. Returns ISO string. */
export function confirmationFillAt(
  quoteObservedAt: string,
  config: LatencyConfig,
  rng: Rng = Math.random,
): string {
  const latencyMs = sampleLatencyMs(config, rng);
  return new Date(Date.parse(quoteObservedAt) + latencyMs).toISOString();
}

/** Validate latency config; returns defaults per missing/invalid field. */
export function normalizeLatencyConfig(config: Partial<LatencyConfig> | undefined): LatencyConfig {
  const c: LatencyConfig = { ...DEFAULT_LATENCY, ...(config ?? {}) };
  if (!Number.isFinite(c.minMs) || c.minMs < 0) c.minMs = DEFAULT_LATENCY.minMs;
  if (!Number.isFinite(c.jitterMs) || c.jitterMs < 0) c.jitterMs = DEFAULT_LATENCY.jitterMs;
  if (!Number.isFinite(c.maxAdverseDriftBps) || c.maxAdverseDriftBps < 0 || c.maxAdverseDriftBps > 1000) {
    c.maxAdverseDriftBps = DEFAULT_LATENCY.maxAdverseDriftBps;
  }
  return c;
}
