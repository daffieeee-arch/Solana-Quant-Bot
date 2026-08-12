import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SolanaRpcProvider } from '../src/providers/solana-rpc.js';
import type { MarketSnapshot } from '../src/scoring.js';
import { loadConfig } from '../src/config.js';
import { createPortfolio } from '../src/portfolio.js';
import { Scanner } from '../src/scanner.js';
import { VALID_SOLANA_INSTRUCTION_DATA } from './solana-layout-fixtures.js';

class MockWebSocket {
  addEventListener() {}
  removeEventListener() {}
  send() {}
  close() {}
}
(globalThis as Record<string, unknown>).WebSocket = MockWebSocket;

const ORIGINAL_FETCH = globalThis.fetch;
const PUMP_FUN = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P';
const RAYDIUM_AMM = '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8';
const RAYDIUM_CPMM = 'CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C';
const PUMP_SWAP_AMM = 'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA';
const METEORA = 'LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo';
const MOONSHOT = 'MoonCVVNZFSYkqNXP6bxHLPL6QQJiMagDL3qcqUQTrG';
const POOL = 'AzmP6MBVjPLt44MMoNpZTw5pxYaiSRdww1aZB7kR5zTT';
const BASE_MINT = 'GxkeU4o26Mey9nu44S2iDpuRUvwCohJjx2bMBh67pump';
const WSOL = 'So11111111111111111111111111111111111111112';
const PUMP_FUN_MINT = 'PumpFunMint111111111111111111111111111111111';
const PUMP_FUN_CURVE = 'PumpFunCurve11111111111111111111111111111111';
// Exact instruction data observed in Franktard's confirmed creation transaction.
const CREATE_POOL_DATA = 'iPiwDbPRj3YavFpj3AxMZtPvR19L3VdUtvXUjHSbo4kstzjSPjXKUWTMLAKkM4qvBVPdXaUjfvWQhhFvxf';

function exactAccounts(roleAccounts: string[], count: number, prefix = 'unused-role') {
  return [...roleAccounts, ...Array.from({ length: count - roleAccounts.length }, (_, index) => `${prefix}-${index}`)];
}

const MALFORMED_LAYOUT_CASES = ([
  { name: 'PumpSwap create_pool', program: PUMP_SWAP_AMM, data: CREATE_POOL_DATA, accounts: exactAccounts([POOL, 'global', 'creator', BASE_MINT, WSOL], 18) },
  { name: 'Pump.fun create', program: PUMP_FUN, data: VALID_SOLANA_INSTRUCTION_DATA.pumpFunCreate, accounts: exactAccounts([PUMP_FUN_MINT, 'mint-authority', PUMP_FUN_CURVE], 14) },
  { name: 'Pump.fun create_v2', program: PUMP_FUN, data: VALID_SOLANA_INSTRUCTION_DATA.pumpFunCreateV2, accounts: exactAccounts([PUMP_FUN_MINT, 'mint-authority', PUMP_FUN_CURVE], 16) },
  { name: 'Raydium CPMM initialize', program: RAYDIUM_CPMM, data: VALID_SOLANA_INSTRUCTION_DATA.raydiumCpmmInitialize, accounts: exactAccounts(['creator', 'config', 'authority', POOL, BASE_MINT, WSOL], 20) },
  { name: 'Raydium CPMM initialize_with_permission', program: RAYDIUM_CPMM, data: VALID_SOLANA_INSTRUCTION_DATA.raydiumCpmmInitializeWithPermission, accounts: exactAccounts(['payer', 'creator', 'config', 'authority', POOL, BASE_MINT, WSOL], 21) },
  { name: 'Meteora initialize_lb_pair', program: METEORA, data: VALID_SOLANA_INSTRUCTION_DATA.meteoraInitializeLbPair, accounts: exactAccounts([POOL, 'reserve-x', BASE_MINT, WSOL], 14) },
  { name: 'Meteora initialize_customizable_permissionless_lb_pair', program: METEORA, data: VALID_SOLANA_INSTRUCTION_DATA.meteoraInitializeCustomizablePermissionlessLbPair, accounts: exactAccounts([POOL, 'reserve-x', BASE_MINT, WSOL], 14) },
  { name: 'Meteora initialize_lb_pair2', program: METEORA, data: VALID_SOLANA_INSTRUCTION_DATA.meteoraInitializeLbPair2, accounts: exactAccounts([POOL, 'reserve-x', BASE_MINT, WSOL], 16) },
  { name: 'Meteora initialize_customizable_permissionless_lb_pair2', program: METEORA, data: VALID_SOLANA_INSTRUCTION_DATA.meteoraInitializeCustomizablePermissionlessLbPair2, accounts: exactAccounts([POOL, 'reserve-x', BASE_MINT, WSOL], 17) },
  { name: 'Meteora initialize_permission_lb_pair', program: METEORA, data: VALID_SOLANA_INSTRUCTION_DATA.meteoraInitializePermissionLbPair, accounts: exactAccounts(['base-seed', POOL, 'reserve-x', BASE_MINT, WSOL], 18) },
  { name: 'Moonshot token_mint', program: MOONSHOT, data: VALID_SOLANA_INSTRUCTION_DATA.moonitTokenMint, accounts: exactAccounts(['sender', 'backend', POOL, BASE_MINT], 11) },
] as const).flatMap((schema) => [
  { ...schema, malformed: 'short', accounts: schema.accounts.slice(0, -1) },
  { ...schema, malformed: 'long', accounts: [...schema.accounts, 'unexpected-extra-account'] },
]);

