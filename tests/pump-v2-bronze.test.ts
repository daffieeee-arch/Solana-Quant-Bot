import { describe, expect, it, vi } from 'vitest';
import bs58 from 'bs58';
import { PUMP_PROGRAM_ID, base58Encode, deriveBondingCurve } from '../src/pump-address.js';
import { PUMP_DISCRIMINATORS } from '../src/pump-parser.js';
import { capturePumpV2BronzeTransaction } from '../src/research/pump-v2-bronze.js';

const signature = bs58.encode(Buffer.alloc(64, 7));
const payer = base58Encode(Buffer.alloc(32, 1));
const loadedWritable = base58Encode(Buffer.alloc(32, 2));
const loadedReadonly = base58Encode(Buffer.alloc(32, 3));
const mint = base58Encode(Buffer.alloc(32, 4));
const curve = deriveBondingCurve(mint);
const wrongCurve = base58Encode(Buffer.alloc(32, 5));
const mintTwo = base58Encode(Buffer.alloc(32, 8));
const curveTwo = deriveBondingCurve(mintTwo);
const TOKEN_PROGRAM_ID = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA';
const tooManyLoadedAccounts = Array.from({ length: 256 }, (_, index) => {
  const bytes = Buffer.alloc(32, 0);
  bytes.writeUInt16LE(index, 0);
  bytes[31] = 6;
  return base58Encode(bytes);
});

function source(overrides: Record<string, unknown> = {}) {
  return {
    slot: 361_000_001,
    transactionIndex: 7,
    signature,
    blockTime: '2026-08-17T00:00:00.000Z',
    err: null,
    feeLamports: '5000',
    logMessages: ['Program 11111111111111111111111111111111 invoke [1]', 'Program 11111111111111111111111111111111 success'],
    staticAccountKeys: [payer],
    loadedAddresses: {
      writable: [loadedWritable],
      readonly: [loadedReadonly],
    },
    preBalancesLamports: ['10000000001', '20000000002', '30000000003'],
    postBalancesLamports: ['9000000001', '21000000002', '30000000003'],
    topLevelInstructions: [],
    innerInstructionGroups: [],
    preTokenBalances: [],
    postTokenBalances: [],
    ...overrides,
  };
}

