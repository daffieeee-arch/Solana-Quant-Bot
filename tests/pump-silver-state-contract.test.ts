import { readFileSync } from 'node:fs';
import bs58 from 'bs58';
import { describe, expect, it } from 'vitest';
import { PUMP_PROGRAM_ID } from '../src/pump-address.js';
import {
  PUMP_SILVER_STATE_OBSERVABILITY_CONTRACT,
  PUMP_SILVER_STATE_QUARANTINE_REASONS,
  evaluatePumpSilverStateFixture,
  evaluatePumpSilverStateFromBronze,
  validatePumpSilverObservabilitySignal,
} from '../src/research/pump-silver-state-contract.js';
import { TOKEN_2022_PROGRAM, bronzeFixture, creator, mintAuthority } from './fixtures/pump-silver/fixture.js';
import {
  rebindStateEvidenceHashes,
  stateEventBindingFromBronze,
  validBuyStateEventBinding,
  validBuyStateEvidence,
  validStateEventBinding,
  validStateEvidence,
  validToken2022StateEventBinding,
} from './fixtures/pump-silver/state-fixture.js';

function evidence(): ReturnType<typeof validBuyStateEvidence> {
  return structuredClone(validBuyStateEvidence());
}

function writeSnapshotU64(
  value: ReturnType<typeof validBuyStateEvidence>,
  snapshotIndex: number,
  offset: number,
  amount: bigint,
): void {
  const bytes = Buffer.from(value.snapshots[snapshotIndex]!.dataHex, 'hex');
  bytes.writeBigUInt64LE(amount, offset);
  value.snapshots[snapshotIndex]!.dataHex = bytes.toString('hex');
  rebindStateEvidenceHashes(value);
}

function reasons(value: ReturnType<typeof validBuyStateEvidence>) {
  return evaluatePumpSilverStateFromBronze(bronzeFixture('buy'), value).quarantineReasons;
}

const goldenVectors = JSON.parse(readFileSync(
  new URL('./fixtures/pump-silver/state-vectors.json', import.meta.url),
  'utf8',
)) as {
  vectors: Array<{ name: string; eventBinding: unknown; evidence: unknown; expected: unknown }>;
  adversarialVectors: Array<{ name: string; eventBinding: unknown; evidence: unknown; expected: unknown }>;
};

function failedRollbackVector() {
  const vector = goldenVectors.adversarialVectors.find((candidate) => candidate.name === 'failed_transaction_exact_rollback');
  if (!vector) throw new Error('missing failed rollback vector');
  return structuredClone(vector) as {
    eventBinding: ReturnType<typeof validBuyStateEventBinding>;
    evidence: ReturnType<typeof validBuyStateEvidence>;
  };
}

const SYNTHETIC_ACCOUNT_RENT_RESERVE = 1_000_000n;

function bindCompleteAccountState(
  eventBinding: ReturnType<typeof validBuyStateEventBinding>,
  stateEvidence: ReturnType<typeof validBuyStateEvidence>,
): void {
  const fixedLamportsByRole: Record<string, string> = {
    mint: '1500000',
    base_bonding_curve_token_account: '2100000',
    quote_bonding_curve_token_account: '2100000',
    bonding_curve: '2000000',
  };
  for (const snapshot of stateEvidence.snapshots) {
    const record = snapshot as unknown as Record<string, unknown>;
    record.executable = false;
    record.stateAuthority = 'RAW_ACCOUNT_STATE';
    record.lamports = snapshot.accountRole === 'bonding_curve'
      && eventBinding.quoteBondingCurveTokenAccount === null
      ? (Buffer.from(snapshot.dataHex, 'hex').readBigUInt64LE(32) + SYNTHETIC_ACCOUNT_RENT_RESERVE).toString()
      : fixedLamportsByRole[snapshot.accountRole];
  }
  rebindStateEvidenceHashes(stateEvidence);
}