const SCANNER_CONFIG = loadConfig({
  MODE: 'paper', PAPER_STARTING_SOL: '10', MAX_POSITION_SOL: '0.25', MAX_CONCURRENT_POSITIONS: '2', MAX_DAILY_LOSS_SOL: '0.5',
  MIN_LIQUIDITY_USD: '25000', MAX_LIQUIDITY_USD: '2000000', MIN_AGE_MINUTES: '3', MAX_AGE_MINUTES: '360',
  MIN_PRICE_CHANGE_M5_PERCENT: '8', MIN_VOLUME_M5_USD: '5000', STOP_LOSS_PERCENT: '15', TAKE_PROFIT_PERCENT: '30',
  TRAILING_STOP_PERCENT: '15', MAX_HOLD_MINUTES: '45', SIMULATED_SLIPPAGE_BPS: '150', SIMULATED_FEE_BPS: '100',
  SCAN_INTERVAL_SECONDS: '30', MAX_CYCLES: '0', STRICT_RISK_MODE: 'false', DATA_DIR: './data', SOL_PRICE_USD: '100',
});

const migrationLogs = [
  `Program ${PUMP_FUN} invoke [1]`,
  'Program log: Instruction: MigrateV2',
  'Program ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL invoke [2]',
  'Program log: Instruction: CreateIdempotent',
  `Program ${PUMP_SWAP_AMM} invoke [2]`,
  'Program log: Instruction: CreatePool',
];

function franktardTransaction(data = CREATE_POOL_DATA, accounts = exactAccounts([POOL, 'global', 'creator', BASE_MINT, WSOL], 18, 'pumpswap-role')) {
  return {
    blockTime: 1_785_329_073,
    meta: {
      err: null,
      logMessages: migrationLogs,
      innerInstructions: [{
        index: 4,
        instructions: [{ programId: PUMP_SWAP_AMM, data, accounts, stackHeight: 2 }],
      }],
    },
    transaction: {
      message: {
        // Deliberately unrelated/reordered: PumpSwap roles must come from instruction accounts.
        accountKeys: [{ pubkey: 'unrelated-3' }, { pubkey: BASE_MINT }, { pubkey: 'unrelated-1' }, { pubkey: POOL }],
        instructions: [
          { programId: PUMP_FUN, data: 'migrate-v2', accounts: ['creator'] },
          { programId: 'unrelated-program-1', data: '1', accounts: [] },
          { programId: 'unrelated-program-2', data: '1', accounts: [] },
          { programId: 'unrelated-program-3', data: '1', accounts: [] },
          { programId: 'unrelated-program-4', data: '1', accounts: [] },
        ],
      },
    },
  };
}

type Internals = {
  transactionRetryDelayMs: number;
  pendingSnapshots: Array<Pick<MarketSnapshot, 'pairId' | 'mint' | 'symbol' | 'source' | 'firstSeenAt' | 'pairCreatedAt' | 'discovery'>>;

  detectNewPool(program: string, logs: string[]): string | undefined;
  onMessage(event: MessageEvent): void;
  onNewPool(program: string | readonly string[], signature: string, receipt?: { receiptAt: string; slot?: number }): Promise<void>;
  fetchSnapshotsOnce(): Promise<MarketSnapshot[]>;
  rpcRequest(method: string, params: unknown[]): Promise<unknown>;
};

