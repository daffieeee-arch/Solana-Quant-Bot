import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const lease = JSON.parse(readFileSync('docs/research/B4_ENGINEERING_VALIDATION_LEASE_PLAN.json', 'utf8'));
const manifest = readFileSync('rust/of1-range-recorder/Cargo.toml', 'utf8');
const cli = readFileSync('rust/of1-range-recorder/src/bin/of1-acquire.rs', 'utf8');

describe('OF1 staged implementation does not authorize or widen the lease', () => {
  it('preserves all original aggregate hard caps and separate missing approvals', () => {
    expect([lease.approved, lease.networkEnabled, lease.readyToRun, lease.executablePlan]).toEqual([false, false, false, false]);
    expect(lease.cost_confirmation).toBe('NOT_CONFIRMED');
    expect(lease.budget).toMatchObject({ maxRequests: 16, requestRetries: 2, concurrency: 1,
      maxResponseEntityBytes: 16_777_216, maxResponseBytesTotal: 134_217_728,
      maxDiskBytes: 268_435_456, requiredFreeDiskBytes: 536_870_912,
      maxMemoryBytes: 536_870_912, maxRuntimeMs: 1_800_000, responseTimeoutMs: 30_000 });
    for (const stage of Object.values(lease.stages) as Array<Record<string, unknown>>) {
      expect(stage.approved).toBe(false);
      expect(stage.approvalId).toBeNull();
      expect(stage.allocatedCaps).toBeNull();
    }
  });
  it('keeps the exact modern metadata inventory and checked retry arithmetic', () => {
    expect(lease.stages.metadata.inventory.map((r: { method: string; path: string }) => [r.method, r.path])).toEqual([
      ['GET', '/978/epoch-978-slot-ranges.raw'], ['GET', '/978/epoch-978.sha256'],
      ['GET', '/978/epoch-978.cid'], ['HEAD', '/978/epoch-978.car'],
    ]);
    const allowance = 432_000 * 12 + 2 * 4096;
    expect(lease.budgetBasis.metadataRetryEnvelopeEntityAllowance).toBe(allowance * 3);
    expect(lease.budgetBasis.remainingAggregateEntityAllowanceAfterMetadataEnvelope).toBe(134_217_728 - allowance * 3);
    expect(lease.budgetBasis.remainingAggregateRequestsAfterMetadataEnvelope).toBe(16 - 4 * 3);
  });
  it('keeps official transport opt-in and no automatic next stage', () => {
    expect(manifest).toContain('default = []');
    expect(manifest).toContain('network-of1 = ["dep:rustls", "dep:webpki-roots"]');
    expect(manifest).toContain('required-features = ["tls-fixture"]');
    expect(cli).toContain('STOP_FOR_REVIEW_NO_AUTOMATIC_NEXT_STAGE');
    expect(cli).toContain('network-of1 capability disabled; no store mutation or connection attempted');
  });
  it('does not promote engineering mechanics to market falsification or zero domain counts', () => {
    expect(lease.outcomes.dataSufficiency).toBe('UNAVAILABLE_NOT_DECODED_IN_B4');
    expect(lease.outcomes.edgeEvaluation).toBe('NOT_EVALUATED_ENGINEERING_SLICE');
    expect(lease.evidenceLimits.b4ProjectEvidence).toBe('Unproven');
    expect(lease.evidenceLimits.rootToSlotMembership).toBe('UNAVAILABLE');
    expect(lease.evidenceLimits.authenticSourceObservation).toBe('UNAVAILABLE');
  });
});
