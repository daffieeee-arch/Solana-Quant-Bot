import { describe, expect, it } from 'vitest';
import {
  PUMP_SILVER_FIXTURE_REGISTRY,
  canonicalPumpSilverHash,
  evaluatePumpSilverTransaction,
} from '../src/research/pump-silver-contract.js';
import {
  ASSOCIATED_TOKEN_PROGRAM,
  TOKEN_2022_PROGRAM,
  bronzeFixture,
  bronzeV0LoadedFixture,
  creator,
  curve,
  eventAuthority,
  feeRecipient,
  key,
  mint,
  user,
  type SupportedFixtureVariant,
} from './fixtures/pump-silver/fixture.js';

describe('Phase 6A Pump Silver fixture-only transaction contract', () => {
  it('binds the pinned unapproved provenance and classifies one structurally paired top-level buy', () => {
    const result = evaluatePumpSilverTransaction(bronzeFixture('buy'));

    expect(PUMP_SILVER_FIXTURE_REGISTRY).toMatchObject({
      registryStatus: 'fixture_only_unapproved',
      fixtureOnly: true,
      approved: false,
      officialDocsCommit: '9c82f61cb711b044a17f770ab8ce9f9bdf78f333',
      pumpIdlSha256: 'b90bc471327f671449271d5d1d42354d1fae6f5a06502f5834459a3108138e49',
    });
    expect(result).toMatchObject({
      schemaVersion: 'PUMP_SILVER_TRANSACTION_CONTRACT_1',
      researchReady: false,
      source: {
        slot: 361_000_001,
        transactionIndex: 7,
        executionStatus: 'succeeded',
      },
      isExecutedTrade: true,
      quarantines: [],
    });
    expect(result.events).toHaveLength(1);
    expect(result.events[0]).toMatchObject({
      variant: 'buy', kind: 'buy', mint, bondingCurve: curve, user, feeRecipient,
      tokenDecimals: 6, quoteDecimals: 9, researchReady: false, isExecutedTrade: true,
      parentCoordinate: { instructionLocation: 'top_level', instructionIndex: 0 },
      eventCoordinate: { instructionLocation: 'inner', parentInstructionIndex: 0, instructionIndex: 0, stackHeight: 2 },
      roles: { event_authority: eventAuthority },
      event: { eventType: 'trade', isBuy: true, ixName: 'buy' },
    });
    expect(result.canonicalHash).toMatch(/^[0-9a-f]{64}$/);
  });

  it.each([
    ['buy', 'buy', 'buy'],
    ['sell', 'sell', 'sell'],
    ['buy_exact_sol_in', 'buy', 'buy_exact_sol_in'],
    ['buy_v2', 'buy', 'buy'],
    ['sell_v2', 'sell', 'sell'],
    ['buy_exact_quote_in_v2', 'buy', 'buy'],
  ] as Array<[SupportedFixtureVariant, 'buy' | 'sell', string]>)('supports pinned trade variant %s with an explicit ix_name binding', (variant, kind, ixName) => {
    const result = evaluatePumpSilverTransaction(bronzeFixture(variant, {
      eventOverrides: { ixName },
    }));
    expect(result.quarantines).toEqual([]);
    expect(result.isExecutedTrade).toBe(true);
    expect(result.events[0]).toMatchObject({ variant, kind, isExecutedTrade: true });
  });

  it.each(['create', 'create_v2'] as const)('binds %s instruction arguments and exact fixed account roles', (variant) => {
    const result = evaluatePumpSilverTransaction(bronzeFixture(variant));
    expect(result.quarantines).toEqual([]);
    expect(result.isExecutedTrade).toBe(false);
    expect(result.events[0]).toMatchObject({
      variant,
      kind: 'create',
      tokenDecimals: null,
      quoteDecimals: 9,
      isExecutedTrade: false,
      event: { name: 'Fixture Coin', symbol: 'FIX', creator: expect.any(String) },
      roles: {
        token_program: variant === 'create_v2' ? TOKEN_2022_PROGRAM : expect.any(String),
        associated_token_program: ASSOCIATED_TOKEN_PROGRAM,
        event_authority: eventAuthority,
      },
    });
  });

  it('pairs a Pump parent invoked by CPI only with its later direct child event CPI', () => {
    const result = evaluatePumpSilverTransaction(bronzeFixture('buy', { innerParent: true }));
    expect(result.quarantines).toEqual([]);
    expect(result.events[0]).toMatchObject({
      parentCoordinate: { instructionLocation: 'inner', parentInstructionIndex: 0, instructionIndex: 0, stackHeight: 2 },
      eventCoordinate: { instructionLocation: 'inner', parentInstructionIndex: 0, instructionIndex: 1, stackHeight: 3 },
    });
  });

  it('accepts legacy and real Bronze v0 loaded-address-resolved shapes without trusting pumpCandidates', () => {
    const legacy = bronzeFixture('buy');
    legacy.pumpCandidates = [{ bad: 'untrusted' }] as never;
    const v0 = bronzeV0LoadedFixture();
    v0.pumpCandidates = [];
    expect(evaluatePumpSilverTransaction(legacy).isExecutedTrade).toBe(true);
    expect(evaluatePumpSilverTransaction(v0).isExecutedTrade).toBe(true);
  });

  it.each([
    ['truncated buy OptionBool', '66063d1201daebea' + '00'.repeat(16)],
    ['extra buy byte', '66063d1201daebea' + '00'.repeat(18)],
    ['non-canonical buy OptionBool bool', '66063d1201daebea' + '00'.repeat(16) + '02'],
    ['extra sell byte', '33e685a4017f83ad' + '00'.repeat(17)],
  ])('quarantines %s before accepting the event', (_label, dataHex) => {
    const fixture = bronzeFixture(dataHex.startsWith('33') ? 'sell' : 'buy', {
      eventOverrides: dataHex.startsWith('33') ? { ixName: 'sell' } : undefined,
    });
    fixture.instructions[0]!.dataHex = dataHex;
    const result = evaluatePumpSilverTransaction(fixture);
    expect(result.isExecutedTrade).toBe(false);
    expect(result.events).toEqual([]);
    expect(result.quarantines).toContainEqual(expect.objectContaining({ reason: 'invalid_parent_instruction_args' }));
  });

  it('uses a bounded synthetic slot interval and denies any real activation-slot claim', () => {
    expect(PUMP_SILVER_FIXTURE_REGISTRY).toMatchObject({
      activationSlotEvidence: 'synthetic_fixture_only_no_real_activation_slot_approval',
      slotRange: { startInclusive: 361_000_000, endExclusive: 362_000_000 },
    });
    const outOfRange = bronzeFixture('buy');
    outOfRange.slot = 362_000_000;
    expect(evaluatePumpSilverTransaction(outOfRange).quarantines).toContainEqual(
      expect.objectContaining({ reason: 'registry_slot_out_of_range' }),
    );
  });

  it('quarantines ambiguous matching fixture registry entries', () => {
    const entry = {
      startInclusive: 361_000_000,
      endExclusive: 362_000_000,
      officialDocsCommit: PUMP_SILVER_FIXTURE_REGISTRY.officialDocsCommit,
      pumpIdlSha256: PUMP_SILVER_FIXTURE_REGISTRY.pumpIdlSha256,
    };
    const result = evaluatePumpSilverTransaction(bronzeFixture('buy'), [entry, { ...entry }]);
    expect(result.events).toEqual([]);
    expect(result.quarantines).toContainEqual(expect.objectContaining({ reason: 'ambiguous_registry_match' }));
  });

  it('rejects a provenance-correct but non-canonical synthetic slot entry', () => {
    const result = evaluatePumpSilverTransaction(bronzeFixture('buy'), [{
      startInclusive: 360_000_000,
      endExclusive: 363_000_000,
      officialDocsCommit: PUMP_SILVER_FIXTURE_REGISTRY.officialDocsCommit,
      pumpIdlSha256: PUMP_SILVER_FIXTURE_REGISTRY.pumpIdlSha256,
    }]);
    expect(result.events).toEqual([]);
    expect(result.quarantines).toContainEqual(expect.objectContaining({ reason: 'registry_entry_mismatch' }));
  });

  it('bounds a stateful fixture-registry array proxy before iteration', () => {
    const entry = {
      startInclusive: 361_000_000,
      endExclusive: 362_000_000,
      officialDocsCommit: PUMP_SILVER_FIXTURE_REGISTRY.officialDocsCommit,
      pumpIdlSha256: PUMP_SILVER_FIXTURE_REGISTRY.pumpIdlSha256,
    };
    let lengthReads = 0;
    let indexReads = 0;
    const entries = new Proxy([entry], {
      get(target, property, receiver) {
        if (property === 'length') {
          lengthReads += 1;
          return indexReads === 0 ? 1 : 1_000;
        }
        if (typeof property === 'string' && /^[0-9]+$/.test(property)) {
          indexReads += 1;
          return entry;
        }
        return Reflect.get(target, property, receiver);
      },
      getOwnPropertyDescriptor(target, property) {
        if (typeof property === 'string' && /^[0-9]+$/.test(property)) {
          return { configurable: true, enumerable: true, writable: true, value: entry };
        }
        return Reflect.getOwnPropertyDescriptor(target, property);
      },
    });

    const result = evaluatePumpSilverTransaction(bronzeFixture('buy'), entries);
    expect(indexReads).toBeLessThanOrEqual(16);
    expect(result.events).toEqual([]);
    expect(result.quarantines).toContainEqual(expect.objectContaining({ reason: 'invalid_fixture_registry' }));
  });

  it('quarantines a malformed fixture registry entry instead of throwing', () => {
    const result = evaluatePumpSilverTransaction(bronzeFixture('buy'), [null] as never);
    expect(result.events).toEqual([]);
    expect(result.quarantines).toContainEqual(expect.objectContaining({ reason: 'invalid_fixture_registry' }));
  });

  it.each([
    ['unknown parent discriminator', () => {
      const fixture = bronzeFixture('buy');
      fixture.instructions[0]!.dataHex = `deadbeefcafebabe${'00'.repeat(17)}`;
      return fixture;
    }, 'unknown_parent_discriminator'],
    ['wrong event discriminator', () => bronzeFixture('buy', {
      eventOverrides: { eventDiscriminator: 'deadbeefcafebabe' },
    }), 'unknown_event_discriminator'],
    ['wrong event kind', () => bronzeFixture('buy', { eventOverrides: { isBuy: false } }), 'event_kind_mismatch'],
    ['wrong ix_name', () => bronzeFixture('buy', { eventOverrides: { ixName: 'sell' } }), 'event_kind_mismatch'],
  ] as const)('quarantines %s explicitly', (_label, makeFixture, reason) => {
    const result = evaluatePumpSilverTransaction(makeFixture());
    expect(result.events).toEqual([]);
    expect(result.isExecutedTrade).toBe(false);
    expect(result.quarantines).toContainEqual(expect.objectContaining({ reason }));
  });

  it.each([
    ['mint role', 2, 'account_role_or_pda_mismatch'],
    ['bonding-curve PDA', 3, 'account_role_or_pda_mismatch'],
    ['creator-vault PDA', 9, 'account_role_or_pda_mismatch'],
    ['event-authority PDA', 10, 'account_role_or_pda_mismatch'],
    ['program role', 11, 'account_role_or_pda_mismatch'],
    ['fee program fixed address', 15, 'account_role_or_pda_mismatch'],
  ] as const)('quarantines a wrong %s', (_label, roleIndex, reason) => {
    const fixture = bronzeFixture('buy');
    const parent = fixture.instructions[0]!;
    parent.accounts[roleIndex] = fixture.accountKeys[0]!;
    parent.accountIndices[roleIndex] = 0;
    const result = evaluatePumpSilverTransaction(fixture);
    expect(result.events).toEqual([]);
    expect(result.quarantines).toContainEqual(expect.objectContaining({ reason }));
  });

  it.each([
    ['legacy create metadata program', 'create', 5],
    ['legacy create token program', 'create', 9],
    ['create_v2 Token-2022 program', 'create_v2', 7],
    ['create_v2 mayhem program', 'create_v2', 9],
  ] as const)('enforces the fixed %s address', (_label, variant, roleIndex) => {
    const fixture = bronzeFixture(variant);
    const parent = fixture.instructions[0]!;
    parent.accounts[roleIndex] = fixture.accountKeys[0]!;
    parent.accountIndices[roleIndex] = 0;
    expect(evaluatePumpSilverTransaction(fixture).quarantines).toContainEqual(
      expect.objectContaining({ reason: 'account_role_or_pda_mismatch' }),
    );
  });

  it('rejects duplicate event coordinates before decoding or balance attribution', () => {
    const fixture = bronzeFixture('buy');
    fixture.instructions.push({ ...fixture.instructions[1]!, accounts: [...fixture.instructions[1]!.accounts], accountIndices: [...fixture.instructions[1]!.accountIndices] });
    const result = evaluatePumpSilverTransaction(fixture);
    expect(result.events).toEqual([]);
    expect(result.quarantines).toContainEqual(expect.objectContaining({ reason: 'duplicate_instruction_coordinate' }));
  });

  it.each([
    ['unsafe slot', (fixture: ReturnType<typeof bronzeFixture>) => { fixture.slot = Number.MAX_SAFE_INTEGER + 1; }, 'unsafe_transaction_coordinates'],
    ['inconsistent token decimals', (fixture: ReturnType<typeof bronzeFixture>) => {
      fixture.postTokenBalances.find((balance) => balance.owner === user && balance.mint === mint)!.decimals = 9;
    }, 'ambiguous_token_balance_attribution'],
    ['unsafe token decimals', (fixture: ReturnType<typeof bronzeFixture>) => {
      fixture.postTokenBalances.find((balance) => balance.owner === user && balance.mint === mint)!.decimals = Number.MAX_SAFE_INTEGER + 1;
    }, 'invalid_bronze_source'],
    ['wrong token delta', (fixture: ReturnType<typeof bronzeFixture>) => {
      fixture.postTokenBalances.find((balance) => balance.owner === user && balance.mint === mint)!.amount = '1';
    }, 'ambiguous_token_balance_attribution'],
    ['wrong user token owner', (fixture: ReturnType<typeof bronzeFixture>) => {
      fixture.preTokenBalances.find((balance) => balance.owner === user && balance.mint === mint)!.owner = key(40);
      fixture.postTokenBalances.find((balance) => balance.owner === user && balance.mint === mint)!.owner = key(40);
    }, 'ambiguous_token_balance_attribution'],
    ['missing curve balance', (fixture: ReturnType<typeof bronzeFixture>) => {
      const index = fixture.postTokenBalances.findIndex((balance) => balance.owner === curve && balance.mint === mint);
      fixture.postTokenBalances.splice(index, 1);
    }, 'ambiguous_token_balance_attribution'],
  ] as const)('quarantines %s', (_label, mutate, reason) => {
    const fixture = bronzeFixture('buy');
    mutate(fixture);
    const result = evaluatePumpSilverTransaction(fixture);
    expect(result.events).toEqual([]);
    expect(result.quarantines).toContainEqual(expect.objectContaining({ reason }));
  });

  it.each([
    ['SOL/quote amount mismatch', { quoteAmount: '999999' }, 'quote_sol_inconsistency'],
    ['SOL/quote reserve mismatch', { virtualQuoteReserves: '1' }, 'quote_sol_inconsistency'],
    ['real token reserve above virtual', { realTokenReserves: '900000000000001' }, 'reserve_inconsistency'],
    ['real SOL reserve above virtual', { realSolReserves: '32000000000', realQuoteReserves: '32000000000' }, 'reserve_inconsistency'],
    ['protocol fee mismatch', { fee: '9999' }, 'fee_inconsistency'],
    ['creator fee mismatch', { creatorFee: '4999' }, 'fee_inconsistency'],
    ['share BPS total mismatch', { shareholders: [{ address: creator, shareBps: 9999 }] }, 'invalid_shareholders'],
    ['duplicate shareholder', { shareholders: [{ address: creator, shareBps: 5000 }, { address: creator, shareBps: 5000 }] }, 'duplicate_shareholder'],
  ] as const)('quarantines %s without floating-point event math', (_label, eventOverrides, reason) => {
    const result = evaluatePumpSilverTransaction(bronzeFixture('buy', { eventOverrides }));
    expect(result.events).toEqual([]);
    expect(result.quarantines).toContainEqual(expect.objectContaining({ reason }));
  });

  it('requires exact 9-decimal WSOL attribution for v2 quote roles', () => {
    const fixture = bronzeFixture('buy_v2', { eventOverrides: { ixName: 'buy' } });
    const quoteBalance = fixture.postTokenBalances.find((balance) => balance.mint !== mint)!;
    quoteBalance.decimals = 8;
    const result = evaluatePumpSilverTransaction(fixture);
    expect(result.events).toEqual([]);
    expect(result.quarantines).toContainEqual(expect.objectContaining({ reason: 'ambiguous_token_balance_attribution' }));
  });

  it('fails closed when two trade events make whole-transaction balance attribution ambiguous', () => {
    const fixture = bronzeFixture('buy');
    const second = fixture.instructions[1]!;
    fixture.instructions.push({ ...second, instructionIndex: 1, accounts: [...second.accounts], accountIndices: [...second.accountIndices] });
    const result = evaluatePumpSilverTransaction(fixture);
    expect(result.events).toEqual([]);
    expect(result.quarantines).toContainEqual(expect.objectContaining({ reason: 'ambiguous_token_balance_attribution' }));
  });

  it('accepts canonical Anchor emit_cpi with only the signing event-authority account meta', () => {
    const fixture = bronzeFixture('buy');
    fixture.instructions[1]!.accounts = [eventAuthority];
    fixture.instructions[1]!.accountIndices = [fixture.accountKeys.indexOf(eventAuthority)];
    const result = evaluatePumpSilverTransaction(fixture);
    expect(result.events).toHaveLength(1);
    expect(result.quarantines.map((item) => item.reason)).not.toContain('event_cpi_account_mismatch');
  });

  it.each([
    ['wrong outer index', (fixture: ReturnType<typeof bronzeFixture>) => { fixture.instructions[1]!.parentInstructionIndex = 1; }],
    ['wrong stack height', (fixture: ReturnType<typeof bronzeFixture>) => { fixture.instructions[1]!.stackHeight = 3; }],
    ['event CPI account mismatch', (fixture: ReturnType<typeof bronzeFixture>) => {
      fixture.instructions[1]!.accounts[0] = fixture.accountKeys[0]!;
      fixture.instructions[1]!.accountIndices[0] = 0;
    }],
  ] as const)('fails closed on %s', (_label, mutate) => {
    const fixture = bronzeFixture('buy');
    mutate(fixture);
    const result = evaluatePumpSilverTransaction(fixture);
    expect(result.events).toEqual([]);
    expect(result.quarantines.length).toBeGreaterThan(0);
  });

  it('requires the event instruction to be inner', () => {
    const fixture = bronzeFixture('buy');
    Object.assign(fixture.instructions[1]!, {
      instructionLocation: 'top_level', instructionIndex: 1, parentInstructionIndex: undefined, stackHeight: undefined,
    });
    const result = evaluatePumpSilverTransaction(fixture);
    expect(result.events).toEqual([]);
    expect(result.quarantines).toContainEqual(expect.objectContaining({ reason: 'event_must_be_inner' }));
  });

  it('quarantines a supported parent when the event tag is attributed to the wrong program', () => {
    const fixture = bronzeFixture('buy');
    fixture.instructions[1]!.programId = key(1);
    fixture.instructions[1]!.programIdIndex = fixture.accountKeys.indexOf(key(1));
    const result = evaluatePumpSilverTransaction(fixture);
    expect(result.events).toEqual([]);
    expect(result.quarantines).toContainEqual(expect.objectContaining({ reason: 'missing_event_cpi' }));
  });

  it('retains a failed transaction as source evidence but never a successful trade classification', () => {
    const result = evaluatePumpSilverTransaction(bronzeFixture('sell', {
      failed: true,
      eventOverrides: { ixName: 'sell' },
    }));
    expect(result.source.executionStatus).toBe('failed');
    expect(result.events).toHaveLength(1);
    expect(result.events[0]).toMatchObject({ variant: 'sell', isExecutedTrade: false, researchReady: false });
    expect(result.isExecutedTrade).toBe(false);
    expect(result.quarantines).toContainEqual(expect.objectContaining({ reason: 'failed_transaction' }));
  });

  it('hashes canonical JSON with domain separation independent of property order and without special-key loss', () => {
    const left = JSON.parse('{"z":1,"__proto__":{"kept":true},"constructor":"kept","prototype":"kept","a":{"y":2,"x":3}}');
    const right = JSON.parse('{"prototype":"kept","a":{"x":3,"y":2},"constructor":"kept","__proto__":{"kept":true},"z":1}');
    expect(Object.keys(left)).toContain('__proto__');
    expect(canonicalPumpSilverHash(left)).toBe(canonicalPumpSilverHash(right));
    expect(canonicalPumpSilverHash(left)).not.toBe(canonicalPumpSilverHash({ z: 1, a: { x: 3, y: 2 } }));
  });

  it('binds exact signature and transaction coordinates into the canonical hash', () => {
    const left = evaluatePumpSilverTransaction(bronzeFixture('buy'));
    const changed = bronzeFixture('buy');
    changed.transactionIndex += 1;
    const right = evaluatePumpSilverTransaction(changed);
    expect(left.canonicalHash).not.toBe(right.canonicalHash);
  });

  it.each([
    ['missing outer instruction for an inner parent', (fixture: ReturnType<typeof bronzeFixture>) => { fixture.instructions.shift(); }],
    ['non-contiguous inner instruction index', (fixture: ReturnType<typeof bronzeFixture>) => { fixture.instructions[1]!.instructionIndex = 1; }],
    ['out-of-order normalized instruction vector', (fixture: ReturnType<typeof bronzeFixture>) => { fixture.instructions.reverse(); }],
  ] as const)('independently rejects %s', (_label, mutate) => {
    const fixture = bronzeFixture('buy', { innerParent: _label.startsWith('missing') });
    mutate(fixture);
    const result = evaluatePumpSilverTransaction(fixture);
    expect(result.events).toEqual([]);
    expect(result.quarantines).toContainEqual(expect.objectContaining({ reason: 'invalid_bronze_instruction_order' }));
  });

  it.each([
    ['sparse instruction vector', (fixture: ReturnType<typeof bronzeFixture>) => { fixture.instructions = new Array(1); }],
    ['null instruction', (fixture: ReturnType<typeof bronzeFixture>) => { fixture.instructions = [null] as never; }],
    ['non-string instruction data', (fixture: ReturnType<typeof bronzeFixture>) => { fixture.instructions[0]!.dataHex = null as never; }],
    ['unsafe instruction coordinate', (fixture: ReturnType<typeof bronzeFixture>) => {
      fixture.instructions[0]!.instructionIndex = Number.MAX_SAFE_INTEGER + 1;
    }],
  ] as const)('quarantines a malformed normalized Bronze shape: %s', (_label, mutate) => {
    const fixture = bronzeFixture('buy');
    mutate(fixture);
    expect(() => evaluatePumpSilverTransaction(fixture)).not.toThrow();
    const result = evaluatePumpSilverTransaction(fixture);
    expect(result.events).toEqual([]);
    expect(result.canonicalHash).toMatch(/^[0-9a-f]{64}$/);
    expect(result.quarantines).toContainEqual(expect.objectContaining({ reason: 'invalid_bronze_instruction_structure' }));
  });

  it('rejects the entire fixture registry when any extra entry is malformed', () => {
    const valid = {
      startInclusive: 361_000_000,
      endExclusive: 362_000_000,
      officialDocsCommit: PUMP_SILVER_FIXTURE_REGISTRY.officialDocsCommit,
      pumpIdlSha256: PUMP_SILVER_FIXTURE_REGISTRY.pumpIdlSha256,
    };
    const result = evaluatePumpSilverTransaction(bronzeFixture('buy'), [
      valid,
      { ...valid, startInclusive: 'bad' as never },
    ]);
    expect(result.events).toEqual([]);
    expect(result.quarantines).toContainEqual(expect.objectContaining({ reason: 'invalid_fixture_registry' }));
  });

  it('rejects a fixture registry entry with an unknown own field', () => {
    const entry = {
      startInclusive: 361_000_000,
      endExclusive: 362_000_000,
      officialDocsCommit: PUMP_SILVER_FIXTURE_REGISTRY.officialDocsCommit,
      pumpIdlSha256: PUMP_SILVER_FIXTURE_REGISTRY.pumpIdlSha256,
      extra: 'CANARY',
    };
    const result = evaluatePumpSilverTransaction(bronzeFixture('buy'), [entry] as never);
    expect(result.events).toEqual([]);
    expect(result.quarantines).toContainEqual(expect.objectContaining({ reason: 'invalid_fixture_registry' }));
  });

  it.each([
    ['non-throwing accessor', () => {
      const entry = {
        endExclusive: 362_000_000,
        officialDocsCommit: PUMP_SILVER_FIXTURE_REGISTRY.officialDocsCommit,
        pumpIdlSha256: PUMP_SILVER_FIXTURE_REGISTRY.pumpIdlSha256,
      };
      Object.defineProperty(entry, 'startInclusive', {
        enumerable: true,
        get: () => 361_000_000,
      });
      return entry;
    }],
    ['throwing accessor', () => {
      const entry = {
        endExclusive: 362_000_000,
        officialDocsCommit: PUMP_SILVER_FIXTURE_REGISTRY.officialDocsCommit,
        pumpIdlSha256: PUMP_SILVER_FIXTURE_REGISTRY.pumpIdlSha256,
      };
      Object.defineProperty(entry, 'startInclusive', {
        enumerable: true,
        get: () => { throw new Error('registry getter canary'); },
      });
      return entry;
    }],
    ['throwing proxy ownKeys trap', () => new Proxy({}, {
      ownKeys: () => { throw new Error('registry ownKeys canary'); },
    })],
    ['throwing proxy descriptor trap', () => new Proxy({
      startInclusive: 361_000_000,
      endExclusive: 362_000_000,
      officialDocsCommit: PUMP_SILVER_FIXTURE_REGISTRY.officialDocsCommit,
      pumpIdlSha256: PUMP_SILVER_FIXTURE_REGISTRY.pumpIdlSha256,
    }, {
      getOwnPropertyDescriptor: () => { throw new Error('registry descriptor canary'); },
    })],
    ['throwing proxy get trap', () => new Proxy({
      startInclusive: 361_000_000,
      endExclusive: 362_000_000,
      officialDocsCommit: PUMP_SILVER_FIXTURE_REGISTRY.officialDocsCommit,
      pumpIdlSha256: PUMP_SILVER_FIXTURE_REGISTRY.pumpIdlSha256,
    }, {
      get: () => { throw new Error('registry get canary'); },
    })],
  ] as const)('rejects registry entry with %s without throwing', (_label, makeEntry) => {
    const evaluate = () => evaluatePumpSilverTransaction(bronzeFixture('buy'), [makeEntry()] as never);
    expect(evaluate).not.toThrow();
    const result = evaluate();
    expect(result.events).toEqual([]);
    expect(result.quarantines).toContainEqual(expect.objectContaining({ reason: 'invalid_fixture_registry' }));
  });

  it.each([
    ['a sparse extra slot', () => {
      const entries = [{
        startInclusive: 361_000_000,
        endExclusive: 362_000_000,
        officialDocsCommit: PUMP_SILVER_FIXTURE_REGISTRY.officialDocsCommit,
        pumpIdlSha256: PUMP_SILVER_FIXTURE_REGISTRY.pumpIdlSha256,
      }];
      entries.length = 2;
      return entries;
    }],
    ['empty provenance text', () => [{
      startInclusive: 1,
      endExclusive: 2,
      officialDocsCommit: '',
      pumpIdlSha256: '',
    }, {
      startInclusive: 361_000_000,
      endExclusive: 362_000_000,
      officialDocsCommit: PUMP_SILVER_FIXTURE_REGISTRY.officialDocsCommit,
      pumpIdlSha256: PUMP_SILVER_FIXTURE_REGISTRY.pumpIdlSha256,
    }]],
  ] as const)('rejects the full registry for %s', (_label, entries) => {
    const result = evaluatePumpSilverTransaction(bronzeFixture('buy'), entries());
    expect(result.events).toEqual([]);
    expect(result.quarantines).toContainEqual(expect.objectContaining({ reason: 'invalid_fixture_registry' }));
  });

  it('bounds malformed public instruction traversal and quarantine output', () => {
    const fixture = bronzeFixture('buy');
    fixture.instructions = Array(10_000).fill(null) as never;
    const result = evaluatePumpSilverTransaction(fixture);
    expect(result.events).toEqual([]);
    expect(result.quarantines).toContainEqual(expect.objectContaining({ reason: 'invalid_bronze_source' }));
    expect(result.quarantines).toContainEqual(expect.objectContaining({ reason: 'invalid_bronze_instruction_structure' }));
    expect(result.quarantines.length).toBeLessThanOrEqual(2);
  });

  it('rejects a failed transaction whose token/native state did not roll back', () => {
    const failed = bronzeFixture('sell', { failed: true, eventOverrides: { ixName: 'sell' } });
    const succeeded = bronzeFixture('sell', { eventOverrides: { ixName: 'sell' } });
    failed.postTokenBalances = succeeded.postTokenBalances;
    failed.postBalancesLamports = succeeded.postBalancesLamports;
    const result = evaluatePumpSilverTransaction(failed);
    expect(result.events).toEqual([]);
    expect(result.quarantines).toContainEqual(expect.objectContaining({ reason: 'failed_state_not_rolled_back' }));
  });

  it('accepts canonical Pump create reserves where virtual token reserves exceed total supply', () => {
    const result = evaluatePumpSilverTransaction(bronzeFixture('create', {
      eventOverrides: {
        virtualTokenReserves: '1073000000000000',
        tokenTotalSupply: '1000000000000000',
      },
    }));
    expect(result.quarantines).toEqual([]);
    expect(result.events).toHaveLength(1);
  });

  it.each(['create', 'create_v2'] as const)('rejects %s real token reserves above total supply', (variant) => {
    const result = evaluatePumpSilverTransaction(bronzeFixture(variant, {
      eventOverrides: {
        virtualTokenReserves: '1073000000000000',
        realTokenReserves: '1000000000000001',
        tokenTotalSupply: '1000000000000000',
      },
    }));
    expect(result.events).toEqual([]);
    expect(result.quarantines).toContainEqual(expect.objectContaining({ reason: 'reserve_inconsistency' }));
  });

  it.each([
    ['buy global', 'buy', 0],
    ['buy associated bonding curve', 'buy', 4],
    ['buy global volume accumulator', 'buy', 12],
    ['buy user volume accumulator', 'buy', 13],
    ['buy fee config', 'buy', 14],
    ['create mint authority', 'create', 1],
    ['create associated bonding curve', 'create', 3],
    ['create global', 'create', 4],
    ['create metadata', 'create', 6],
    ['create_v2 global params', 'create_v2', 10],
    ['create_v2 SOL vault', 'create_v2', 11],
    ['create_v2 Mayhem state', 'create_v2', 12],
    ['buy_v2 quote fee ATA', 'buy_v2', 7],
    ['buy_v2 quote buyback ATA', 'buy_v2', 9],
    ['buy_v2 base curve ATA', 'buy_v2', 11],
    ['buy_v2 quote curve ATA', 'buy_v2', 12],
    ['buy_v2 quote user ATA', 'buy_v2', 15],
    ['buy_v2 creator ATA', 'buy_v2', 17],
    ['buy_v2 sharing config', 'buy_v2', 18],
    ['buy_v2 user volume accumulator', 'buy_v2', 20],
    ['buy_v2 user volume ATA', 'buy_v2', 21],
    ['buy_v2 fee config', 'buy_v2', 22],
  ] as const)('rejects a fabricated official PDA role: %s', (_label, variant, roleIndex) => {
    const fixture = bronzeFixture(variant);
    fixture.instructions[0]!.accounts[roleIndex] = fixture.accountKeys[0]!;
    fixture.instructions[0]!.accountIndices[roleIndex] = 0;
    const result = evaluatePumpSilverTransaction(fixture);
    expect(result.events).toEqual([]);
    expect(result.quarantines).toContainEqual(expect.objectContaining({ reason: 'account_role_or_pda_mismatch' }));
  });

  it('rejects a legacy trade without exact native SOL and fee movement', () => {
    const fixture = bronzeFixture('buy');
    fixture.postBalancesLamports = [...fixture.preBalancesLamports];
    fixture.postBalancesLamports[0] = (BigInt(fixture.preBalancesLamports[0]!) - BigInt(fixture.feeLamports)).toString();
    const result = evaluatePumpSilverTransaction(fixture);
    expect(result.events).toEqual([]);
    expect(result.quarantines).toContainEqual(expect.objectContaining({ reason: 'native_balance_inconsistency' }));
  });

  it('rejects a v2 trade without exact WSOL quote and fee movement', () => {
    const fixture = bronzeFixture('buy_v2', { eventOverrides: { ixName: 'buy' } });
    for (const post of fixture.postTokenBalances.filter((balance) => balance.mint !== mint)) {
      const pre = fixture.preTokenBalances.find((balance) => balance.accountIndex === post.accountIndex);
      if (pre) post.amount = pre.amount;
    }
    const result = evaluatePumpSilverTransaction(fixture);
    expect(result.events).toEqual([]);
    expect(result.quarantines).toContainEqual(expect.objectContaining({ reason: 'quote_balance_inconsistency' }));
  });

  it('rejects a trade whose event real reserves do not match post-state curve balances', () => {
    const result = evaluatePumpSilverTransaction(bronzeFixture('buy', {
      eventOverrides: { realTokenReserves: '99999999' },
    }));
    expect(result.events).toEqual([]);
    expect(result.quarantines).toContainEqual(expect.objectContaining({ reason: 'reserve_balance_inconsistency' }));
  });

  it('rejects a non-coercible signature without throwing or echoing it', () => {
    const fixture = bronzeFixture('buy') as unknown as Record<string, unknown>;
    fixture.signature = Object.create(null);
    expect(() => evaluatePumpSilverTransaction(fixture as never)).not.toThrow();
    const result = evaluatePumpSilverTransaction(fixture as never);
    expect(result.source.signature).toBe('');
    expect(result.events).toEqual([]);
    expect(result.quarantines).toContainEqual(expect.objectContaining({ reason: 'invalid_bronze_source' }));
  });

  it('rejects and does not echo a signature above the Bronze producer maximum', () => {
    const fixture = bronzeFixture('buy');
    fixture.signature = 'S'.repeat(89);
    const result = evaluatePumpSilverTransaction(fixture);
    expect(result.source.signature).toBe('');
    expect(result.events).toEqual([]);
    expect(result.quarantines).toContainEqual(expect.objectContaining({ reason: 'invalid_bronze_source' }));
  });

  it.each([
    ['duplicate account key', (fixture: ReturnType<typeof bronzeFixture>) => { fixture.accountKeys.push(fixture.accountKeys[0]!); }],
    ['invalid signature', (fixture: ReturnType<typeof bronzeFixture>) => { fixture.signature = 'not-base58'; }],
    ['non-canonical instruction hex', (fixture: ReturnType<typeof bronzeFixture>) => { fixture.instructions[0]!.dataHex = 'zz'; }],
    ['sparse pre-token balances', (fixture: ReturnType<typeof bronzeFixture>) => { fixture.preTokenBalances = new Array(1); }],
    ['null pre-token balance', (fixture: ReturnType<typeof bronzeFixture>) => { fixture.preTokenBalances = [null] as never; }],
    ['invalid block time', (fixture: ReturnType<typeof bronzeFixture>) => { fixture.blockTime = 'not-time'; }],
    ['invalid fee', (fixture: ReturnType<typeof bronzeFixture>) => { fixture.feeLamports = '-1'; }],
    ['invalid log evidence', (fixture: ReturnType<typeof bronzeFixture>) => { fixture.logMessages = [null] as never; }],
    ['native balance length mismatch', (fixture: ReturnType<typeof bronzeFixture>) => { fixture.postBalancesLamports.pop(); }],
  ] as const)('revalidates normalized Bronze producer evidence: %s', (_label, mutate) => {
    const fixture = bronzeFixture('buy');
    mutate(fixture);
    expect(() => evaluatePumpSilverTransaction(fixture)).not.toThrow();
    const result = evaluatePumpSilverTransaction(fixture);
    expect(result.events).toEqual([]);
    expect(result.quarantines).toContainEqual(expect.objectContaining({ reason: 'invalid_bronze_source' }));
  });

  it.each([-1, 256])('rejects token decimals outside the Bronze UInt8 domain: %s', (decimals) => {
    const fixture = bronzeFixture('buy');
    for (const balance of [...fixture.preTokenBalances, ...fixture.postTokenBalances]) balance.decimals = decimals;
    const result = evaluatePumpSilverTransaction(fixture);
    expect(result.events).toEqual([]);
    expect(result.quarantines).toContainEqual(expect.objectContaining({ reason: 'invalid_bronze_source' }));
  });
});
