import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SolanaRpcProvider } from '../src/providers/solana-rpc.js';
import type { MarketSnapshot } from '../src/scoring.js';
import { VALID_SOLANA_INSTRUCTION_DATA } from './solana-layout-fixtures.js';

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  readonly sent: string[] = [];
  closeCalls = 0;
  private readonly listeners = new Map<string, EventListener[]>();

  constructor(_url?: string) {
    MockWebSocket.instances.push(this);
  }

  static reset(): void {
    MockWebSocket.instances = [];
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
    if (typeof listener !== 'function') return;
    const listeners = this.listeners.get(type) ?? [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type: string, listener: EventListenerOrEventListenerObject) {
    if (typeof listener !== 'function') return;
    this.listeners.set(type, (this.listeners.get(type) ?? []).filter((candidate) => candidate !== listener));
  }

  send(payload: string) { this.sent.push(payload); }

  emit(type: string, event: Event = new Event(type)): void {
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }

  close() {
    this.closeCalls += 1;
    this.emit('close');
  }
}
(globalThis as Record<string, unknown>).WebSocket = MockWebSocket;

const ORIGINAL_FETCH = globalThis.fetch;
const PUMP_SWAP_AMM = 'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA';
const DEX_PROGRAMS = [
  '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8',
  '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P',
  PUMP_SWAP_AMM,
  'CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C',
  'LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo',
  'MoonCVVNZFSYkqNXP6bxHLPL6QQJiMagDL3qcqUQTrG',
];

type PendingPoolSnapshot = Pick<MarketSnapshot, 'pairId' | 'mint' | 'symbol' | 'source' | 'firstSeenAt' | 'pairCreatedAt' | 'discovery'>;

function snapshot(index: number): PendingPoolSnapshot {
  return {
    pairId: `pair-${index}`,
    mint: `mint-${index}`,
    symbol: `M${index}`,
    source: 'test',
    pairCreatedAt: new Date().toISOString(),
  };
}

type ProviderInternals = {
  ws: WebSocket | null;
  requestId: number;
  subscribed: boolean;
  pendingSnapshots: PendingPoolSnapshot[];
  poolRequestsInFlight: Set<string>;
  whaleRequestsInFlight: Set<string>;
  transactionRetryDelayMs: number;
  enqueuePending(snapshot: PendingPoolSnapshot): void;
  scheduleNewPool(program: string, signature: string): void;
  detectNewPool(program: string, logs: string[]): string | undefined;
  onOpen(): void;
  onMessage(event: MessageEvent): void;
  drainDiagnostics(): string[];
  scheduleReconnect(): void;
  connect(): void;
  detectWhaleTx(signature: string, logs: string[]): void;
  onNewPool(program: string, signature: string): Promise<void>;
  rpcRequest(method: string, params: unknown[]): Promise<unknown>;
  fetchSnapshotsOnce(): Promise<MarketSnapshot[]>;

  fetchWithTimeout(url: string, init?: RequestInit): Promise<Response>;
};

