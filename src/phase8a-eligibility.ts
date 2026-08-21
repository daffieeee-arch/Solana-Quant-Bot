export type Phase8AEligibilityStatus = Readonly<{
  schemaVersion: 'PHASE8A_ELIGIBILITY_STATUS_1';
  pilotEligible: false;
  transportPilot: Readonly<{
    contractReady: true;
    inputMode: 'SYNTHETIC_FIXTURE_ONLY';
    preflightStatus: 'NOT_RUN';
    eligible: false;
    executionAuthorized: false;
    reasonCodes: readonly ['SYNTHETIC_FIXTURE_ONLY', 'PREFLIGHT_NOT_RUN', 'EXECUTION_NOT_AUTHORIZED'];
  }>;
  acceptedSilver: Readonly<{
    eligible: false;
    activationVerdict: 'HOLD_UNPROVEN_ACTIVATION';
    provenRegistryEntries: 0;
    totalRegistryEntries: 10;
    reasonCodes: readonly ['NO_PROVEN_REGISTRY_ENTRIES', 'UNPROVEN_ACTIVATION'];
  }>;
  research: Readonly<{
    approved: false;
    researchReady: false;
    strategyInputEligible: false;
    profitabilityEvidence: false;
    reasonCodes: readonly ['FIXTURE_ONLY', 'NOT_APPROVED', 'NOT_STRATEGY_INPUT'];
  }>;
}>;

function deepFreeze<T>(value: T): T {
  if (typeof value !== 'object' || value === null || Object.isFrozen(value)) return value;
  for (const descriptor of Object.values(Object.getOwnPropertyDescriptors(value))) {
    if (Object.hasOwn(descriptor, 'value')) deepFreeze(descriptor.value);
  }
  return Object.freeze(value);
}

export const PHASE8A_ELIGIBILITY_STATUS: Phase8AEligibilityStatus = deepFreeze({
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
} as const);

function compareExact(candidate: unknown, expected: unknown, path: string, errors: string[]): void {
  if (expected === null || typeof expected !== 'object') {
    if (candidate !== expected) errors.push(`${path}: exact value mismatch`);
    return;
  }
  if (typeof candidate !== 'object' || candidate === null) {
    errors.push(`${path}: exact object mismatch`);
    return;
  }
  const expectedArray = Array.isArray(expected);
  if (Array.isArray(candidate) !== expectedArray) {
    errors.push(`${path}: exact container mismatch`);
    return;
  }
  try {
    const candidateDescriptors = Object.getOwnPropertyDescriptors(candidate);
    const expectedDescriptors = Object.getOwnPropertyDescriptors(expected);
    const expectedKeys = Object.keys(expectedDescriptors)
      .filter((key) => expectedArray ? key !== 'length' : true)
      .sort();
    const candidateKeys = Object.keys(candidateDescriptors)
      .filter((key) => expectedArray ? key !== 'length' : true)
      .sort();
    if (JSON.stringify(candidateKeys) !== JSON.stringify(expectedKeys)) {
      errors.push(`${path}: exact key set mismatch`);
      return;
    }
    if (expectedArray && (candidate as unknown[]).length !== (expected as unknown[]).length) {
      errors.push(`${path}: exact array length mismatch`);
      return;
    }
    for (const key of expectedKeys) {
      const actual = candidateDescriptors[key];
      const wanted = expectedDescriptors[key];
      if (!actual || !Object.hasOwn(actual, 'value') || actual.enumerable !== wanted.enumerable) {
        errors.push(`${path}.${key}: accessor or descriptor mismatch`);
        continue;
      }
      compareExact(actual.value, wanted.value, `${path}.${key}`, errors);
    }
  } catch {
    errors.push(`${path}: unsafe capability-shaped input`);
  }
}

export function validatePhase8AEligibility(value: unknown): string[] {
  const errors: string[] = [];
  compareExact(value, PHASE8A_ELIGIBILITY_STATUS, 'eligibility', errors);
  return [...new Set(errors)].sort();
}
