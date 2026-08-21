import { describe, expect, it } from 'vitest';
import {
  PHASE8A_ELIGIBILITY_STATUS,
  validatePhase8AEligibility,
} from '../src/phase8a-eligibility.js';

const clone = () => structuredClone(PHASE8A_ELIGIBILITY_STATUS) as Record<string, any>;

describe('Phase 8A split eligibility contract', () => {
  it('keeps a working fixture contract separate from every real eligibility gate', () => {
    expect(PHASE8A_ELIGIBILITY_STATUS).toEqual({
      schemaVersion: 'PHASE8A_ELIGIBILITY_STATUS_1',
      pilotEligible: false,
      transportPilot: {
        contractReady: true,
        inputMode: 'SYNTHETIC_FIXTURE_ONLY',
        preflightStatus: 'NOT_RUN',
        eligible: false,
        executionAuthorized: false,
        reasonCodes: ['SYNTHETIC_FIXTURE_ONLY', 'PREFLIGHT_NOT_RUN', 'EXECUTION_NOT_AUTHORIZED'],
      },
      acceptedSilver: {
        eligible: false,
        activationVerdict: 'HOLD_UNPROVEN_ACTIVATION',
        provenRegistryEntries: 0,
        totalRegistryEntries: 10,
        reasonCodes: ['NO_PROVEN_REGISTRY_ENTRIES', 'UNPROVEN_ACTIVATION'],
      },
      research: {
        approved: false,
        researchReady: false,
        strategyInputEligible: false,
        profitabilityEvidence: false,
        reasonCodes: ['FIXTURE_ONLY', 'NOT_APPROVED', 'NOT_STRATEGY_INPUT'],
      },
    });
    expect(validatePhase8AEligibility(PHASE8A_ELIGIBILITY_STATUS)).toEqual([]);
    expect(Object.isFrozen(PHASE8A_ELIGIBILITY_STATUS)).toBe(true);
    expect(Object.isFrozen(PHASE8A_ELIGIBILITY_STATUS.transportPilot)).toBe(true);
  });

  it.each([
    ['transport execution authorization', (v: any) => { v.transportPilot.executionAuthorized = true; }],
    ['transport eligibility', (v: any) => { v.transportPilot.eligible = true; }],
    ['accepted Silver', (v: any) => { v.acceptedSilver.eligible = true; }],
    ['proven registry entries', (v: any) => { v.acceptedSilver.provenRegistryEntries = 1; }],
    ['research readiness', (v: any) => { v.research.researchReady = true; }],
    ['legacy pilot eligibility', (v: any) => { v.pilotEligible = true; }],
    ['strategy input eligibility', (v: any) => { v.research.strategyInputEligible = true; }],
    ['approval', (v: any) => { v.research.approved = true; }],
    ['profitability evidence', (v: any) => { v.research.profitabilityEvidence = true; }],
    ['preflight completion', (v: any) => { v.transportPilot.preflightStatus = 'PASS'; }],
    ['real source mode', (v: any) => { v.transportPilot.inputMode = 'REAL'; }],
  ])('rejects escalation through %s', (_name, mutate) => {
    const candidate = clone();
    mutate(candidate);
    expect(validatePhase8AEligibility(candidate).join('\n')).toMatch(/mismatch|forbidden|false|exact/i);
  });

  it('rejects missing, extra, sparse, accessor-backed and capability-shaped input', () => {
    const missing = clone();
    delete missing.acceptedSilver;
    expect(validatePhase8AEligibility(missing)).not.toEqual([]);

    const extra = clone();
    extra.transportPilot.network = 'http';
    expect(validatePhase8AEligibility(extra)).not.toEqual([]);

    const sparse = clone();
    sparse.transportPilot.reasonCodes = new Array(3);
    expect(validatePhase8AEligibility(sparse)).not.toEqual([]);

    let getterRuns = 0;
    const accessor = clone();
    Object.defineProperty(accessor.transportPilot, 'eligible', {
      enumerable: true,
      get() { getterRuns += 1; return false; },
    });
    expect(validatePhase8AEligibility(accessor)).not.toEqual([]);
    expect(getterRuns).toBe(0);

    expect(validatePhase8AEligibility(() => undefined)).not.toEqual([]);
  });
});