describe('SolanaRpcProvider 24x7 bounds', () => {
  let provider: SolanaRpcProvider | undefined;

  beforeEach(() => {
    MockWebSocket.reset();
    globalThis.fetch = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ result: null }) }) as Response) as unknown as typeof fetch;
  });

  afterEach(() => {
    provider?.destroy();
    provider = undefined;
    globalThis.fetch = ORIGINAL_FETCH;
    vi.useRealTimers();
  });

  it('hard-caps the pending discovery queue with FIFO eviction', () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as ProviderInternals;
    for (let index = 0; index < 700; index += 1) {
      internal.enqueuePending(snapshot(index));
    }
    expect(internal.pendingSnapshots).toHaveLength(500);
    expect(internal.pendingSnapshots[0]?.pairId).toBe('pair-200');
  });

  it('hard-caps asynchronous pool and whale RPC work', () => {
    provider = new SolanaRpcProvider(['ConfiguredWhale111111111111111111111111111111'], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as ProviderInternals;
    internal.onNewPool = async () => await new Promise<void>(() => {});
    internal.rpcRequest = async () => await new Promise<unknown>(() => {});
    for (let index = 0; index < 40; index += 1) {
      internal.scheduleNewPool('program', `pool-signature-${index}`);
      internal.detectWhaleTx(`whale-signature-${index}`, ['Program 675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8 invoke']);
    }
    expect(internal.poolRequestsInFlight.size).toBe(16);
    expect(internal.whaleRequestsInFlight.size).toBe(16);
  });

  it('detects and parses only the official PumpSwap create_pool instruction', async () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as ProviderInternals;
    const createLogs = [
      `Program ${PUMP_SWAP_AMM} invoke [1]`,
      'Program log: Instruction: CreatePool',
    ];
    expect(internal.detectNewPool(PUMP_SWAP_AMM, createLogs)).toBe(PUMP_SWAP_AMM);

    internal.rpcRequest = async () => ({
      blockTime: 1_785_329_073,
      meta: { err: null, logMessages: createLogs },
      transaction: {
        message: {
          accountKeys: [],
          instructions: [{
            programId: PUMP_SWAP_AMM,
            // Official create_pool discriminator [233,146,209,142,207,104,64,188].
            data: VALID_SOLANA_INSTRUCTION_DATA.pumpSwapCreatePool,
            accounts: [
              'pump-swap-pool', 'global-config', 'creator', 'base-mint', 'So11111111111111111111111111111111111111112', 'lp-mint',
              'user-base', 'user-quote', 'user-pool', 'pool-base', 'pool-quote', 'system',
              'token-2022', 'base-token-program', 'quote-token-program', 'associated-token',
              'event-authority', PUMP_SWAP_AMM,
            ],
          }],
        },
      },
    });

    await internal.onNewPool(PUMP_SWAP_AMM, 'pump-swap-signature');

    expect(internal.pendingSnapshots).toEqual([
      expect.objectContaining({
        pairId: 'pump-swap-pool',
        mint: 'base-mint',
        source: 'solana_rpc_ws_pumpswap',
        pairCreatedAt: '2026-07-29T12:44:33.000Z',
      }),
    ]);
  });

  it('does not hydrate an ordinary program-scoped swap notification', async () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as ProviderInternals;
    internal.transactionRetryDelayMs = 0;
    const rpcRequest = vi.fn(async () => null);
    internal.rpcRequest = rpcRequest;

    internal.onMessage({
      data: JSON.stringify({
        jsonrpc: '2.0',
        method: 'logsNotification',
        params: {
          result: {
            context: { slot: 123 },
            value: {
              err: null,
              signature: 'ordinary-pump-swap-signature',
              logs: [
                `Program ${PUMP_SWAP_AMM} invoke [1]`,
                'Program log: Instruction: Buy',
                `Program ${PUMP_SWAP_AMM} success`,
              ],
            },
          },
        },
      }),
    } as MessageEvent);
    await new Promise<void>((resolve) => setImmediate(resolve));

    expect(rpcRequest).not.toHaveBeenCalled();
  });

  it('subscribes exactly once to each of the six DEX programs even when whale wallets are configured', () => {
    provider = new SolanaRpcProvider(['whale-wallet-a', 'whale-wallet-b'], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as ProviderInternals;
    const ws = new MockWebSocket();
    internal.ws = ws as unknown as WebSocket;
    internal.onOpen();
    const subscriptions = ws.sent.map((payload) => JSON.parse(payload) as { method?: string; params?: Array<{ mentions?: string[] }> });
    expect(subscriptions).toHaveLength(DEX_PROGRAMS.length);
    expect(subscriptions.every((subscription) => subscription.method === 'logsSubscribe')).toBe(true);
    expect(subscriptions.map((subscription) => subscription.params?.[0]?.mentions?.[0])).toEqual(DEX_PROGRAMS);
    expect(JSON.stringify(subscriptions)).not.toContain('675kTow9M1f3qS67Q9AUbhgT4dBCHBBWkw6SnS7m24bf');
  });

  it('becomes subscribed only after every WebSocket subscription is acknowledged', () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as ProviderInternals;
    const ws = new MockWebSocket();
    internal.ws = ws as unknown as WebSocket;

    internal.onOpen();
    expect(internal.subscribed).toBe(false);

    const requestIds = ws.sent.map((payload) => (JSON.parse(payload) as { id: number }).id);
    requestIds.forEach((id, index) => {
      internal.onMessage({ data: JSON.stringify({ jsonrpc: '2.0', id, result: 1_000 + index }) } as MessageEvent);
      expect(internal.subscribed).toBe(index === requestIds.length - 1);
    });
  });

  it('fails closed and reconnects when the six subscription acknowledgements miss their deadline', async () => {
    vi.useFakeTimers();
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as ProviderInternals;
    const socket = MockWebSocket.instances[0]!;

    socket.emit('open');
    expect(internal.subscribed).toBe(false);
    await vi.advanceTimersByTimeAsync(10_000);

    expect(socket.closeCalls).toBe(1);
    expect(internal.drainDiagnostics()).toEqual([
      'discovery: solana_ws: subscription_timeout pending=6',
    ]);
  });

  it('reports a rejected Helius subscription as degraded instead of silently returning no pools', () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as ProviderInternals;
    const ws = new MockWebSocket();
    internal.ws = ws as unknown as WebSocket;
    internal.onOpen();
    const requestId = (JSON.parse(ws.sent[0]!) as { id: number }).id;

    internal.onMessage({
      data: JSON.stringify({ jsonrpc: '2.0', id: requestId, error: { code: -32005, message: 'credit limit exceeded' } }),
    } as MessageEvent);

    expect(internal.subscribed).toBe(false);
    expect(internal.drainDiagnostics()).toEqual([
      'discovery: solana_ws: subscription_rejected code=-32005 message=credit limit exceeded',
    ]);
  });

  it('uses bounded exponential backoff instead of a reconnect storm', async () => {
    vi.useFakeTimers();
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as ProviderInternals;
    internal.connect = vi.fn();

    const expectedDelays = [1_000, 2_000, 4_000, 8_000, 16_000, 32_000, 60_000, 60_000];
    for (const [index, delayMs] of expectedDelays.entries()) {
      internal.scheduleReconnect();
      await vi.advanceTimersByTimeAsync(delayMs - 1);
      expect(internal.connect).toHaveBeenCalledTimes(index);
      await vi.advanceTimersByTimeAsync(1);
      expect(internal.connect).toHaveBeenCalledTimes(index + 1);
    }
  });

  it('invalidates an erroring socket generation and ignores its stale close after reconnect', async () => {
    vi.useFakeTimers();
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as ProviderInternals;
    const first = MockWebSocket.instances[0]!;

    first.emit('error');
    expect(first.closeCalls).toBe(1);

    await vi.advanceTimersByTimeAsync(1_000);
    const second = MockWebSocket.instances[1]!;
    expect(second).toBeDefined();
    expect(internal.ws).toBe(second);

    first.emit('close');
    expect(internal.ws).toBe(second);
    await vi.advanceTimersByTimeAsync(2_000);
    expect(MockWebSocket.instances).toHaveLength(2);
  });

  it('does not amplify 10,000 ordinary successful swap logs into transaction hydration', async () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as ProviderInternals;
    const rpcRequest = vi.fn(async () => null);
    internal.rpcRequest = rpcRequest;

    for (let index = 0; index < 10_000; index += 1) {
      internal.onMessage({
        data: JSON.stringify({
          method: 'logsNotification',
          params: {
            result: {
              context: { slot: 357_000_000 + index },
              value: {
                err: null,
                signature: `ordinary-swap-${index}`,
                logs: [`Program ${PUMP_SWAP_AMM} invoke [1]`, 'Program log: Instruction: Buy'],
              },
            },
          },
        }),
      } as MessageEvent);
    }

    await new Promise<void>((resolve) => setImmediate(resolve));
    expect(rpcRequest).not.toHaveBeenCalled();
  });

  it('shares one transaction fetch across pool and whale consumers and immediate duplicate notifications', async () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as ProviderInternals;
    const rpcRequest = vi.fn(async () => ({
      blockTime: 1_785_329_073,
      meta: { err: null, preBalances: [1], postBalances: [1], preTokenBalances: [] },
      transaction: { message: { accountKeys: [], instructions: [] } },
    }));
    internal.rpcRequest = rpcRequest;
    const notification = {
      data: JSON.stringify({
        method: 'logsNotification',
        params: {
          result: {
            context: { slot: 357_123_999 },
            value: {
              err: null,
              signature: 'shared-signature',
              logs: [`Program ${PUMP_SWAP_AMM} invoke [1]`, 'Program log: Instruction: CreatePool'],
            },
          },
        },
      }),
    } as MessageEvent;

    internal.onMessage(notification);
    await vi.waitFor(() => expect(rpcRequest).toHaveBeenCalledTimes(1));
    internal.onMessage(notification);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(rpcRequest).toHaveBeenCalledTimes(1);
  });

  it('attributes whale interest only to a configured wallet with a threshold SOL spend and positive token receipt', async () => {
    const whale = 'ConfiguredWhale111111111111111111111111111111';
    provider = new SolanaRpcProvider([whale], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as ProviderInternals;
    internal.rpcRequest = async () => ({
      meta: {
        err: null,
        preBalances: [10_000_000_000, 0, 0],
        postBalances: [4_000_000_000, 6_000_000_000, 0],
        preTokenBalances: [{ accountIndex: 2, mint: 'bought-mint', owner: whale, uiTokenAmount: { amount: '0', decimals: 0, uiAmount: 0 } }],
        postTokenBalances: [{ accountIndex: 2, mint: 'bought-mint', owner: whale, uiTokenAmount: { amount: '100', decimals: 0, uiAmount: 100 } }],
      },
      transaction: { message: { accountKeys: [{ pubkey: whale }, { pubkey: 'counterparty' }, { pubkey: 'whale-token-account' }], instructions: [] } },
    });

    internal.detectWhaleTx('exact-whale-buy', [`Program ${PUMP_SWAP_AMM} invoke [1]`]);
    await vi.waitFor(() => expect(provider.whaleActivity.has('bought-mint')).toBe(true));
  });

  it('rejects failed transactions, unconfigured owners, and notifications when no whale wallets are configured', async () => {
    const configured = 'ConfiguredWhale111111111111111111111111111111';
    const other = 'OtherOwner111111111111111111111111111111111';
    provider = new SolanaRpcProvider([configured], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as ProviderInternals;
    internal.rpcRequest = async () => ({
      meta: {
        err: { InstructionError: [0, 'Custom'] },
        preBalances: [10_000_000_000, 10_000_000_000],
        postBalances: [9_900_000_000, 3_000_000_000],
        preTokenBalances: [{ accountIndex: 2, mint: 'wrong-mint', owner: other, uiTokenAmount: { amount: '0', uiAmount: 0 } }],
        postTokenBalances: [{ accountIndex: 2, mint: 'wrong-mint', owner: other, uiTokenAmount: { amount: '100', uiAmount: 100 } }],
      },
      transaction: { message: { accountKeys: [{ pubkey: configured }, { pubkey: other }], instructions: [] } },
    });
    internal.detectWhaleTx('failed-or-wrong-owner', [`Program ${PUMP_SWAP_AMM} invoke [1]`]);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(provider.whaleActivity.size).toBe(0);

    provider.destroy();
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const noWalletInternal = provider as unknown as ProviderInternals;
    const rpcRequest = vi.fn(async () => null);
    noWalletInternal.rpcRequest = rpcRequest;
    noWalletInternal.detectWhaleTx('no-wallet-configured', [`Program ${PUMP_SWAP_AMM} invoke [1]`]);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(rpcRequest).not.toHaveBeenCalled();
  });

  it.each([
    {
      name: 'duplicate configured wallet account',
      accountKeys: ['configured-whale', 'configured-whale', 'token-account'],
      preBalances: [10_000_000_000, 10_000_000_000, 0],
      postBalances: [4_000_000_000, 10_000_000_000, 0],
      accountIndex: 2,
    },
    {
      name: 'out-of-range token account index',
      accountKeys: ['configured-whale', 'counterparty'],
      preBalances: [10_000_000_000, 0],
      postBalances: [4_000_000_000, 6_000_000_000],
      accountIndex: 99,
    },
    {
      name: 'mismatched lamport vector length',
      accountKeys: ['configured-whale', 'counterparty', 'token-account'],
      preBalances: [10_000_000_000, 0, 0],
      postBalances: [4_000_000_000, 6_000_000_000],
      accountIndex: 2,
    },
  ])('rejects malformed whale receipt: $name', async ({ accountKeys, preBalances, postBalances, accountIndex }) => {
    const whale = 'configured-whale';
    provider = new SolanaRpcProvider([whale], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as ProviderInternals;
    internal.rpcRequest = async () => ({
      meta: {
        err: null,
        preBalances,
        postBalances,
        preTokenBalances: [{ accountIndex, mint: 'must-not-mark', owner: whale, uiTokenAmount: { amount: '0', decimals: 0 } }],
        postTokenBalances: [{ accountIndex, mint: 'must-not-mark', owner: whale, uiTokenAmount: { amount: '1', decimals: 0 } }],
      },
      transaction: { message: { accountKeys: accountKeys.map((pubkey) => ({ pubkey })), instructions: [] } },
    });
    internal.detectWhaleTx(`malformed-${accountIndex}-${accountKeys.length}`, [`Program ${PUMP_SWAP_AMM} invoke [1]`]);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(provider.whaleActivity.has('must-not-mark')).toBe(false);
  });

  it('fails closed for a PumpSwap transaction without the create_pool discriminator', async () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as ProviderInternals;
    internal.rpcRequest = async () => ({
      blockTime: 1_785_329_073,
      transaction: {
        message: {
          accountKeys: [],
          instructions: [{ programId: PUMP_SWAP_AMM, data: '11111111', accounts: ['wrong-pool', 'x', 'x', 'wrong-mint'] }],
        },
      },
    });

    await internal.onNewPool(PUMP_SWAP_AMM, 'ordinary-pump-swap-signature');

    expect(internal.pendingSnapshots).toEqual([]);
  });

  it('aborts stalled HTTP RPC requests at the configured deadline', async () => {
    globalThis.fetch = vi.fn((_input: string | URL | Request, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true });
    })) as unknown as typeof fetch;
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test', 10);
    const internal = provider as unknown as ProviderInternals;
    await expect(internal.rpcRequest('getBalance', ['address'])).rejects.toMatchObject({ name: 'AbortError' });
  });

  it('shares one in-flight snapshot batch across overlapping polls', async () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as ProviderInternals;
    let resolveBatch!: (value: MarketSnapshot[]) => void;
    let calls = 0;
    internal.fetchSnapshotsOnce = () => {
      calls += 1;
      return new Promise<MarketSnapshot[]>((resolve) => { resolveBatch = resolve; });
    };
    const first = provider.fetchSnapshots();
    const second = provider.fetchSnapshots();
    expect(second).toBe(first);
    expect(calls).toBe(1);
    resolveBatch([]);
    await first;
  });

  it('drains discovered identity without calling an external quote provider', async () => {
    provider = new SolanaRpcProvider([], 5, 'wss://mock', 'https://rpc.test');
    const internal = provider as unknown as ProviderInternals;
    internal.enqueuePending(snapshot(1));

    await expect(internal.fetchSnapshotsOnce()).resolves.toEqual([
      expect.objectContaining({ pairId: 'pair-1', mint: 'mint-1', priceUsd: 0 }),
    ]);
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });
});
