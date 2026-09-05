import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const PLAN_PATH = 'docs/research/B4_ENGINEERING_VALIDATION_LEASE_PLAN.json';
const DOC_PATH = 'docs/research/B4_ENGINEERING_VALIDATION_LEASE_PLAN.md';

describe('B4 engineering-validation lease plan draft', () => {
  const plan = JSON.parse(readFileSync(PLAN_PATH, 'utf8'));
  const doc = readFileSync(DOC_PATH, 'utf8');

  it('stays unapproved and offline', () => {
    expect(plan.schemaVersion).toBe('B4_ENGINEERING_VALIDATION_LEASE_PLAN_1');
    expect(plan.approved).toBe(false);
    expect(plan.networkEnabled).toBe(false);
    expect(plan.creditSpendAuthorized).toBe(false);
    expect(plan.cost_confirmation).toBe('NOT_CONFIRMED');
    expect(plan.operator).toBeNull();
    expect(plan.approved_at).toBeNull();
    expect(plan.host).toBe('files.old-faithful.net');
    expect(plan.hostAllowlist).toEqual(['files.old-faithful.net']);
    expect(plan.redirectsAllowed).toBe(false);
    expect(plan.s3Allowed).toBe(false);
    expect(plan.hostedGrpcAllowed).toBe(false);
    expect(plan.purpose).toBe('ENGINEERING_VALIDATION_ONLY');
  });

  it('records a 128-slot epoch-978 window and refuses the old provisional example', () => {
    expect(plan.epoch).toBe(978);
    expect(plan.slot_range).toMatchObject({
      startInclusive: 422_496_000,
      endExclusive: 422_496_128,
      requestedSlots: 128,
      objectKind: 'SLOT_WINDOW',
      selectionPolicy: 'SOURCE_AND_ENGINEERING_PROPERTIES_ONLY',
    });
    expect(plan.slot_range.startInclusive).toBe(978 * 432_000);
    expect(plan.slot_range.rejectedProvisionalExample).toEqual({
      startInclusive: 422_506_000,
      endExclusive: 422_506_128,
      reason: 'Must not be fetched because it appeared in earlier docs without origin/class.',
    });
    expect(plan.objectIdentities.epochCarSha256).toBe('UNAVAILABLE');
  });

  it('names hard budgets and a non-expansion hard stop', () => {
    expect(plan.budget.maxRequests).toBe(16);
    expect(plan.budget.concurrency).toBe(1);
    expect(plan.budget.maxResponseEntityBytes).toBe(16 * 1024 * 1024);
    expect(plan.budget.maxResponseBytesTotal).toBe(128 * 1024 * 1024);
    expect(plan.budget.maxDiskBytes).toBe(256 * 1024 * 1024);
    expect(plan.budget.maxRuntimeMs).toBe(30 * 60 * 1000);
    expect(plan.hard_stop.join(' ')).toMatch(/Do not auto-expand/i);
    expect(plan.expectedTerminalStates).toContain('INSUFFICIENT_SAMPLE');
  });

  it('keeps the prose draft from authorizing a call', () => {
    expect(doc).toContain('`approved: false`');
    expect(doc).toContain('`networkEnabled: false`');
    expect(doc).toContain('[422496000, 422496128)');
    expect(doc).not.toMatch(/(?:Pilot A|bandwidth(?:-cap)? preflight)\s*:\s*(?:authorized|approved|GO)\b/i);
  });
});