describe('PumpSwap transaction discovery', () => {
  let provider: SolanaRpcProvider | undefined;

  beforeEach(() => {
    globalThis.fetch = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ result: null }) }) as Response) as unknown as typeof fetch;
  });

  afterEach(() => {
    provider?.destroy();
    provider = undefined;
    globalThis.fetch = ORIGINAL_FETCH;
    vi.restoreAllMocks();
  });

  it('parses Franktard from the real inner-CPI shape independent of global account order', async () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as Internals;
    internal.rpcRequest = async () => franktardTransaction();

    await internal.onNewPool(PUMP_SWAP_AMM, 'franktard-create-signature', {
      receiptAt: '2026-07-29T12:44:33.125Z', slot: 357_123_456,
    });

    expect(internal.pendingSnapshots).toEqual([expect.objectContaining({
      pairId: POOL,
      mint: BASE_MINT,
      source: 'solana_rpc_ws_pumpswap',
      pairCreatedAt: '2026-07-29T12:44:33.000Z',
    })]);
    expect(internal.pendingSnapshots[0]).not.toHaveProperty('observedAt');
    expect(internal.pendingSnapshots[0]).not.toHaveProperty('priceUsd');
    expect(internal.pendingSnapshots[0]).toEqual(expect.objectContaining({
      firstSeenAt: '2026-07-29T12:44:33.125Z',
      discovery: {
        signature: 'franktard-create-signature', slot: 357_123_456, programId: PUMP_SWAP_AMM,
        instructionLocation: 'inner', instructionIndex: 0, parentInstructionIndex: 4,
        receiptAt: '2026-07-29T12:44:33.125Z',
      },
    }));
  });

  it('does not hydrate a program-scoped event without a positive init instruction log', async () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as Internals;
    internal.rpcRequest = vi.fn(async () => franktardTransaction());

    internal.onMessage({ data: JSON.stringify({
      method: 'logsNotification',
      params: {
        result: {
          context: { slot: 357_123_456 },
          value: {
            err: null,
            signature: 'missing-instruction-name',
            logs: [`Program ${PUMP_SWAP_AMM} invoke [2]`, 'Program log: instruction output unavailable'],
          },
        },
      },
    }) } as MessageEvent);

    await new Promise<void>((resolve) => setImmediate(resolve));
    expect(internal.pendingSnapshots).toEqual([]);
    expect(internal.rpcRequest).not.toHaveBeenCalled();
  });

  it('rejects an inner instruction group without a valid parent instruction index', async () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as Internals;
    const malformed = franktardTransaction();
    malformed.meta.innerInstructions = [{
      instructions: [{
        programId: PUMP_SWAP_AMM,
        data: CREATE_POOL_DATA,
        accounts: exactAccounts([POOL, 'global', 'creator', BASE_MINT, WSOL], 18, 'missing-parent-role'),
        stackHeight: 2,
      }],
    }] as typeof malformed.meta.innerInstructions;
    internal.rpcRequest = async () => malformed;

    await internal.onNewPool(PUMP_SWAP_AMM, 'missing-parent-index', {
      receiptAt: '2026-07-29T12:44:33.125Z', slot: 357_123_456,
    });

    expect(internal.pendingSnapshots).toEqual([]);
  });

  it('emits unpriced identity and never treats pool-state lamports as a reserve', async () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as Internals;

    internal.pendingSnapshots.push({
      pairId: 'unquoted-pool', mint: 'unquoted-mint', symbol: 'UNQ', source: 'solana_rpc_ws_raydium-amm',
      firstSeenAt: '2026-07-29T12:44:33.125Z', pairCreatedAt: '2026-07-29T12:44:33.000Z',
    });
    const snapshots = await internal.fetchSnapshotsOnce();

    expect(snapshots).toEqual([expect.objectContaining({
      pairId: 'unquoted-pool', mint: 'unquoted-mint', priceUsd: 0,
      firstSeenAt: '2026-07-29T12:44:33.125Z', pairCreatedAt: '2026-07-29T12:44:33.000Z',
    })]);
    expect(snapshots[0]).not.toHaveProperty('liquidityUsd');
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it('accepts PumpSwap and Meteora pools with WSOL in either official mint role', async () => {
    const cases = [
      {
        program: PUMP_SWAP_AMM, data: CREATE_POOL_DATA, pool: 'reverse-pumpswap-pool', mint: 'reverse-pumpswap-mint',
        accounts: exactAccounts(['reverse-pumpswap-pool', 'global', 'creator', WSOL, 'reverse-pumpswap-mint'], 18, 'reverse-pumpswap'),
      },
      {
        program: METEORA, data: VALID_SOLANA_INSTRUCTION_DATA.meteoraInitializeLbPair, pool: 'reverse-meteora-pool', mint: 'reverse-meteora-mint',
        accounts: exactAccounts(['reverse-meteora-pool', 'reserve-x', WSOL, 'reverse-meteora-mint'], 14, 'reverse-meteora'),
      },
    ];
    for (const item of cases) {
      provider?.destroy();
      provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
      const internal = provider as unknown as Internals;
      internal.rpcRequest = async () => ({
        blockTime: 1_785_329_073,
        meta: { err: null, logMessages: [`Program ${item.program} invoke [1]`] },
        transaction: { message: { accountKeys: [], instructions: [{ programId: item.program, data: item.data, accounts: item.accounts }] } },
      });
      await internal.onNewPool(item.program, `reverse-${item.program}`);
      expect(internal.pendingSnapshots).toEqual([expect.objectContaining({ pairId: item.pool, mint: item.mint })]);
    }
  });

  it('tries every exact parser for mixed-program transactions instead of discarding after the first heuristic', async () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as Internals;
    const pool = 'mixed-meteora-pool';
    const mint = 'mixed-meteora-mint';
    internal.rpcRequest = async () => ({
      blockTime: 1_785_329_073,
      meta: { err: null, logMessages: [`Program ${RAYDIUM_AMM} invoke [1]`, 'Program log: initialize', `Program ${METEORA} invoke [2]`] },
      transaction: { message: { accountKeys: [], instructions: [
        { programId: RAYDIUM_AMM, data: '11111111', accounts: [] },
        { programId: METEORA, data: VALID_SOLANA_INSTRUCTION_DATA.meteoraInitializeLbPair, accounts: exactAccounts([pool, 'reserve-x', mint, WSOL], 14, 'mixed-meteora') },
      ] } },
    });
    await internal.onNewPool([RAYDIUM_AMM, METEORA], 'mixed-program-signature');
    expect(internal.pendingSnapshots).toEqual([expect.objectContaining({ pairId: pool, mint, source: 'solana_rpc_ws_meteora' })]);
  });

  it('fails closed when any account reference in an exact-length layout is unresolved', async () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as Internals;
    const accounts: Array<string | number> = exactAccounts([POOL, 'global', 'creator', BASE_MINT, WSOL], 18, 'resolved');
    accounts[17] = 999;
    internal.rpcRequest = async () => ({
      blockTime: 1_785_329_073,
      meta: { err: null, logMessages: [`Program ${PUMP_SWAP_AMM} invoke [1]`] },
      transaction: { message: { accountKeys: [], instructions: [{ programId: PUMP_SWAP_AMM, data: CREATE_POOL_DATA, accounts }] } },
    });
    await internal.onNewPool(PUMP_SWAP_AMM, 'unresolved-account-reference');
    expect(internal.pendingSnapshots).toEqual([]);
  });

  it('fails closed instead of inventing pairCreatedAt when blockTime is missing', async () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as Internals;
    const transaction = franktardTransaction();
    delete (transaction as { blockTime?: number }).blockTime;
    internal.rpcRequest = async () => transaction;
    await internal.onNewPool(PUMP_SWAP_AMM, 'missing-block-time');
    expect(internal.pendingSnapshots).toEqual([]);
  });

  it('uses mixed migration logs to schedule only the positively triggered exact parser', () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as Internals;
    expect(internal.detectNewPool(PUMP_SWAP_AMM, migrationLogs)).toBe(PUMP_SWAP_AMM);
    expect(internal.detectNewPool(PUMP_FUN, migrationLogs)).toBeUndefined();

    const scheduled: Array<[readonly string[], string, { receiptAt: string; slot?: number }]> = [];
    (internal as unknown as { scheduleNewPool(programs: readonly string[], signature: string, receipt: { receiptAt: string; slot?: number }): void }).scheduleNewPool = (programs, signature, receipt) => scheduled.push([programs, signature, receipt]);
    internal.onMessage({ data: JSON.stringify({ method: 'logsNotification', params: { result: { context: { slot: 357_123_999 }, value: { err: null, signature: 'mixed-signature', logs: migrationLogs } } } }) } as MessageEvent);
    expect(scheduled).toEqual([[
      [PUMP_SWAP_AMM],
      'mixed-signature',
      { receiptAt: expect.any(String), slot: 357_123_999 },
    ]]);
  });

  it('parses Pump.fun create roles from the official instruction instead of global account positions', async () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as Internals;
    const pumpFunCreate = {
      blockTime: 1_785_339_374,
      meta: {
        err: null,
        logMessages: [`Program ${PUMP_FUN} invoke [2]`, 'Program log: Instruction: Create'],
        innerInstructions: [{ index: 1, instructions: [{
          programId: PUMP_FUN,
          // Official create discriminator [24,30,200,40,5,28,7,119].
          data: VALID_SOLANA_INSTRUCTION_DATA.pumpFunCreate,
          accounts: [PUMP_FUN_MINT, 'mint-authority', PUMP_FUN_CURVE, 'associated-curve', 'global', 'metadata-program', 'metadata', 'user', 'system', 'token', 'ata', 'rent', 'event', PUMP_FUN],
        }] }],
      },
      transaction: { message: {
        accountKeys: ['wrong-global-0', 'wrong-global-1', 'wrong-global-2'],
        instructions: [
          { programId: 'unrelated-program-0', data: '1', accounts: [] },
          { programId: 'unrelated-program-1', data: '1', accounts: [] },
        ],
      } },
    };
    internal.rpcRequest = async () => pumpFunCreate;
    await internal.onNewPool(PUMP_FUN, 'pump-fun-create');
    expect(internal.pendingSnapshots).toEqual([expect.objectContaining({
      pairId: PUMP_FUN_CURVE, mint: PUMP_FUN_MINT, source: 'solana_rpc_ws_pump-fun',
    })]);

    provider.destroy();
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const rawVersioned = provider as unknown as Internals;
    rawVersioned.rpcRequest = async () => ({
      blockTime: 1_785_339_374,
      meta: {
        err: null,
        logMessages: [`Program ${PUMP_FUN} invoke [2]`, 'Program log: Instruction: Create'],
        loadedAddresses: { writable: exactAccounts([PUMP_FUN_MINT, 'loaded-middle', PUMP_FUN_CURVE], 14, 'pump-fun-loaded'), readonly: [] },
        innerInstructions: [{ index: 0, instructions: [{
          programIdIndex: 0, data: VALID_SOLANA_INSTRUCTION_DATA.pumpFunCreate, accounts: Array.from({ length: 14 }, (_, index) => index + 1),
        }] }],
      },
      transaction: { message: {
        accountKeys: [PUMP_FUN],
        instructions: [{ programId: 'unrelated-program-0', data: '1', accounts: [] }],
      } },
    });
    await rawVersioned.onNewPool(PUMP_FUN, 'pump-fun-loaded-addresses');
    expect(rawVersioned.pendingSnapshots).toEqual([expect.objectContaining({
      pairId: PUMP_FUN_CURVE, mint: PUMP_FUN_MINT,
    })]);
  });

  it('parses Raydium AMM Initialize2 pool and non-WSOL mint from official instruction roles', async () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as Internals;
    const pool = 'RaydiumAmmPool111111111111111111111111111111';
    const mint = 'RaydiumAmmMint111111111111111111111111111111';
    internal.rpcRequest = async () => ({
      blockTime: 1_785_339_374,
      meta: { err: null, logMessages: [`Program ${RAYDIUM_AMM} invoke [1]`, 'Program log: initialize2'] },
      transaction: { message: {
        accountKeys: ['wrong-0', 'wrong-1', 'wrong-2', 'wrong-3', 'wrong-4'],
        instructions: [{
          programId: RAYDIUM_AMM,
          // Official Initialize2: opcode 1 + nonce/u64/u64/u64 (26 bytes).
          data: '2n1XR4oJkmBdJMxhBGQGb96gQ88xUzxLFyH',
          accounts: ['token', 'ata', 'system', 'rent', pool, 'authority', 'open-orders', 'lp-mint', mint, WSOL, 'coin-vault', 'pc-vault', 'target-orders', 'config', 'fee', 'market-program', 'market', 'wallet', 'user-coin', 'user-pc', 'user-lp'],
        }],
      } },
    });
    await internal.onNewPool(RAYDIUM_AMM, 'raydium-amm-init2');
    expect(internal.pendingSnapshots).toEqual([expect.objectContaining({
      pairId: pool, mint, source: 'solana_rpc_ws_raydium-amm',
    })]);

    provider.destroy();
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const shortLayout = provider as unknown as Internals;
    shortLayout.rpcRequest = async () => ({
      blockTime: 1_785_339_374,
      meta: { err: null, logMessages: [`Program ${RAYDIUM_AMM} invoke [1]`, 'Program log: initialize2'] },
      transaction: { message: { accountKeys: [], instructions: [{
        programId: RAYDIUM_AMM,
        data: '2n1XR4oJkmBdJMxhBGQGb96gQ88xUzxLFyH',
        // Processor-supported 19-account variant omits open_orders and market_program.
        accounts: ['token', 'ata', 'system', 'rent', `${pool}-19`, 'authority', 'lp-mint', `${mint}-19`, WSOL, 'coin-vault', 'pc-vault', 'target-orders', 'config', 'fee', 'market', 'wallet', 'user-coin', 'user-pc', 'user-lp'],
      }] } },
    });
    await shortLayout.onNewPool(RAYDIUM_AMM, 'raydium-amm-init2-19');
    expect(shortLayout.pendingSnapshots).toEqual([expect.objectContaining({
      pairId: `${pool}-19`, mint: `${mint}-19`, source: 'solana_rpc_ws_raydium-amm',
    })]);
  });

  it('parses Raydium CPMM initialize pool and non-WSOL mint from official IDL roles', async () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as Internals;
    const pool = 'RaydiumCpmmPool11111111111111111111111111111';
    const mint = 'RaydiumCpmmMint11111111111111111111111111111';
    internal.rpcRequest = async () => ({
      blockTime: 1_785_339_374,
      meta: { err: null, logMessages: [`Program ${RAYDIUM_CPMM} invoke [1]`, 'Program log: Instruction: Initialize'] },
      transaction: { message: {
        accountKeys: ['wrong-0', 'wrong-1', 'wrong-2', 'wrong-3', 'wrong-4'],
        instructions: [{
          programId: RAYDIUM_CPMM, data: VALID_SOLANA_INSTRUCTION_DATA.raydiumCpmmInitialize,
          accounts: ['creator', 'config', 'authority', pool, mint, WSOL, 'lp-mint', 'creator-0', 'creator-1', 'creator-lp', 'vault-0', 'vault-1', 'fee', 'observation', 'token', 'token-0-program', 'token-1-program', 'ata', 'system', 'rent'],
        }],
      } },
    });
    await internal.onNewPool(RAYDIUM_CPMM, 'raydium-cpmm-initialize');
    expect(internal.pendingSnapshots).toEqual([expect.objectContaining({
      pairId: pool, mint, source: 'solana_rpc_ws_raydium-cpmm',
    })]);
  });

  it.each([
    ['initialize_lb_pair', VALID_SOLANA_INSTRUCTION_DATA.meteoraInitializeLbPair, exactAccounts(['POOL', 'reserve-x', 'MINT', WSOL], 14, 'meteora-lb')],
    ['initialize_customizable_permissionless_lb_pair', VALID_SOLANA_INSTRUCTION_DATA.meteoraInitializeCustomizablePermissionlessLbPair, exactAccounts(['POOL', 'reserve-x', 'MINT', WSOL], 14, 'meteora-custom')],
    ['initialize_lb_pair2', VALID_SOLANA_INSTRUCTION_DATA.meteoraInitializeLbPair2, exactAccounts(['POOL', 'reserve-x', 'MINT', WSOL], 16, 'meteora-lb2')],
    ['initialize_customizable_permissionless_lb_pair2', VALID_SOLANA_INSTRUCTION_DATA.meteoraInitializeCustomizablePermissionlessLbPair2, exactAccounts(['POOL', 'reserve-x', 'MINT', WSOL], 17, 'meteora-custom2')],
    ['initialize_permission_lb_pair', VALID_SOLANA_INSTRUCTION_DATA.meteoraInitializePermissionLbPair, exactAccounts(['base-seed', 'POOL', 'reserve-x', 'MINT', WSOL], 18, 'meteora-permission')],
  ])('parses Meteora %s pool and mint from official IDL roles', async (name, data, roleAccounts) => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as Internals;
    const pool = `Meteora-${name}-Pool`;
    const mint = `Meteora-${name}-Mint`;
    internal.rpcRequest = async () => ({
      blockTime: 1_785_339_374,
      meta: { err: null, logMessages: [`Program ${METEORA} invoke [1]`, `Program log: Instruction: ${name}`] },
      transaction: { message: {
        accountKeys: ['wrong-0', 'wrong-1', 'wrong-2', 'wrong-3', 'wrong-4'],
        instructions: [{
          programId: METEORA, data,
          accounts: roleAccounts.map((account) => account === 'POOL' ? pool : account === 'MINT' ? mint : account),
        }],
      } },
    });
    await internal.onNewPool(METEORA, `meteora-${name}`);
    expect(internal.pendingSnapshots).toEqual([expect.objectContaining({
      pairId: pool, mint, source: 'solana_rpc_ws_meteora',
    })]);
  });

  it('parses a confirmed Moonshot TokenMint fixture from official curve/mint roles', async () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as Internals;
    const curve = 'C5SoPpgfC7E6tHEsA6Vr8SZT2px4GrYPQLHuxRSdiG9u';
    const mint = 'BxxUDXABruNcXtetciuaQCo24QcEf1LxytpPkAjwmoon';
    internal.rpcRequest = async () => ({
      // Confirmed mainnet signature 65ZxBBgNPSJHtznPhJVWRFuAayCC2fDFAVTyruKRcoFRmX4iiqkAwxMx9prmySVQobMVhGZViFjtvH9trNRSdeJ3.
      blockTime: 1_785_215_934,
      meta: { err: null, logMessages: [`Program ${MOONSHOT} invoke [1]`, 'Program log: Instruction: TokenMint'] },
      transaction: { message: {
        accountKeys: ['wrong-0', 'wrong-1', 'wrong-2', 'wrong-3', 'wrong-4'],
        instructions: [{
          programId: MOONSHOT,
          data: 'YaoFtQPF2iFQ4MwimTv8DPw1FFMemdYuJsMDDwH1YxXVNfDa8gFzgY3QfnoNyHWaZGG1rVksJP1aVLtMQ9pGjC66WNgbe4z8tu4ZXbzV5sMrbrpvkp4D55KyECZ34rW69xfYd47h1',
          accounts: ['GGsmbrRoYAjJm83JZNHSXDkhGystGUkuRchViqhdZEyj', 'Cb8Fnhp95f9dLxB3sYkNCbN3Mjxuc3v2uQZ7uVeqvNGB', curve, mint, '6wRv1ch8VuV1PKovad5oQQgKG1oHnV1j7CpdtEeyoYUb', 'HbPLHXTzkGhZAos6EjNzAJh9EwJjm4JoVdBsSJ3KRgHj', '36Eru7v11oU5Pfrojyn5oY3nETA1a1iqsw2WUu6afkM9', 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA', 'ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL', 'metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s', '11111111111111111111111111111111'],
        }],
      } },
    });
    await internal.onNewPool(MOONSHOT, 'moonshot-real-token-mint');
    expect(internal.pendingSnapshots).toEqual([expect.objectContaining({
      pairId: curve, mint, source: 'solana_rpc_ws_moonshot',
      pairCreatedAt: '2026-07-28T05:18:54.000Z',
    })]);
  });

  it.each([
    ['wrong discriminator', franktardTransaction('11111111')],
    ['short account list', franktardTransaction(CREATE_POOL_DATA, [POOL, 'global', 'creator'])],
    ['failed transaction', { ...franktardTransaction(), meta: { ...franktardTransaction().meta, err: { InstructionError: [4, 'Custom'] } } }],
  ])('fails closed for %s', async (_label, transaction) => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as Internals;
    internal.rpcRequest = async () => transaction;
    await internal.onNewPool(PUMP_SWAP_AMM, `bad-${_label}`);
    expect(internal.pendingSnapshots).toEqual([]);
  });

  it.each(MALFORMED_LAYOUT_CASES)('fails closed for $name $malformed account layout', async ({ program, data, accounts }) => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as Internals;
    internal.rpcRequest = async () => ({
      blockTime: 1_785_339_374,
      meta: { err: null, logMessages: [`Program ${program} invoke [1]`] },
      transaction: { message: {
        accountKeys: [],
        instructions: [{ programId: program, data, accounts }],
      } },
    });
    await internal.onNewPool(program, `malformed-${program}-${accounts.length}`);
    expect(internal.pendingSnapshots).toEqual([]);
  });

  it('fails closed when transaction success metadata is missing', async () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as Internals;
    const transaction = franktardTransaction();
    transaction.transaction.message.instructions = transaction.meta.innerInstructions[0].instructions;
    const withoutMeta = { blockTime: transaction.blockTime, transaction: transaction.transaction };
    internal.rpcRequest = async () => withoutMeta;
    await internal.onNewPool(PUMP_SWAP_AMM, 'missing-meta');
    expect(internal.pendingSnapshots).toEqual([]);
  });

  it('does not burst-retry a malformed non-null confirmed transaction', async () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as Internals;
    let calls = 0;
    internal.rpcRequest = async () => { calls += 1; return {}; };
    await internal.onNewPool(PUMP_SWAP_AMM, 'malformed-non-null');
    expect(calls).toBe(1);
    expect(internal.pendingSnapshots).toEqual([]);
  });

  it('supports both inner and top-level official create_pool instructions', async () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as Internals;
    const topLevel = franktardTransaction();
    topLevel.meta.innerInstructions = [];
    topLevel.transaction.message.instructions = [{
      programId: PUMP_SWAP_AMM,
      data: CREATE_POOL_DATA,
      accounts: exactAccounts([POOL, 'global', 'creator', BASE_MINT, WSOL], 18, 'pumpswap-top-level'),
    }];
    internal.rpcRequest = async () => topLevel;
    await internal.onNewPool(PUMP_SWAP_AMM, 'top-level-create');
    expect(internal.pendingSnapshots).toEqual([expect.objectContaining({ pairId: POOL, mint: BASE_MINT })]);
  });

  it('retries a temporarily unavailable confirmed transaction with a hard bound', async () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as Internals;
    internal.transactionRetryDelayMs = 0;
    let calls = 0;
    internal.rpcRequest = async () => (++calls === 1 ? null : franktardTransaction());
    await internal.onNewPool(PUMP_SWAP_AMM, 'temporarily-null');
    expect(calls).toBe(2);
    expect(internal.pendingSnapshots).toEqual([expect.objectContaining({ pairId: POOL })]);
  });

  it('stops after three null consistency-window responses', async () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as Internals;
    internal.transactionRetryDelayMs = 0;
    let calls = 0;
    internal.rpcRequest = async () => { calls += 1; return null; };
    await internal.onNewPool(PUMP_SWAP_AMM, 'always-null');
    expect(calls).toBe(3);
    expect(internal.pendingSnapshots).toEqual([]);
  });

  it('never polls HTTP for new signatures when the WebSocket event buffer is empty', async () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as Internals;
    const rpcRequest = vi.fn(async () => null);
    internal.rpcRequest = rpcRequest;

    await expect(internal.fetchSnapshotsOnce()).resolves.toEqual([]);

    expect(rpcRequest).not.toHaveBeenCalled();
  });

  it('preserves provider first-seen while draining discovery identity', async () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as Internals;
    internal.pendingSnapshots.push({
      pairId: POOL, mint: BASE_MINT, symbol: 'FRANK', source: 'solana_rpc_ws_pumpswap',
      firstSeenAt: '2026-07-29T12:44:34.000Z', pairCreatedAt: '2026-07-29T12:44:33.000Z',
    });
    await expect(internal.fetchSnapshotsOnce()).resolves.toEqual([expect.objectContaining({
      pairId: POOL, firstSeenAt: '2026-07-29T12:44:34.000Z',
      observedAt: expect.any(String), pairCreatedAt: '2026-07-29T12:44:33.000Z', priceUsd: 0,
    })]);
  });

  it('carries exact PumpSwap provenance through discovery, exact quote and Scanner', async () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as Internals;
    internal.rpcRequest = async () => franktardTransaction();
    await internal.onNewPool(PUMP_SWAP_AMM, 'full-production-path');
    const snapshots = (await internal.fetchSnapshotsOnce()).map((snapshot) => ({
      ...snapshot, priceUsd: 1, liquidityUsd: 50_000, volumeM5Usd: 15_000,
      priceChangeM5Percent: 20, buysM5: 30, sellsM5: 10,
    }));
    expect(snapshots).toEqual([expect.objectContaining({
      pairId: POOL, mint: BASE_MINT, pairCreatedAt: '2026-07-29T12:44:33.000Z', firstSeenAt: expect.any(String),
    })]);
    const evaluatedAt = snapshots[0]!.observedAt;
    const result = await new Scanner(
      { fetchSnapshots: async () => snapshots }, { ...SCANNER_CONFIG, maxAgeMinutes: Number.MAX_SAFE_INTEGER }, createPortfolio(SCANNER_CONFIG, evaluatedAt), () => new Date(evaluatedAt),
    ).runOnce();
    expect(result.decisions).toEqual([expect.objectContaining({
      type: 'paper_entry', pairId: POOL, mint: BASE_MINT,
      pairCreatedAt: '2026-07-29T12:44:33.000Z', firstSeenAt: snapshots[0]!.firstSeenAt,
    })]);
  });

  it.each([
    [PUMP_FUN, 'pump-fun', VALID_SOLANA_INSTRUCTION_DATA.pumpFunCreate, (pool: string, mint: string) => exactAccounts([mint, 'authority', pool], 14, 'pump-fun-full'), 'Create'],
    [RAYDIUM_AMM, 'raydium-amm', '2n1XR4oJkmBdJMxhBGQGb96gQ88xUzxLFyH', (pool: string, mint: string) => ['token', 'ata', 'system', 'rent', pool, 'authority', 'open-orders', 'lp-mint', mint, WSOL, 'coin-vault', 'pc-vault', 'target-orders', 'config', 'fee', 'market-program', 'market', 'wallet', 'user-coin', 'user-pc', 'user-lp'], 'initialize2'],
    [RAYDIUM_CPMM, 'raydium-cpmm', VALID_SOLANA_INSTRUCTION_DATA.raydiumCpmmInitialize, (pool: string, mint: string) => exactAccounts(['creator', 'config', 'authority', pool, mint, WSOL], 20, 'cpmm-full'), 'Initialize'],
    [METEORA, 'meteora', VALID_SOLANA_INSTRUCTION_DATA.meteoraInitializeLbPair, (pool: string, mint: string) => exactAccounts([pool, 'reserve-x', mint, WSOL], 14, 'meteora-full'), 'InitializeLbPair'],
    [MOONSHOT, 'moonshot', VALID_SOLANA_INSTRUCTION_DATA.moonitTokenMint, (pool: string, mint: string) => exactAccounts(['deployer', 'config', pool, mint], 11, 'moonshot-full'), 'TokenMint'],
  ])(
    'carries exact %s identity through discovery, exact quote and Scanner',
    async (program, dexName, data, accountsFor, instructionName) => {
      provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
      const internal = provider as unknown as Internals;
      const pool = `${dexName}-full-pool`;
      const mint = `${dexName}-full-mint`;
      internal.rpcRequest = async () => ({
        blockTime: 1_785_329_073,
        meta: { err: null, logMessages: [`Program ${program} invoke [1]`, `Program log: Instruction: ${instructionName}`] },
        transaction: { message: {
          accountKeys: ['wrong-global-0', 'wrong-global-1', 'wrong-global-mint', 'wrong-global-3', 'wrong-global-pool'],
          instructions: [{ programId: program, data, accounts: accountsFor(pool, mint) }],
        } },
      });
      await internal.onNewPool(program, `${dexName}-full-path`);
      const snapshots = (await internal.fetchSnapshotsOnce()).map((snapshot) => ({
        ...snapshot, priceUsd: 1, liquidityUsd: 50_000, volumeM5Usd: 15_000,
        priceChangeM5Percent: 20, buysM5: 30, sellsM5: 10,
      }));
      expect(snapshots).toEqual([expect.objectContaining({
        pairId: pool, mint, pairCreatedAt: '2026-07-29T12:44:33.000Z', firstSeenAt: expect.any(String),
      })]);
      const evaluatedAt = snapshots[0]!.observedAt;
      const result = await new Scanner(
        { fetchSnapshots: async () => snapshots }, { ...SCANNER_CONFIG, maxAgeMinutes: Number.MAX_SAFE_INTEGER }, createPortfolio(SCANNER_CONFIG, evaluatedAt), () => new Date(evaluatedAt),
      ).runOnce();
      expect(result.decisions).toEqual([expect.objectContaining({
        type: 'paper_entry', pairId: pool, mint,
        pairCreatedAt: '2026-07-29T12:44:33.000Z', firstSeenAt: snapshots[0]!.firstSeenAt,
      })]);
    },
  );

  it.each([
    [METEORA, 'meteora', VALID_SOLANA_INSTRUCTION_DATA.meteoraInitializeLbPair, (pool: string, mint: string) => exactAccounts([pool, 'reserve-x', mint, WSOL], 14, 'meteora-raw'), 'InitializeLbPair'],
    [MOONSHOT, 'moonshot', VALID_SOLANA_INSTRUCTION_DATA.moonitTokenMint, (pool: string, mint: string) => exactAccounts(['deployer', 'config', pool, mint], 11, 'moonshot-raw'), 'TokenMint'],
  ])(
    'uses the raw on-chain pool address for exact %s enrichment', async (program, dexName, data, accountsFor, instructionName) => {
      provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
      const internal = provider as unknown as Internals;
      const genericPool = `${dexName}-raw-pool`;
      const genericMint = `${dexName}-mint`;
      internal.rpcRequest = async () => ({
        blockTime: 1_785_329_073,
        meta: { err: null, logMessages: [`Program ${program} invoke [1]`, `Program log: Instruction: ${instructionName}`] },
        transaction: { message: {
          accountKeys: ['a', 'b', 'wrong-global-mint', 'x', 'wrong-global-pool'],
          instructions: [{ programId: program, data, accounts: accountsFor(genericPool, genericMint) }],
        } },
      });
      await internal.onNewPool(program, `${dexName}-signature`);
      expect(internal.pendingSnapshots).toEqual([expect.objectContaining({ pairId: genericPool, mint: genericMint })]);
    },
  );

  it('never treats PumpSwap pool-account rent as SOL liquidity while draining identity', async () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as Internals;

    internal.pendingSnapshots.push({
      pairId: POOL, mint: BASE_MINT, symbol: 'FRANK', source: 'solana_rpc_ws_pumpswap',
      pairCreatedAt: '2026-07-29T12:44:33.000Z',
    });

    await expect(internal.fetchSnapshotsOnce()).resolves.toEqual([
      expect.objectContaining({ pairId: POOL, mint: BASE_MINT, priceUsd: 0 }),
    ]);
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });
});