describe('Pump v2 Bronze capture', () => {
  it('captures canonical transaction coordinates, loaded-address order and exact native balances', () => {
    const captured = capturePumpV2BronzeTransaction(source());

    expect(captured).toEqual({
      schemaVersion: 'PUMP_V2_BRONZE_TRANSACTION_1',
      slot: 361_000_001,
      transactionIndex: 7,
      signature,
      blockTime: '2026-08-17T00:00:00.000Z',
      executionStatus: 'succeeded',
      feeLamports: '5000',
      logMessages: ['Program 11111111111111111111111111111111 invoke [1]', 'Program 11111111111111111111111111111111 success'],
      accountKeys: [payer, loadedWritable, loadedReadonly],
      preBalancesLamports: ['10000000001', '20000000002', '30000000003'],
      postBalancesLamports: ['9000000001', '21000000002', '30000000003'],
      instructions: [],
      pumpCandidates: [],
      quarantines: [],
      preTokenBalances: [],
      postTokenBalances: [],
    });
  });

  it.each([
    ['unsafe slot', source({ slot: Number.MAX_SAFE_INTEGER + 1 }), /invalid_slot/],
    ['negative transaction index', source({ transactionIndex: -1 }), /invalid_transaction_index/],
    ['non-canonical signature', source({ signature: 'not-base58' }), /invalid_signature/],
    ['oversized signature before base58 decode', source({ signature: '1'.repeat(100_000) }), /invalid_signature/],
    ['non-canonical timestamp', source({ blockTime: '2026-08-17T00:00:00Z' }), /invalid_block_time/],
    ['implicit execution state', source({ err: undefined }), /missing_execution_state/],
    ['malformed execution error', source({ err: false }), /invalid_execution_error/],
    ['native balance length drift', source({ postBalancesLamports: ['1'] }), /native_balance_length_mismatch/],
    ['non-integer lamports', source({ preBalancesLamports: ['1.5', '2', '3'] }), /invalid_lamport_amount/],
  ] as const)('rejects %s instead of emitting a partial envelope', (_label, candidate, error) => {
    expect(() => capturePumpV2BronzeTransaction(candidate)).toThrow(error);
  });

  it('preserves top-level and inner CPI coordinates while resolving loaded account indexes', () => {
    const captured = capturePumpV2BronzeTransaction(source({
      loadedAddresses: { writable: [mint, curve], readonly: [PUMP_PROGRAM_ID] },
      preBalancesLamports: ['100', '200', '300', '400'],
      postBalancesLamports: ['90', '210', '300', '400'],
      topLevelInstructions: [{
        programIdIndex: 0,
        accountIndices: [1],
        dataHex: '0102030405060708',
      }],
      innerInstructionGroups: [{
        parentInstructionIndex: 0,
        instructions: [{
          programIdIndex: 3,
          accountIndices: [1, 2],
          dataHex: `${PUMP_DISCRIMINATORS.buy}${'00'.repeat(16)}`,
          stackHeight: 2,
        }],
      }],
    }));

    expect(captured.instructions).toEqual([
      {
        instructionLocation: 'top_level',
        instructionIndex: 0,
        programIdIndex: 0,
        programId: payer,
        accountIndices: [1],
        accounts: [mint],
        dataHex: '0102030405060708',
      },
      {
        instructionLocation: 'inner',
        parentInstructionIndex: 0,
        instructionIndex: 0,
        stackHeight: 2,
        programIdIndex: 3,
        programId: PUMP_PROGRAM_ID,
        accountIndices: [1, 2],
        accounts: [mint, curve],
        dataHex: `${PUMP_DISCRIMINATORS.buy}${'00'.repeat(16)}`,
      },
    ]);
  });

  it('ignores known Pump bytes when the exact program ID does not match', () => {
    const captured = capturePumpV2BronzeTransaction(source({
      loadedAddresses: { writable: [mint, curve], readonly: [] },
      topLevelInstructions: [{
        programIdIndex: 0,
        accountIndices: [1, 2],
        dataHex: `${PUMP_DISCRIMINATORS.buy}${'00'.repeat(8)}`,
      }],
    }));
    expect(captured.pumpCandidates).toEqual([]);
    expect(captured.quarantines).toEqual([]);
  });

  it.each([
    [
      'out-of-range program index',
      source({ topLevelInstructions: [{ programIdIndex: 3, accountIndices: [], dataHex: '00' }] }),
      /invalid_instruction/,
    ],
    [
      'out-of-range account index',
      source({ topLevelInstructions: [{ programIdIndex: 0, accountIndices: [3], dataHex: '00' }] }),
      /invalid_instruction/,
    ],
    [
      'non-canonical instruction hex',
      source({ topLevelInstructions: [{ programIdIndex: 0, accountIndices: [], dataHex: '0' }] }),
      /invalid_instruction/,
    ],
    [
      'invalid stack height',
      source({ topLevelInstructions: [{ programIdIndex: 0, accountIndices: [], dataHex: '00', stackHeight: 0 }] }),
      /invalid_stack_height/,
    ],
    [
      'duplicate inner group',
      source({
        topLevelInstructions: [{ programIdIndex: 0, accountIndices: [], dataHex: '00' }],
        innerInstructionGroups: [
          { parentInstructionIndex: 0, instructions: [] },
          { parentInstructionIndex: 0, instructions: [] },
        ],
      }),
      /duplicate_inner_instruction_group/,
    ],
  ] as const)('rejects %s', (_label, candidate, error) => {
    expect(() => capturePumpV2BronzeTransaction(candidate)).toThrow(error);
  });

  it('quarantines an ambiguous instruction containing two canonical mint-to-curve pairs', () => {
    const captured = capturePumpV2BronzeTransaction(source({
      staticAccountKeys: [payer, PUMP_PROGRAM_ID],
      loadedAddresses: { writable: [mint, curve, mintTwo, curveTwo], readonly: [] },
      preBalancesLamports: ['1', '1', '1', '1', '1', '1'],
      postBalancesLamports: ['1', '1', '1', '1', '1', '1'],
      topLevelInstructions: [{
        programIdIndex: 1,
        accountIndices: [2, 3, 4, 5],
        dataHex: `${PUMP_DISCRIMINATORS.buy}${'00'.repeat(8)}`,
      }],
    }));

    expect(captured.pumpCandidates[0]).toMatchObject({
      parserStatus: 'quarantined',
      mint: null,
      curve: null,
    });
    expect(captured.quarantines[0]?.reason).toBe('unresolved_pump_identity');
  });

  it('captures every Pump candidate and quarantines an unknown discriminator without guessing', () => {
    const unknownDiscriminator = 'deadbeefcafebabe';
    const captured = capturePumpV2BronzeTransaction(source({
      staticAccountKeys: [payer, PUMP_PROGRAM_ID],
      loadedAddresses: { writable: [mint, curve], readonly: [] },
      preBalancesLamports: ['100', '200', '300', '400'],
      postBalancesLamports: ['90', '210', '300', '400'],
      topLevelInstructions: [{
        programIdIndex: 1,
        accountIndices: [2, 3],
        dataHex: `${PUMP_DISCRIMINATORS.buy}${'00'.repeat(16)}`,
      }],
      innerInstructionGroups: [{
        parentInstructionIndex: 0,
        instructions: [
          {
            programIdIndex: 1,
            accountIndices: [2, 3],
            dataHex: `${unknownDiscriminator}${'11'.repeat(8)}`,
            stackHeight: 2,
          },
          {
            programIdIndex: 1,
            accountIndices: [2, 3],
            dataHex: `${PUMP_DISCRIMINATORS.sell}${'22'.repeat(8)}`,
            stackHeight: 2,
          },
        ],
      }],
    }));

    const prefix = `361000001:7:${signature}`;
    expect(captured.pumpCandidates).toEqual([
      {
        eventKey: `${prefix}:top_level:-:0:${PUMP_PROGRAM_ID}:${PUMP_DISCRIMINATORS.buy}`,
        instructionLocation: 'top_level',
        instructionIndex: 0,
        discriminatorHex: PUMP_DISCRIMINATORS.buy,
        discriminatorSource: 'official_idl',
        parserStatus: 'known_discriminator',
        variant: 'buy',
        kind: 'buy',
        mint,
        curve,
        executionStatus: 'succeeded',
      },
      {
        eventKey: `${prefix}:inner:0:0:${PUMP_PROGRAM_ID}:${unknownDiscriminator}`,
        instructionLocation: 'inner',
        parentInstructionIndex: 0,
        instructionIndex: 0,
        discriminatorHex: unknownDiscriminator,
        discriminatorSource: null,
        parserStatus: 'quarantined',
        variant: null,
        kind: null,
        mint,
        curve,
        executionStatus: 'succeeded',
      },
      {
        eventKey: `${prefix}:inner:0:1:${PUMP_PROGRAM_ID}:${PUMP_DISCRIMINATORS.sell}`,
        instructionLocation: 'inner',
        parentInstructionIndex: 0,
        instructionIndex: 1,
        discriminatorHex: PUMP_DISCRIMINATORS.sell,
        discriminatorSource: 'official_idl',
        parserStatus: 'known_discriminator',
        variant: 'sell',
        kind: 'sell',
        mint,
        curve,
        executionStatus: 'succeeded',
      },
    ]);
    expect(captured.quarantines).toEqual([{
      eventKey: `${prefix}:inner:0:0:${PUMP_PROGRAM_ID}:${unknownDiscriminator}`,
      reason: 'unknown_pump_discriminator',
      discriminatorHex: unknownDiscriminator,
    }]);
    for (const candidate of captured.pumpCandidates) {
      expect(candidate).not.toHaveProperty('isExecutedTrade');
    }
  });

  it('labels an observed runtime dispatcher separately from official IDL variants', () => {
    const captured = capturePumpV2BronzeTransaction(source({
      staticAccountKeys: [payer, PUMP_PROGRAM_ID],
      loadedAddresses: { writable: [mint, curve], readonly: [] },
      preBalancesLamports: ['1', '1', '1', '1'],
      postBalancesLamports: ['1', '1', '1', '1'],
      topLevelInstructions: [{
        programIdIndex: 1,
        accountIndices: [2, 3],
        dataHex: `${PUMP_DISCRIMINATORS.liveBuy}${'00'.repeat(8)}`,
      }],
    }));

    expect(captured.pumpCandidates[0]).toMatchObject({
      variant: 'live_buy_dispatcher',
      kind: 'buy',
      discriminatorSource: 'observed_runtime',
      parserStatus: 'known_discriminator',
    });
  });

  it('retains a failed known Pump instruction but never marks it as an executed trade', () => {
    const captured = capturePumpV2BronzeTransaction(source({
      err: { InstructionError: [0, 'Custom'] },
      staticAccountKeys: [payer, PUMP_PROGRAM_ID],
      loadedAddresses: { writable: [mint, curve], readonly: [] },
      preBalancesLamports: ['100', '200', '300', '400'],
      postBalancesLamports: ['100', '200', '300', '400'],
      topLevelInstructions: [{
        programIdIndex: 1,
        accountIndices: [2, 3],
        dataHex: `${PUMP_DISCRIMINATORS.buy}${'00'.repeat(8)}`,
      }],
    }));

    expect(captured.executionStatus).toBe('failed');
    expect(captured.pumpCandidates).toHaveLength(1);
    expect(captured.pumpCandidates[0]).toMatchObject({
      parserStatus: 'known_discriminator',
      variant: 'buy',
      kind: 'buy',
      executionStatus: 'failed',
    });
    expect(captured.pumpCandidates[0]).not.toHaveProperty('isExecutedTrade');
    expect(captured.quarantines).toEqual([]);
  });

  it('normalizes token balances by account index while preserving exact raw integer strings', () => {
    const captured = capturePumpV2BronzeTransaction(source({
      preTokenBalances: [
        { accountIndex: 2, mint, owner: payer, programId: TOKEN_PROGRAM_ID, decimals: 6, amount: '9007199254740993' },
        { accountIndex: 1, mint, owner: payer, programId: TOKEN_PROGRAM_ID, decimals: 6, amount: '0' },
      ],
      postTokenBalances: [
        { accountIndex: 1, mint, owner: payer, programId: TOKEN_PROGRAM_ID, decimals: 6, amount: '25' },
        { accountIndex: 2, mint, owner: payer, programId: TOKEN_PROGRAM_ID, decimals: 6, amount: '9007199254740968' },
      ],
    }));

    expect(captured.preTokenBalances).toEqual([
      { accountIndex: 1, mint, owner: payer, programId: TOKEN_PROGRAM_ID, decimals: 6, amount: '0' },
      { accountIndex: 2, mint, owner: payer, programId: TOKEN_PROGRAM_ID, decimals: 6, amount: '9007199254740993' },
    ]);
    expect(captured.postTokenBalances).toEqual([
      { accountIndex: 1, mint, owner: payer, programId: TOKEN_PROGRAM_ID, decimals: 6, amount: '25' },
      { accountIndex: 2, mint, owner: payer, programId: TOKEN_PROGRAM_ID, decimals: 6, amount: '9007199254740968' },
    ]);
  });

  it('preserves a valid token authority transition instead of rejecting pre/post owner drift', () => {
    const captured = capturePumpV2BronzeTransaction(source({
      preTokenBalances: [{
        accountIndex: 1, mint, owner: payer, programId: TOKEN_PROGRAM_ID, decimals: 6, amount: '1',
      }],
      postTokenBalances: [{
        accountIndex: 1, mint, owner: loadedWritable, programId: TOKEN_PROGRAM_ID, decimals: 6, amount: '1',
      }],
    }));

    expect(captured.preTokenBalances[0]?.owner).toBe(payer);
    expect(captured.postTokenBalances[0]?.owner).toBe(loadedWritable);
    expect(captured.preTokenBalances[0]?.programId).toBe(TOKEN_PROGRAM_ID);
    expect(captured.postTokenBalances[0]?.programId).toBe(TOKEN_PROGRAM_ID);
  });

  it.each([
    [
      'duplicate account index',
      source({ preTokenBalances: [
        { accountIndex: 1, mint, owner: payer, programId: TOKEN_PROGRAM_ID, decimals: 6, amount: '1' },
        { accountIndex: 1, mint, owner: payer, programId: TOKEN_PROGRAM_ID, decimals: 6, amount: '2' },
      ] }),
      /duplicate_token_balance_account_index/,
    ],
    [
      'out-of-range account index',
      source({ preTokenBalances: [{ accountIndex: 3, mint, owner: payer, programId: TOKEN_PROGRAM_ID, decimals: 6, amount: '1' }] }),
      /invalid_token_balance/,
    ],
    [
      'fractional raw amount',
      source({ preTokenBalances: [{ accountIndex: 1, mint, owner: payer, programId: TOKEN_PROGRAM_ID, decimals: 6, amount: '1.5' }] }),
      /invalid_token_balance/,
    ],
    [
      'invalid decimals',
      source({ preTokenBalances: [{ accountIndex: 1, mint, owner: payer, programId: TOKEN_PROGRAM_ID, decimals: 256, amount: '1' }] }),
      /invalid_token_balance/,
    ],
    [
      'invalid mint key',
      source({ preTokenBalances: [{ accountIndex: 1, mint: 'invalid', owner: payer, programId: TOKEN_PROGRAM_ID, decimals: 6, amount: '1' }] }),
      /invalid_token_balance/,
    ],
  ] as const)('rejects %s', (_label, candidate, error) => {
    expect(() => capturePumpV2BronzeTransaction(candidate)).toThrow(error);
  });

  it.each([
    ['empty static account vector', source({
      staticAccountKeys: [],
      preBalancesLamports: ['2', '3'],
      postBalancesLamports: ['2', '3'],
    }), /empty_static_account_keys/],
    ['invalid static account key', source({ staticAccountKeys: ['invalid'] }), /invalid_account_key/],
    [
      'duplicate resolved account key',
      source({ loadedAddresses: { writable: [payer], readonly: [loadedReadonly] } }),
      /duplicate_account_key/,
    ],
    [
      'lamports above UInt64',
      source({ preBalancesLamports: ['18446744073709551616', '2', '3'] }),
      /invalid_lamport_amount/,
    ],
    [
      'resolved account vector above the u8 index domain',
      source({
        loadedAddresses: { writable: tooManyLoadedAccounts, readonly: [] },
        preBalancesLamports: Array.from({ length: 257 }, () => '0'),
        postBalancesLamports: Array.from({ length: 257 }, () => '0'),
      }),
      /too_many_account_keys/,
    ],
    [
      'top-level program ID loaded through an address table',
      source({
        loadedAddresses: { writable: [mint, curve], readonly: [PUMP_PROGRAM_ID] },
        preBalancesLamports: ['100', '200', '300', '400'],
        postBalancesLamports: ['90', '210', '300', '400'],
        topLevelInstructions: [{
          programIdIndex: 3,
          accountIndices: [1, 2],
          dataHex: `${PUMP_DISCRIMINATORS.buy}${'00'.repeat(8)}`,
        }],
      }),
      /top_level_program_must_be_static/,
    ],
  ] as const)('rejects %s in the source envelope', (_label, candidate, error) => {
    expect(() => capturePumpV2BronzeTransaction(candidate)).toThrow(error);
  });

  it.each([
    ['missing fee', source({ feeLamports: undefined }), /invalid_fee_lamports/],
    ['fractional fee', source({ feeLamports: '1.5' }), /invalid_fee_lamports/],
    ['fee above UInt64', source({ feeLamports: '18446744073709551616' }), /invalid_fee_lamports/],
    ['missing logs', source({ logMessages: null }), /invalid_log_messages/],
    ['non-string log entry', source({ logMessages: ['ok', 3] }), /invalid_log_messages/],
    ['oversized aggregate logs', source({ logMessages: ['x'.repeat(10_001)] }), /invalid_log_messages/],
  ] as const)('rejects %s instead of losing fee/log provenance', (_label, candidate, error) => {
    expect(() => capturePumpV2BronzeTransaction(candidate)).toThrow(error);
  });

  it('rejects sparse log arrays instead of emitting undefined as null', () => {
    expect(() => capturePumpV2BronzeTransaction(source({
      logMessages: new Array<string>(1),
    }))).toThrow(/invalid_log_messages/);
  });

  it('rejects an oversized log by code-unit length before UTF-8 byte scanning', () => {
    const byteLength = vi.spyOn(Buffer, 'byteLength');
    try {
      expect(() => capturePumpV2BronzeTransaction(source({
        logMessages: ['x'.repeat(10_001)],
      }))).toThrow(/invalid_log_messages/);
      expect(byteLength).not.toHaveBeenCalled();
    } finally {
      byteLength.mockRestore();
    }
  });

  it('rejects sparse instruction account indices', () => {
    expect(() => capturePumpV2BronzeTransaction(source({
      topLevelInstructions: [{
        programIdIndex: 0,
        accountIndices: new Array<number>(1),
        dataHex: '',
      }],
    }))).toThrow(/invalid_instruction/);
  });

  it.each([
    ['top-level stack height 2', source({
      topLevelInstructions: [{ programIdIndex: 0, accountIndices: [], dataHex: '', stackHeight: 2 }],
    })],
    ['inner stack height 1', source({
      topLevelInstructions: [{ programIdIndex: 0, accountIndices: [], dataHex: '' }],
      innerInstructionGroups: [{
        parentInstructionIndex: 0,
        instructions: [{ programIdIndex: 0, accountIndices: [], dataHex: '', stackHeight: 1 }],
      }],
    })],
    ['top-level unbounded stack height', source({
      topLevelInstructions: [{
        programIdIndex: 0,
        accountIndices: [],
        dataHex: '',
        stackHeight: Number.MAX_SAFE_INTEGER,
      }],
    })],
    ['inner stack height above SIMD-0268 maximum', source({
      topLevelInstructions: [{ programIdIndex: 0, accountIndices: [], dataHex: '' }],
      innerInstructionGroups: [{
        parentInstructionIndex: 0,
        instructions: [{ programIdIndex: 0, accountIndices: [], dataHex: '', stackHeight: 10 }],
      }],
    })],
  ])('rejects Solana-impossible %s', (_label, candidate) => {
    expect(() => capturePumpV2BronzeTransaction(candidate)).toThrow(/invalid_stack_height/);
  });

  it('accepts reported top-level height 1 and inner heights 2 through SIMD-0268 maximum 9', () => {
    const captured = capturePumpV2BronzeTransaction(source({
      topLevelInstructions: [{ programIdIndex: 0, accountIndices: [], dataHex: '', stackHeight: 1 }],
      innerInstructionGroups: [{
        parentInstructionIndex: 0,
        instructions: [
          { programIdIndex: 0, accountIndices: [], dataHex: '', stackHeight: 2 },
          { programIdIndex: 0, accountIndices: [], dataHex: '', stackHeight: 9 },
        ],
      }],
    }));
    expect(captured.instructions.map((instruction) => instruction.stackHeight)).toEqual([1, 2, 9]);
  });

  it('caches transaction-wide PDA derivation across the maximum accepted account-reference budget', () => {
    const accountIndices = Array.from({ length: 16 }, (_, index) => index % 2 === 0 ? 2 : 3);
    const candidate = source({
      staticAccountKeys: [payer, PUMP_PROGRAM_ID],
      loadedAddresses: { writable: [mint, curve], readonly: [] },
      preBalancesLamports: ['1', '1', '1', '1'],
      postBalancesLamports: ['1', '1', '1', '1'],
      topLevelInstructions: Array.from({ length: 256 }, () => ({
        programIdIndex: 1,
        accountIndices,
        dataHex: PUMP_DISCRIMINATORS.buy,
      })),
    });
    const started = performance.now();
    const captured = capturePumpV2BronzeTransaction(candidate);
    expect(captured.pumpCandidates).toHaveLength(256);
    expect(performance.now() - started).toBeLessThan(500);
  });

  it('rejects transaction-wide instruction account-reference amplification', () => {
    const accountIndices = Array.from({ length: 256 }, (_, index) => index % 2 === 0 ? 2 : 3);
    expect(() => capturePumpV2BronzeTransaction(source({
      staticAccountKeys: [payer, PUMP_PROGRAM_ID],
      loadedAddresses: { writable: [mint, curve], readonly: [] },
      preBalancesLamports: ['1', '1', '1', '1'],
      postBalancesLamports: ['1', '1', '1', '1'],
      topLevelInstructions: Array.from({ length: 17 }, () => ({
        programIdIndex: 1,
        accountIndices,
        dataHex: PUMP_DISCRIMINATORS.buy,
      })),
    }))).toThrow(/too_many_instruction_account_references/);
  });

  it('rejects oversized valid-alphabet base58 before superlinear decode work', () => {
    const started = performance.now();
    expect(() => capturePumpV2BronzeTransaction(source({
      staticAccountKeys: ['2'.repeat(100_000)],
    }))).toThrow(/invalid_account_key/);
    expect(performance.now() - started).toBeLessThan(250);
  });

  it.each([
    [
      'oversized instruction data',
      source({ topLevelInstructions: [{ programIdIndex: 0, accountIndices: [], dataHex: '00'.repeat(1_233) }] }),
      /invalid_instruction/,
    ],
    [
      'oversized top-level instruction array',
      source({ topLevelInstructions: Array.from({ length: 257 }, () => ({
        programIdIndex: 0, accountIndices: [], dataHex: '',
      })) }),
      /too_many_instructions/,
    ],
  ] as const)('rejects %s within the capture budget', (_label, candidate, error) => {
    expect(() => capturePumpV2BronzeTransaction(candidate)).toThrow(error);
  });

  it('quarantines a known trade when no unique canonical mint-to-curve PDA relation exists', () => {
    const captured = capturePumpV2BronzeTransaction(source({
      staticAccountKeys: [payer, PUMP_PROGRAM_ID],
      loadedAddresses: { writable: [mint, wrongCurve], readonly: [] },
      preBalancesLamports: ['100', '200', '300', '400'],
      postBalancesLamports: ['90', '210', '300', '400'],
      topLevelInstructions: [{
        programIdIndex: 1,
        accountIndices: [2, 3],
        dataHex: `${PUMP_DISCRIMINATORS.buy}${'00'.repeat(8)}`,
      }],
    }));

    expect(captured.pumpCandidates).toHaveLength(1);
    expect(captured.pumpCandidates[0]).toMatchObject({
      parserStatus: 'quarantined',
      variant: 'buy',
      kind: 'buy',
      mint: null,
      curve: null,
    });
    expect(captured.quarantines).toEqual([{
      eventKey: captured.pumpCandidates[0]!.eventKey,
      reason: 'unresolved_pump_identity',
      discriminatorHex: PUMP_DISCRIMINATORS.buy,
    }]);
  });
});