describe('Phase 6B Pump Silver state/provenance fixture contract', () => {
  it.each(goldenVectors.vectors)('replays shared normalized vector $name byte-for-byte', (vector) => {
    expect(evaluatePumpSilverStateFixture(vector.eventBinding, vector.evidence)).toEqual(vector.expected);
  });
  it.each(goldenVectors.adversarialVectors)('matches shared quarantine vector $name byte-for-byte', (vector) => {
    expect(evaluatePumpSilverStateFixture(vector.eventBinding, vector.evidence)).toEqual(vector.expected);
  });
  it('accepts one exact-byte synthetic legacy buy transition while remaining not research-ready', () => {
    const result = evaluatePumpSilverStateFromBronze(bronzeFixture('buy'), validBuyStateEvidence());

    expect(result.status).toBe('FIXTURE_VALID');
    expect(result.approved).toBe(false);
    expect(result.researchReady).toBe(false);
    expect(result.pilotEligible).toBe(false);
    expect(result.quarantineReasons).toEqual([]);
    expect(result.state?.before.bondingCurve.realTokenReserves).toBe('100000000');
    expect(result.state?.after.bondingCurve.realTokenReserves).toBe('98000000');
    expect(result.state?.before.mint.supply).toBe('1000000000000000');
    expect(result.state?.after.mint.decimals).toBe(6);
  });

  it('accepts complete instruction-boundary account state with native quote lamports', () => {
    const eventBinding = validBuyStateEventBinding();
    const stateEvidence = validBuyStateEvidence();
    bindCompleteAccountState(eventBinding, stateEvidence);
    expect(evaluatePumpSilverStateFixture(eventBinding, stateEvidence).status).toBe('FIXTURE_VALID');
  });

  it.each([
    ['lamports', 1],
    ['executable', 'false'],
  ])('rejects noncanonical complete-state field %s as input schema', (field, value) => {
    const eventBinding = validBuyStateEventBinding();
    const stateEvidence = validBuyStateEvidence();
    (stateEvidence.snapshots[0] as unknown as Record<string, unknown>)[field] = value;
    rebindStateEvidenceHashes(stateEvidence);
    expect(evaluatePumpSilverStateFixture(eventBinding, stateEvidence).quarantineReasons)
      .toEqual(['INVALID_INPUT_SCHEMA']);
  });

  it('binds legacy native quote reserves to curve-account lamports', () => {
    const eventBinding = validBuyStateEventBinding();
    const stateEvidence = validBuyStateEvidence();
    const postCurve = stateEvidence.snapshots.find((snapshot) => snapshot.boundary === 'parent_instruction_post'
      && snapshot.accountRole === 'bonding_curve');
    if (!postCurve) throw new Error('missing post curve snapshot');
    postCurve.lamports = (BigInt(postCurve.lamports) + 1n).toString();
    rebindStateEvidenceHashes(stateEvidence);
    expect(evaluatePumpSilverStateFixture(eventBinding, stateEvidence).quarantineReasons)
      .toEqual(['RESERVE_MISMATCH']);
  });

  it('pins the exact synthetic legacy rent reserve instead of accepting a stable caller offset', () => {
    const eventBinding = validBuyStateEventBinding();
    const stateEvidence = validBuyStateEvidence();
    for (const snapshot of stateEvidence.snapshots) {
      if (snapshot.accountRole === 'bonding_curve') {
        snapshot.lamports = (BigInt(snapshot.lamports) + 1n).toString();
      }
    }
    rebindStateEvidenceHashes(stateEvidence);
    expect(evaluatePumpSilverStateFixture(eventBinding, stateEvidence).quarantineReasons)
      .toEqual(['RESERVE_MISMATCH']);
  });

  it.each([
    ['buy', 'mint'],
    ['buy', 'base_bonding_curve_token_account'],
    ['buy_v2', 'bonding_curve'],
    ['buy_v2', 'mint'],
    ['buy_v2', 'base_bonding_curve_token_account'],
    ['buy_v2', 'quote_bonding_curve_token_account'],
  ] as const)('rejects unexplained successful %s %s lamport changes', (variant, role) => {
    const eventBinding = validStateEventBinding(variant);
    const stateEvidence = validStateEvidence(eventBinding);
    const post = stateEvidence.snapshots.find((snapshot) => snapshot.boundary === 'parent_instruction_post'
      && snapshot.accountRole === role);
    if (!post) throw new Error(`missing ${variant} ${role} post snapshot`);
    post.lamports = (BigInt(post.lamports) + 1n).toString();
    rebindStateEvidenceHashes(stateEvidence);
    expect(evaluatePumpSilverStateFixture(eventBinding, stateEvidence).quarantineReasons)
      .toEqual(['ACCOUNT_LAMPORTS_MISMATCH']);
  });

  it('includes lamports in failed-transaction rollback equality', () => {
    const { eventBinding, evidence: stateEvidence } = failedRollbackVector();
    const postCurve = stateEvidence.snapshots.find((snapshot) => snapshot.boundary === 'parent_instruction_post'
      && snapshot.accountRole === 'bonding_curve');
    if (!postCurve) throw new Error('missing failed post curve snapshot');
    postCurve.lamports = (BigInt(postCurve.lamports) + 1n).toString();
    rebindStateEvidenceHashes(stateEvidence);
    expect(evaluatePumpSilverStateFixture(eventBinding, stateEvidence).quarantineReasons)
      .toEqual(['FAILED_TRANSACTION_ROLLBACK_MISMATCH']);
  });

  it('authenticates failed provenance before classifying rollback mismatch', () => {
    const vector = goldenVectors.adversarialVectors.find((candidate) =>
      candidate.name === 'failed_transaction_lamports_rollback_mismatch');
    if (!vector) throw new Error('missing failed lamports rollback mismatch vector');
    const eventBinding = structuredClone(vector.eventBinding) as ReturnType<typeof validBuyStateEventBinding>;
    const stateEvidence = structuredClone(vector.evidence) as ReturnType<typeof validBuyStateEvidence>;
    stateEvidence.provenance.carSha256 = 'd'.repeat(64);
    expect(evaluatePumpSilverStateFixture(eventBinding, stateEvidence).quarantineReasons)
      .toEqual(['INVALID_CAR_PROVENANCE']);
  });

  it('rejects executable account snapshots for data-account roles', () => {
    const eventBinding = validBuyStateEventBinding();
    const stateEvidence = validBuyStateEvidence();
    stateEvidence.snapshots[0]!.executable = true;
    rebindStateEvidenceHashes(stateEvidence);
    expect(evaluatePumpSilverStateFixture(eventBinding, stateEvidence).quarantineReasons)
      .toEqual(['UNKNOWN_ACCOUNT_LAYOUT']);
  });

  it('returns deeply immutable quarantine outputs', () => {
    const result = evaluatePumpSilverStateFixture(null, null);
    const canonicalHash = result.canonicalHash;
    expect(result.status).toBe('QUARANTINED');
    expect(Object.isFrozen(result)).toBe(true);
    expect(Object.isFrozen(result.quarantineReasons)).toBe(true);
    expect(() => result.quarantineReasons.push('INVALID_INPUT_SCHEMA')).toThrow(TypeError);
    expect(Reflect.set(result, 'canonicalHash', 'mutated-after-return')).toBe(false);
    expect(result.canonicalHash).toBe(canonicalHash);
  });

  it('returns deeply immutable provenance and coverage snapshots detached from caller input', () => {
    const eventBinding = validBuyStateEventBinding();
    const value = validStateEvidence(eventBinding);
    const result = evaluatePumpSilverStateFixture(eventBinding, value);
    expect(result.status).toBe('FIXTURE_VALID');
    expect(result.provenance).not.toBe(value.provenance);
    expect(result.coverage).not.toBe(value.coverage);
    expect(Object.isFrozen(result.provenance)).toBe(true);
    expect(Object.isFrozen(result.coverage)).toBe(true);
    expect(Object.isFrozen((result.coverage as Record<string, unknown>).quarantineByReason)).toBe(true);

    const sourceSha256 = (result.provenance as Record<string, unknown>).sourceSha256;
    const expectedSlots = (result.coverage as Record<string, unknown>).expectedSlots;
    value.provenance.sourceSha256 = 'd'.repeat(64);
    value.coverage.expectedSlots = '999';
    expect((result.provenance as Record<string, unknown>).sourceSha256).toBe(sourceSha256);
    expect((result.coverage as Record<string, unknown>).expectedSlots).toBe(expectedSlots);
  });

  it('binds every parent and inner-event coordinate explicitly', () => {
    const eventBinding = validBuyStateEventBinding() as Record<string, unknown>;
    expect(eventBinding).toMatchObject({
      parentInstructionLocation: 'top_level', parentInstructionIndex: 0, parentStackHeight: null,
      eventInstructionLocation: 'inner', eventParentInstructionIndex: 0, eventInstructionIndex: 0,
      eventStackHeight: 2, executionStatus: 'succeeded', tokenDecimals: 6, quoteDecimals: 9,
    });
    for (const snapshot of validBuyStateEvidence().snapshots as Array<Record<string, unknown>>) {
      expect(snapshot).toMatchObject({
        parentInstructionLocation: 'top_level', parentInstructionIndex: 0, parentStackHeight: null,
        eventInstructionLocation: 'inner', eventParentInstructionIndex: 0, eventInstructionIndex: 0,
        eventStackHeight: 2,
      });
    }
  });

  it('derives the event key instead of accepting a caller-selected alias', () => {
    const value = evidence();
    value.eventKey = 'arbitrary-but-matching';
    for (const snapshot of value.snapshots) snapshot.eventKey = value.eventKey;
    expect(reasons(rebindStateEvidenceHashes(value))).toContain('INVALID_INPUT_SCHEMA');
  });

  it('requires initialized mint state and event-bound decimals', () => {
    for (const mutation of ['uninitialized', 'wrong_decimals'] as const) {
      const value = evidence();
      for (const index of [1, 3]) {
        const bytes = Buffer.from(value.snapshots[index]!.dataHex, 'hex');
        bytes[mutation === 'uninitialized' ? 45 : 44] = mutation === 'uninitialized' ? 0 : 7;
        value.snapshots[index]!.dataHex = bytes.toString('hex');
      }
      expect(reasons(rebindStateEvidenceHashes(value))).toContain('MINT_STATE_MISMATCH');
    }
  });

  it.each([
    ['creator', 49, Buffer.alloc(32, 91)],
    ['mayhem mode', 81, Buffer.from([1])],
    ['cashback mode', 82, Buffer.from([1])],
    ['illegal completion', 48, Buffer.from([1])],
  ])('rejects unsupported curve %s drift', (_name, offset, replacement) => {
    const value = evidence();
    const bytes = Buffer.from(value.snapshots[2]!.dataHex, 'hex');
    replacement.copy(bytes, offset);
    value.snapshots[2]!.dataHex = bytes.toString('hex');
    expect(reasons(rebindStateEvidenceHashes(value))).toContain('CURVE_STATE_MISMATCH');
  });

  it('binds the authenticated Phase 6A creator to both exact curve snapshots', () => {
    const eventBinding = validBuyStateEventBinding() as Record<string, unknown>;
    expect(eventBinding.creator).toBe(creator);

    const value = validStateEvidence(eventBinding as ReturnType<typeof validBuyStateEventBinding>);
    const conflictingCreator = Buffer.alloc(32, 79);
    for (const index of [0, 2]) {
      const bytes = Buffer.from(value.snapshots[index]!.dataHex, 'hex');
      conflictingCreator.copy(bytes, 49);
      value.snapshots[index]!.dataHex = bytes.toString('hex');
    }
    expect(evaluatePumpSilverStateFixture(eventBinding, rebindStateEvidenceHashes(value)).quarantineReasons)
      .toContain('CURVE_STATE_MISMATCH');
  });

  it('binds the authenticated Phase 6A mayhem mode to both exact curve snapshots', () => {
    const eventBinding = validBuyStateEventBinding() as Record<string, unknown>;
    expect(eventBinding.mayhemMode).toBe(false);

    const value = validStateEvidence(eventBinding as ReturnType<typeof validBuyStateEventBinding>);
    for (const index of [0, 2]) {
      const bytes = Buffer.from(value.snapshots[index]!.dataHex, 'hex');
      bytes[81] = 1;
      value.snapshots[index]!.dataHex = bytes.toString('hex');
    }
    expect(evaluatePumpSilverStateFixture(eventBinding, rebindStateEvidenceHashes(value)).quarantineReasons)
      .toContain('CURVE_STATE_MISMATCH');
  });

  it('pins synthetic cashback-coin state and binds both exact curve snapshots to it', () => {
    const eventBinding = validBuyStateEventBinding() as Record<string, unknown>;
    expect(eventBinding.isCashbackCoin).toBe(false);

    const value = validStateEvidence(eventBinding as ReturnType<typeof validBuyStateEventBinding>);
    for (const index of [0, 2]) {
      const bytes = Buffer.from(value.snapshots[index]!.dataHex, 'hex');
      bytes[82] = 1;
      value.snapshots[index]!.dataHex = bytes.toString('hex');
    }
    expect(evaluatePumpSilverStateFixture(eventBinding, rebindStateEvidenceHashes(value)).quarantineReasons)
      .toContain('CURVE_STATE_MISMATCH');
  });

  it('enforces real/virtual and real/supply reserve relations independently of the event', () => {
    const realOverSupply = evidence();
    for (const index of [0, 2]) writeSnapshotU64(realOverSupply, index, 40, 50_000_000n);
    for (const index of [1, 3]) writeSnapshotU64(realOverSupply, index, 36, 50_000_000n);
    expect(reasons(realOverSupply)).toContain('RESERVE_MISMATCH');

    const realOverVirtual = evidence();
    writeSnapshotU64(realOverVirtual, 0, 8, 99_000_000n);
    expect(reasons(realOverVirtual)).toContain('RESERVE_MISMATCH');
  });

  it('rejects a syntactically valid wrong base ATA at the event-binding boundary', () => {
    const eventBinding = validBuyStateEventBinding();
    eventBinding.baseBondingCurveTokenAccount = '11111111111111111111111111111111';
    expect(evaluatePumpSilverStateFixture(eventBinding, evidence()).quarantineReasons).toEqual(['INVALID_INPUT_SCHEMA']);
  });

  it('rejects a syntactically valid wrong quote ATA at the event-binding boundary', () => {
    const eventBinding = validStateEventBinding('buy_v2');
    eventBinding.quoteBondingCurveTokenAccount = '11111111111111111111111111111111';
    expect(evaluatePumpSilverStateFixture(eventBinding, validStateEvidence(eventBinding)).quarantineReasons)
      .toEqual(['INVALID_INPUT_SCHEMA']);
  });

  it('requires exact-byte base bonding-curve token-account snapshots', () => {
    const roles = validBuyStateEvidence().snapshots.map((snapshot) => `${snapshot.boundary}:${snapshot.accountRole}`);
    expect(roles).toEqual(expect.arrayContaining([
      'parent_instruction_pre:base_bonding_curve_token_account',
      'parent_instruction_post:base_bonding_curve_token_account',
    ]));
  });

  it.each(['buy', 'sell', 'buy_v2', 'sell_v2', 'buy_exact_sol_in', 'buy_exact_quote_in_v2'] as const)(
    'accepts exact-byte state for normalized and authoritative %s fixtures',
    (variant) => {
      const eventBinding = validStateEventBinding(variant);
      const stateEvidence = validStateEvidence(eventBinding);
      expect(evaluatePumpSilverStateFixture(eventBinding, stateEvidence).status).toBe('FIXTURE_VALID');
      const ixName = variant === 'sell' || variant === 'sell_v2' ? 'sell'
        : variant === 'buy_exact_sol_in' ? 'buy_exact_sol_in' : 'buy';
      expect(evaluatePumpSilverStateFromBronze(
        bronzeFixture(variant, { eventOverrides: { ixName } }), stateEvidence,
      ).status).toBe('FIXTURE_VALID');
    },
  );

  it('accepts a pinned Token-2022 base-mint fixture with no extensions', () => {
    const bronze = bronzeFixture('buy_v2', {
      baseTokenProgram: TOKEN_2022_PROGRAM,
      eventOverrides: { ixName: 'buy' },
    });
    const eventBinding = validToken2022StateEventBinding();
    const authoritativeBinding = stateEventBindingFromBronze(bronze);
    expect(eventBinding).toEqual(authoritativeBinding);
    const stateEvidence = validStateEvidence(eventBinding);
    const result = evaluatePumpSilverStateFixture(eventBinding, stateEvidence);
    expect(result.status).toBe('FIXTURE_VALID');
    expect(result.state?.after.mint.tokenProgramId).toBe(TOKEN_2022_PROGRAM);
    expect(result.state?.after.baseBondingCurveTokenAccount.tokenProgramId).toBe(TOKEN_2022_PROGRAM);
    expect(evaluatePumpSilverStateFromBronze(bronze, stateEvidence)).toEqual(result);
  });

  it.each([
    ['base amount', (value: ReturnType<typeof evidence>) => writeSnapshotU64(value, 5, 64, 98_000_001n), 'RESERVE_MISMATCH'],
    ['base mint', (value: ReturnType<typeof evidence>) => {
      const bytes = Buffer.from(value.snapshots[5]!.dataHex, 'hex');
      Buffer.from(bs58.decode(mintAuthority)).copy(bytes, 0);
      value.snapshots[5]!.dataHex = bytes.toString('hex');
    }, 'RESERVE_MISMATCH'],
    ['base owner', (value: ReturnType<typeof evidence>) => {
      const bytes = Buffer.from(value.snapshots[5]!.dataHex, 'hex');
      Buffer.from(bs58.decode(mintAuthority)).copy(bytes, 32);
      value.snapshots[5]!.dataHex = bytes.toString('hex');
    }, 'RESERVE_MISMATCH'],
    ['frozen base state', (value: ReturnType<typeof evidence>) => {
      const bytes = Buffer.from(value.snapshots[5]!.dataHex, 'hex');
      bytes[108] = 2;
      value.snapshots[5]!.dataHex = bytes.toString('hex');
    }, 'RESERVE_MISMATCH'],
    ['wrong base program owner', (value: ReturnType<typeof evidence>) => {
      value.snapshots[5]!.ownerProgramId = PUMP_PROGRAM_ID;
    }, 'WRONG_ACCOUNT_OWNER'],
  ])('rejects token-account %s drift', (_name, mutate, reason) => {
    const value = evidence();
    mutate(value);
    expect(reasons(rebindStateEvidenceHashes(value))).toContain(reason);
  });

  it('requires quote-token account bytes for v2 tokenized quote liquidity', () => {
    const eventBinding = validStateEventBinding('buy_v2');
    const stateEvidence = validStateEvidence(eventBinding);
    stateEvidence.snapshots.pop();
    expect(evaluatePumpSilverStateFixture(eventBinding, stateEvidence).quarantineReasons)
      .toContain('MISSING_RAW_ACCOUNT_BYTES');
  });

  it('does not let the authoritative wrapper relabel an unpinned Bronze identity as synthetic', () => {
    const bronze = bronzeFixture('buy');
    bronze.signature = bs58.encode(Buffer.alloc(64, 99));
    const evidence = validStateEvidence(stateEventBindingFromBronze(bronze));
    expect(evaluatePumpSilverStateFromBronze(bronze, evidence).quarantineReasons)
      .toContain('PHASE6A_INVALID');
  });

  it('returns FAILED_TRANSACTION only for an authenticated failed fixture with exact rollback evidence', () => {
    const bronze = bronzeFixture('buy', { failed: true, eventOverrides: { ixName: 'buy' } });
    const { eventBinding, evidence: stateEvidence } = failedRollbackVector();
    const authoritativeBinding = stateEventBindingFromBronze(bronze);
    expect(eventBinding).toEqual(authoritativeBinding);
    const normalized = evaluatePumpSilverStateFixture(eventBinding, stateEvidence);
    expect(normalized.quarantineReasons).toEqual(['FAILED_TRANSACTION']);
    expect(evaluatePumpSilverStateFromBronze(bronze, stateEvidence)).toEqual(normalized);
  });

  it('rejects an arbitrary failed binding before echoing caller-selected identity fields', () => {
    const { eventBinding, evidence: stateEvidence } = failedRollbackVector();
    eventBinding.signature = bs58.encode(Buffer.alloc(64, 99));
    const result = evaluatePumpSilverStateFixture(eventBinding, stateEvidence);
    expect(result.quarantineReasons).toEqual(['PHASE6A_INVALID']);
    expect(result.eventKey).toBeNull();
    expect(result.sourcePhase6aSha256).toBeNull();
    expect(result.eventBindingSha256).toBeNull();
  });

  it('requires complete evidence before classifying an authenticated failed transaction', () => {
    const { eventBinding } = failedRollbackVector();
    expect(evaluatePumpSilverStateFixture(eventBinding, null).quarantineReasons).toEqual(['INVALID_INPUT_SCHEMA']);
  });

  it('rejects a failed transaction whose post-state represents a successful transition', () => {
    const { eventBinding, evidence: stateEvidence } = failedRollbackVector();
    const successful = validBuyStateEvidence();
    for (const snapshot of stateEvidence.snapshots) {
      if (snapshot.boundary !== 'parent_instruction_post') continue;
      const replacement = successful.snapshots.find((candidate) => candidate.boundary === snapshot.boundary
        && candidate.accountRole === snapshot.accountRole);
      if (!replacement) throw new Error(`missing successful ${snapshot.accountRole} snapshot`);
      snapshot.dataHex = replacement.dataHex;
    }
    rebindStateEvidenceHashes(stateEvidence);
    expect(evaluatePumpSilverStateFixture(eventBinding, stateEvidence).quarantineReasons)
      .toEqual(['FAILED_TRANSACTION_ROLLBACK_MISMATCH']);
  });

  it('classifies every valid-layout changed failed post-state before reserve semantics', () => {
    const { eventBinding, evidence: stateEvidence } = failedRollbackVector();
    const postBase = stateEvidence.snapshots.find((snapshot) => snapshot.boundary === 'parent_instruction_post'
      && snapshot.accountRole === 'base_bonding_curve_token_account');
    if (!postBase) throw new Error('missing failed post base token account');
    const bytes = Buffer.from(postBase.dataHex, 'hex');
    bytes.writeBigUInt64LE(bytes.readBigUInt64LE(64) + 1n, 64);
    postBase.dataHex = bytes.toString('hex');
    rebindStateEvidenceHashes(stateEvidence);
    expect(evaluatePumpSilverStateFixture(eventBinding, stateEvidence).quarantineReasons)
      .toEqual(['FAILED_TRANSACTION_ROLLBACK_MISMATCH']);
  });

  it('validates failed-transaction post-state bytes before classification', () => {
    const { eventBinding, evidence: stateEvidence } = failedRollbackVector();
    const postCurve = stateEvidence.snapshots.find((snapshot) => snapshot.boundary === 'parent_instruction_post'
      && snapshot.accountRole === 'bonding_curve');
    if (!postCurve) throw new Error('missing failed post curve');
    postCurve.dataHex = '00';
    rebindStateEvidenceHashes(stateEvidence);
    expect(evaluatePumpSilverStateFixture(eventBinding, stateEvidence).quarantineReasons).toEqual(['UNKNOWN_ACCOUNT_LAYOUT']);
  });

  it('quarantines non-enumerable and accessor-backed canonical input without invoking getters', () => {
    const nonEnumerable = evidence();
    Object.defineProperty(nonEnumerable.registry, 'officialDocsCommit', {
      value: nonEnumerable.registry.officialDocsCommit, enumerable: false, configurable: true,
    });
    expect(reasons(nonEnumerable)).toContain('INVALID_INPUT_SCHEMA');

    const accessor = evidence();
    let getterCalls = 0;
    Object.defineProperty(accessor.registry, 'officialDocsCommit', {
      enumerable: true, configurable: true,
      get() { getterCalls += 1; throw new Error('must not execute'); },
    });
    expect(() => reasons(accessor)).not.toThrow();
    expect(reasons(accessor)).toContain('INVALID_INPUT_SCHEMA');
    expect(getterCalls).toBe(0);
  });

  it('rejects accessor-backed array elements without invoking them', () => {
    const value = evidence();
    let getterCalls = 0;
    Object.defineProperty(value.snapshots, 0, {
      enumerable: true,
      configurable: true,
      get() { getterCalls += 1; throw new Error('ACCESSOR_EXECUTED'); },
    });
    let observed: ReturnType<typeof evaluatePumpSilverStateFixture> | undefined;
    expect(() => { observed = evaluatePumpSilverStateFixture(validBuyStateEventBinding(), value); }).not.toThrow();
    expect(observed?.quarantineReasons).toContain('INVALID_INPUT_SCHEMA');
    expect(getterCalls).toBe(0);
  });

  it('rejects inherited array iterator accessors without invoking them', () => {
    const value = evidence();
    let getterCalls = 0;
    const inherited = Object.create(Array.prototype) as object;
    Object.defineProperty(inherited, Symbol.iterator, {
      configurable: true,
      get() { getterCalls += 1; throw new Error('INHERITED_ITERATOR_ACCESSOR_EXECUTED'); },
    });
    Object.setPrototypeOf(value.snapshots, inherited);
    let observed: ReturnType<typeof evaluatePumpSilverStateFixture> | undefined;
    expect(() => { observed = evaluatePumpSilverStateFixture(validBuyStateEventBinding(), value); }).not.toThrow();
    expect(observed?.quarantineReasons).toContain('INVALID_INPUT_SCHEMA');
    expect(getterCalls).toBe(0);
  });

  it('rejects proxies before executing their get traps', () => {
    const value = evidence();
    let getTrapCalls = 0;
    value.registry = new Proxy(value.registry, {
      get() { getTrapCalls += 1; throw new Error('PROXY_GET_EXECUTED'); },
    });
    let observed: ReturnType<typeof evaluatePumpSilverStateFixture> | undefined;
    expect(() => { observed = evaluatePumpSilverStateFixture(validBuyStateEventBinding(), value); }).not.toThrow();
    expect(observed?.quarantineReasons).toContain('INVALID_INPUT_SCHEMA');
    expect(getTrapCalls).toBe(0);
  });

  it('rejects a revoked top-level event-binding proxy without throwing', () => {
    const revoked = Proxy.revocable(validBuyStateEventBinding(), {});
    revoked.revoke();
    let observed: ReturnType<typeof evaluatePumpSilverStateFixture> | undefined;
    expect(() => { observed = evaluatePumpSilverStateFixture(revoked.proxy, evidence()); }).not.toThrow();
    expect(observed?.quarantineReasons).toContain('INVALID_INPUT_SCHEMA');
  });

  it('rejects a revoked snapshots-array proxy without throwing', () => {
    const value = evidence();
    const revoked = Proxy.revocable(value.snapshots, {});
    revoked.revoke();
    value.snapshots = revoked.proxy;
    let observed: ReturnType<typeof evaluatePumpSilverStateFixture> | undefined;
    expect(() => { observed = evaluatePumpSilverStateFixture(validBuyStateEventBinding(), value); }).not.toThrow();
    expect(observed?.quarantineReasons).toContain('INVALID_INPUT_SCHEMA');
  });

  it('rejects non-string event variants without executing coercion hooks', () => {
    const binding = validBuyStateEventBinding() as unknown as Record<string, unknown>;
    let coercionCalls = 0;
    binding.variant = {
      toString() { coercionCalls += 1; throw new Error('VARIANT_COERCION_EXECUTED'); },
    };
    let observed: ReturnType<typeof evaluatePumpSilverStateFixture> | undefined;
    expect(() => { observed = evaluatePumpSilverStateFixture(binding, evidence()); }).not.toThrow();
    expect(observed?.quarantineReasons).toContain('INVALID_INPUT_SCHEMA');
    expect(coercionCalls).toBe(0);
  });

  it('rejects non-string snapshot roles without executing coercion hooks', () => {
    const value = evidence();
    let coercionCalls = 0;
    (value.snapshots[0] as unknown as Record<string, unknown>).accountRole = {
      toString() { coercionCalls += 1; throw new Error('ROLE_COERCION_EXECUTED'); },
    };
    let observed: ReturnType<typeof evaluatePumpSilverStateFixture> | undefined;
    expect(() => { observed = evaluatePumpSilverStateFixture(validBuyStateEventBinding(), value); }).not.toThrow();
    expect(observed?.quarantineReasons).toContain('INVALID_INPUT_SCHEMA');
    expect(coercionCalls).toBe(0);
  });

  it('rejects observability label proxies without executing traps', () => {
    let ownKeysCalls = 0;
    let getCalls = 0;
    const labels = new Proxy({ stage: 'silver' }, {
      ownKeys(target) { ownKeysCalls += 1; return Reflect.ownKeys(target); },
      get() { getCalls += 1; throw new Error('LABEL_PROXY_GET_EXECUTED'); },
    });
    expect(() => validatePumpSilverObservabilitySignal({ metric: 'queue_depth', labels })).not.toThrow();
    expect(validatePumpSilverObservabilitySignal({ metric: 'queue_depth', labels })).toBe(false);
    expect(ownKeysCalls).toBe(0);
    expect(getCalls).toBe(0);
  });

  it('rejects hidden properties on canonical arrays', () => {
    const value = evidence();
    Object.defineProperty(value.registry.supportedToken2022Extensions, 'mint', {
      value: 'hidden-high-cardinality-data', enumerable: false,
    });
    expect(reasons(value)).toContain('INVALID_REGISTRY');
  });

  it.each([
    ['accepted slot reported skipped', (value: ReturnType<typeof evidence>) => {
      value.coverage.observedSlots = '0'; value.coverage.skippedSlots = '1';
    }],
    ['accepted slot reported quarantined', (value: ReturnType<typeof evidence>) => {
      value.coverage.observedSlots = '0'; value.coverage.quarantinedSlots = '1';
    }],
    ['callbacks exceed budget', (value: ReturnType<typeof evidence>) => {
      value.coverage.expectedCallbacks = '17'; value.coverage.observedCallbacks = '17';
    }],
  ])('closes coverage and budget for %s', (_name, mutate) => {
    const value = evidence();
    mutate(value);
    expect(reasons(rebindStateEvidenceHashes(value))).toContain('INCOMPLETE_COVERAGE');
  });

  it('does not authenticate arbitrary normalized Phase 6A source identities', () => {
    const eventBinding = validBuyStateEventBinding();
    const vectorEvidence = validBuyStateEvidence();
    eventBinding.sourcePhase6aSha256 = 'd'.repeat(64);
    vectorEvidence.provenance.sourceSha256 = 'd'.repeat(64);
    rebindStateEvidenceHashes(vectorEvidence);
    expect(evaluatePumpSilverStateFixture(eventBinding, vectorEvidence).status).toBe('QUARANTINED');
  });

  it('uses exclusively synthetic CAR and inventory identities', () => {
    const value = evidence();
    expect(value.provenance.epochCid).not.toBe('bafyreihvgnloaiilehijbe42cloousmwvl2z666zr7wvqprs6wnqfeqo4q');
    expect(value.provenance.carSha256).not.toBe('3c9727378e617f5cba8b5e206bce8fc6ae5df34d3a4408eeb69aea4d62ac7218');
  });

  it('emits approved false even for quarantined output', () => {
    const value = evidence();
    value.registry.approved = true;
    expect((evaluatePumpSilverStateFromBronze(bronzeFixture('buy'), value) as unknown as Record<string, unknown>).approved).toBe(false);
  });

  it.each([
    ['missing raw bytes', (value: ReturnType<typeof evidence>) => { value.snapshots[0]!.dataHex = ''; }, 'MISSING_RAW_ACCOUNT_BYTES'],
    ['transaction-wide balances', (value: ReturnType<typeof evidence>) => { value.snapshots[0]!.transactionWideBalanceOnly = true; }, 'TRANSACTION_WIDE_BALANCE_ONLY'],
    ['event fields as state authority', (value: ReturnType<typeof evidence>) => { value.snapshots[0]!.stateAuthority = 'EVENT_FIELDS' as never; }, 'EVENT_FIELDS_AS_STATE_AUTHORITY'],
    ['wrong owner', (value: ReturnType<typeof evidence>) => { value.snapshots[0]!.ownerProgramId = value.snapshots[1]!.ownerProgramId; }, 'WRONG_ACCOUNT_OWNER'],
    ['wrong account identity', (value: ReturnType<typeof evidence>) => { value.snapshots[0]!.accountPubkey = value.snapshots[1]!.accountPubkey; }, 'ACCOUNT_IDENTITY_MISMATCH'],
    ['stale snapshot', (value: ReturnType<typeof evidence>) => { value.snapshots[0]!.slot = '361000000'; }, 'INVALID_SNAPSHOT_COORDINATES'],
    ['future snapshot', (value: ReturnType<typeof evidence>) => { value.snapshots[0]!.slot = '361000002'; }, 'INVALID_SNAPSHOT_COORDINATES'],
    ['unsafe write ordinal', (value: ReturnType<typeof evidence>) => { value.snapshots[0]!.writeOrdinal = 9_007_199_254_740_993 as never; }, 'UNSAFE_INTEGER'],
    ['unknown snapshot key', (value: ReturnType<typeof evidence>) => { Object.assign(value.snapshots[0]!, { arbitrary: true }); }, 'INVALID_INPUT_SCHEMA'],
  ])('fails closed for %s', (_name, mutate, expectedReason) => {
    const value = evidence();
    mutate(value);
    expect(reasons(value)).toContain(expectedReason);
  });

  it('rejects duplicate snapshots distinctly from missing snapshots', () => {
    const value = evidence();
    value.snapshots[3] = structuredClone(value.snapshots[2]) as never;
    expect(reasons(value)).toContain('DUPLICATE_SNAPSHOT');
  });

  it('rejects multiple writes without a unique instruction boundary', () => {
    const value = evidence();
    value.snapshots.push({ ...structuredClone(value.snapshots[0]!), writeOrdinal: '3' } as never);
    expect(reasons(value)).toContain('AMBIGUOUS_ACCOUNT_WRITES');
  });

  it('requires before write ordinals to precede after write ordinals', () => {
    const value = evidence();
    value.snapshots[0]!.writeOrdinal = '3';
    value.snapshots[1]!.writeOrdinal = '3';
    expect(reasons(rebindStateEvidenceHashes(value))).toContain('INVALID_SNAPSHOT_ORDER');
  });

  it('rejects a supply mismatch from exact mint bytes', () => {
    const value = evidence();
    const mintBytes = Buffer.from(value.snapshots[3]!.dataHex, 'hex');
    mintBytes.writeBigUInt64LE(999_999_999_999_999n, 36);
    value.snapshots[3]!.dataHex = mintBytes.toString('hex');
    expect(reasons(rebindStateEvidenceHashes(value))).toContain('SUPPLY_OR_DECIMAL_MISMATCH');
  });

  it('rejects reserve deltas that do not match the Phase 6A event', () => {
    const value = evidence();
    writeSnapshotU64(value, 2, 24, 97_999_999n);
    expect(reasons(value)).toContain('RESERVE_MISMATCH');
  });

  it('rejects event/state conflict even when the raw transition delta is internally consistent', () => {
    const value = evidence();
    writeSnapshotU64(value, 0, 24, 99_999_999n);
    writeSnapshotU64(value, 2, 24, 97_999_999n);
    writeSnapshotU64(value, 4, 64, 99_999_999n);
    writeSnapshotU64(value, 5, 64, 97_999_999n);
    expect(reasons(value)).toContain('EVENT_STATE_CONFLICT');
  });

  it.each([
    ['CAR CID', (value: ReturnType<typeof evidence>) => { value.provenance.epochCid = 'bafyreiaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'; }, 'INVALID_CAR_PROVENANCE'],
    ['CAR SHA', (value: ReturnType<typeof evidence>) => { value.provenance.carSha256 = 'd'.repeat(64); }, 'INVALID_CAR_PROVENANCE'],
    ['CAR size', (value: ReturnType<typeof evidence>) => { value.provenance.carFileSizeBytes = '767389334225'; value.provenance.cumulativeSourceBytes = '767393652045'; }, 'INVALID_CAR_PROVENANCE'],
    ['inventory SHA', (value: ReturnType<typeof evidence>) => { value.provenance.slotInventorySha256 = 'e'.repeat(64); }, 'INVALID_SLOT_INVENTORY'],
    ['inventory size', (value: ReturnType<typeof evidence>) => { value.provenance.slotInventorySizeBytes = '4317821'; value.provenance.cumulativeSourceBytes = '767393652045'; }, 'INVALID_SLOT_INVENTORY'],
  ])('rejects incorrect synthetic golden %s', (_name, mutate, expectedReason) => {
    const value = evidence();
    mutate(value);
    expect(reasons(value)).toContain(expectedReason);
  });

  it('pins every synthetic parser/reducer/adapter identity exactly', () => {
    const value = evidence();
    value.provenance.parserGitSha = 'dddddddddddddddddddddddddddddddddddddddd';
    expect(reasons(value)).toContain('INVALID_CAR_PROVENANCE');
  });

  it('pins the synthetic source slot range exactly rather than accepting a wider plausible range', () => {
    const value = evidence();
    value.provenance.slotRange.startInclusive = '361000000';
    expect(reasons(value)).toContain('INVALID_SLOT_INVENTORY');
  });

  it('rejects a numerically valid but unapproved synthetic pilot budget', () => {
    const value = evidence();
    value.pilotBudget.maxCallbacks = '17';
    expect(reasons(rebindStateEvidenceHashes(value))).toContain('UNSAFE_INTEGER');
  });

  it('rejects incomplete coverage even when its rerun hash is rebound', () => {
    const value = evidence();
    value.coverage.observedCallbacks = '1';
    expect(reasons(rebindStateEvidenceHashes(value))).toContain('INCOMPLETE_COVERAGE');
  });

  it('rejects quarantine totals that do not close', () => {
    const value = evidence();
    value.coverage.quarantineByReason = [{ reason: 'INVALID_INPUT_SCHEMA', count: '1' }] as never;
    expect(reasons(rebindStateEvidenceHashes(value))).toContain('QUARANTINE_ACCOUNTING_MISMATCH');
  });

  it('rejects a deterministic rerun hash mismatch', () => {
    const value = evidence();
    value.provenance.deterministicRerunHash = 'f'.repeat(64);
    expect(reasons(value)).toContain('RERUN_HASH_MISMATCH');
  });

  it('rejects a self-approved synthetic registry', () => {
    const value = evidence();
    value.registry.approved = true;
    expect(reasons(value)).toContain('SELF_APPROVED_REGISTRY');
  });

  it('rejects a registry that claims a real activation range', () => {
    const value = evidence();
    value.registry.realActivationSlotRange = { startInclusive: '1', endExclusive: '2' } as never;
    expect(reasons(value)).toContain('REAL_ACTIVATION_RANGE_FORBIDDEN');
  });

  it('rejects every attempt to set researchReady true', () => {
    const value = evidence();
    value.registry.researchReady = true;
    expect(reasons(value)).toContain('RESEARCH_READY_FORBIDDEN');
  });

  it('keeps observability outside canonical input and rejects canonical influence', () => {
    const value = evidence();
    Object.assign(value, { observability: { queueDepth: 1 } });
    expect(reasons(value)).toContain('OBSERVABILITY_CANONICAL_INFLUENCE_FORBIDDEN');
  });

  it('keeps authoritative quarantine reason-count documentation bound to the export', () => {
    const contract = readFileSync('docs/PHASE6B_PUMP_SILVER_STATE_PROVENANCE_CONTRACT.md', 'utf8');
    const documentedCounts = [...contract.matchAll(/\b(\d+)\b(?= (?:bounded reason codes|closed quarantine reasons))/g)]
      .map((match) => Number(match[1]));
    expect(documentedCounts).toEqual([
      PUMP_SILVER_STATE_QUARANTINE_REASONS.length,
      PUMP_SILVER_STATE_QUARANTINE_REASONS.length,
    ]);
  });

  it('permits only bounded low-cardinality observability labels and includes external timing', () => {
    expect(PUMP_SILVER_STATE_OBSERVABILITY_CONTRACT.maximumReasonCodes).toBe(PUMP_SILVER_STATE_QUARANTINE_REASONS.length);
    expect(PUMP_SILVER_STATE_OBSERVABILITY_CONTRACT.maximumReasonCodes).toBeLessThanOrEqual(64);
    expect(PUMP_SILVER_STATE_OBSERVABILITY_CONTRACT.metricsRequiredLater).toEqual(expect.arrayContaining([
      'callback_duration_seconds',
      'state_evaluation_duration_seconds',
    ]));
    expect(validatePumpSilverObservabilitySignal({
      metric: 'silver_state_quarantined_total',
      labels: { stage: 'silver', reason: 'RESERVE_MISMATCH' },
    })).toBe(true);
    expect(validatePumpSilverObservabilitySignal({
      metric: 'silver_state_quarantined_total',
      labels: { mint: '36VASLSKLFD2KokjXG7V28veZvXEsyHRKefLonPaAKzv' },
    })).toBe(false);
    const hiddenForbidden = { stage: 'silver' } as Record<string, unknown>;
    Object.defineProperty(hiddenForbidden, 'mint', { value: 'hidden', enumerable: false });
    expect(validatePumpSilverObservabilitySignal({ metric: 'queue_depth', labels: hiddenForbidden })).toBe(false);
    const throwingLabels = {} as Record<string, unknown>;
    Object.defineProperty(throwingLabels, 'stage', { enumerable: true, get() { throw new Error('must not execute'); } });
    expect(() => validatePumpSilverObservabilitySignal({ metric: 'queue_depth', labels: throwingLabels })).not.toThrow();
    expect(validatePumpSilverObservabilitySignal({ metric: 'queue_depth', labels: throwingLabels })).toBe(false);
  });

  it('does not let side-channel observability change canonical state bytes', () => {
    const before = evaluatePumpSilverStateFromBronze(bronzeFixture('buy'), evidence());
    expect(validatePumpSilverObservabilitySignal({ metric: 'queue_depth', labels: { stage: 'silver' } })).toBe(true);
    const after = evaluatePumpSilverStateFromBronze(bronzeFixture('buy'), evidence());
    expect(after.canonicalHash).toBe(before.canonicalHash);
  });
});
