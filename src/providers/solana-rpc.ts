import type { MarketSnapshot } from '../scoring.js';
import type { MarketProvider } from '../scanner.js';
import { defaultHttpFetcher, fetchWithTimeout as fetchHttpWithTimeout } from './http.js';
import {
  decodeBase58,
  validateSolanaInstructionData,
  type SolanaInstructionLayout,
} from './solana-instruction-layouts.js';

const RAYDIUM_AMM = '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8';
const PUMP_FUN = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P';
const RAYDIUM_CPMM = 'CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C';
const METEORA_DLMM = 'LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo';
const MOONSHOT = 'MoonCVVNZFSYkqNXP6bxHLPL6QQJiMagDL3qcqUQTrG';
const PUMP_SWAP_AMM = 'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA';
const PUMP_FUN_CREATE_SCHEMAS = [
  { layout: 'pumpFunCreate', accountCount: 14 },
  { layout: 'pumpFunCreateV2', accountCount: 16 },
] as const;
const RAYDIUM_CPMM_INITIALIZE_SCHEMAS = [
  { layout: 'raydiumCpmmInitialize', accountCount: 20, pool: 3, mint0: 4, mint1: 5 },
  { layout: 'raydiumCpmmInitializeWithPermission', accountCount: 21, pool: 4, mint0: 5, mint1: 6 },
] as const;
const METEORA_INITIALIZE_SCHEMAS = [
  { layout: 'meteoraInitializeLbPair', accountCount: 14, pool: 0, mintX: 2, mintY: 3 },
  { layout: 'meteoraInitializeCustomizablePermissionlessLbPair', accountCount: 14, pool: 0, mintX: 2, mintY: 3 },
  { layout: 'meteoraInitializeLbPair2', accountCount: 16, pool: 0, mintX: 2, mintY: 3 },
  { layout: 'meteoraInitializeCustomizablePermissionlessLbPair2', accountCount: 17, pool: 0, mintX: 2, mintY: 3 },
  { layout: 'meteoraInitializePermissionLbPair', accountCount: 18, pool: 1, mintX: 3, mintY: 4 },
] as const;
const WRAPPED_SOL_MINT = 'So11111111111111111111111111111111111111112';
const DEX_PROGRAMS = [RAYDIUM_AMM, PUMP_FUN, PUMP_SWAP_AMM, RAYDIUM_CPMM, METEORA_DLMM, MOONSHOT] as const;

const DEX_NAMES: Record<string, string> = {
  [RAYDIUM_AMM]: 'raydium-amm',
  [PUMP_FUN]: 'pump-fun',
  [RAYDIUM_CPMM]: 'raydium-cpmm',
  [METEORA_DLMM]: 'meteora',
  [MOONSHOT]: 'moonshot',
  [PUMP_SWAP_AMM]: 'pumpswap',
};

const SOLANA_RPC_WS = 'wss://api.mainnet-beta.solana.com';
const SOLANA_RPC_HTTP = 'https://api.mainnet-beta.solana.com';
const MAX_PENDING_SNAPSHOTS = 500;
const MAX_POOL_REQUESTS = 16;
const MAX_WHALE_REQUESTS = 16;
const MAX_WHALE_WALLETS = 100;
const MAX_WHALE_ACTIVITY = 10_000;
const MAX_RECENT_TRANSACTION_FETCHES = 5_000;
const RECENT_TRANSACTION_FETCH_TTL_MS = 60_000;
const DEFAULT_HTTP_TIMEOUT_MS = 10_000;
const SUBSCRIPTION_ACK_TIMEOUT_MS = 10_000;
const INITIAL_RECONNECT_DELAY_MS = 1_000;
const MAX_RECONNECT_DELAY_MS = 60_000;

// Re-emit cooldown so an already-seen pool can be re-offered as it ripens (Laag A).
const REEMIT_COOLDOWN_MS = 30_000;

type RpcAccountKey = string | number | { pubkey?: string; source?: 'transaction' | 'lookupTable' };
type RpcInstruction = { programId?: RpcAccountKey; programIdIndex?: number; accounts?: RpcAccountKey[]; data?: string };
type InstructionLocation = {
  instructionLocation: 'top_level' | 'inner';
  instructionIndex: number;
  parentInstructionIndex?: number;
};
type LocatedInstruction = InstructionLocation & { instruction: RpcInstruction };
type ParsedPoolCreation = InstructionLocation & { poolAddress: string; mintAddress: string };
type PendingPoolSnapshot = Pick<
  MarketSnapshot,
  'pairId' | 'mint' | 'symbol' | 'source' | 'firstSeenAt' | 'pairCreatedAt' | 'discovery'
>;
type RpcTokenBalance = {
  accountIndex?: number;
  mint?: string;
  owner?: string;
  uiTokenAmount?: { amount?: string; decimals?: number; uiAmount?: number | null };
};
type PoolTransaction = {
  blockTime?: number | null;
  meta?: {
    err?: unknown;
    logMessages?: string[];
    preBalances?: number[];
    postBalances?: number[];
    preTokenBalances?: RpcTokenBalance[];
    postTokenBalances?: RpcTokenBalance[];
    loadedAddresses?: { writable?: RpcAccountKey[]; readonly?: RpcAccountKey[] };
    innerInstructions?: Array<{ index?: number; instructions?: RpcInstruction[] }>;
  };
  transaction?: { message?: { accountKeys?: RpcAccountKey[]; instructions?: RpcInstruction[] } };
};

type CanonicalTokenBalance = { accountIndex: number; mint: string; owner: string; decimals: number; amount: bigint };
type WhaleReceipt = { wallet: string; mint: string; spentLamports: bigint; tokenDelta: bigint };

function canonicalWhaleAccountKeys(tx: PoolTransaction): string[] | undefined {
  const messageKeys = tx.transaction?.message?.accountKeys;
  if (!Array.isArray(messageKeys)) return undefined;
  const hasParsedSource = messageKeys.some((value) => typeof value === 'object' && value !== null && 'source' in value);
  if (hasParsedSource) {
    if (!messageKeys.every((value) => typeof value === 'object'
      && value !== null
      && (value.source === 'transaction' || value.source === 'lookupTable')
      && typeof value.pubkey === 'string')) return undefined;
    return messageKeys.map((value) => (value as { pubkey: string }).pubkey);
  }
  const combined = [
    ...messageKeys,
    ...(tx.meta?.loadedAddresses?.writable ?? []),
    ...(tx.meta?.loadedAddresses?.readonly ?? []),
  ];
  const keys = combined.map(accountKey);
  return keys.every((key): key is string => typeof key === 'string' && key.length > 0) ? keys : undefined;
}

function canonicalTokenBalances(raw: unknown, accountCount: number): Map<number, CanonicalTokenBalance> | undefined {
  if (!Array.isArray(raw)) return undefined;
  const balances = new Map<number, CanonicalTokenBalance>();
  for (const candidate of raw as RpcTokenBalance[]) {
    const accountIndex = candidate?.accountIndex;
    const amount = candidate?.uiTokenAmount?.amount;
    const decimals = candidate?.uiTokenAmount?.decimals;
    if (!Number.isInteger(accountIndex)
      || accountIndex! < 0
      || accountIndex! >= accountCount
      || balances.has(accountIndex!)
      || typeof candidate.mint !== 'string'
      || candidate.mint.length === 0
      || typeof candidate.owner !== 'string'
      || candidate.owner.length === 0
      || typeof amount !== 'string'
      || !/^\d+$/.test(amount)
      || !Number.isInteger(decimals)
      || decimals! < 0
      || decimals! > 255) return undefined;
    balances.set(accountIndex!, {
      accountIndex: accountIndex!, mint: candidate.mint, owner: candidate.owner,
      decimals: decimals!, amount: BigInt(amount),
    });
  }
  return balances;
}

function parseWhaleReceipts(tx: PoolTransaction, wallets: ReadonlySet<string>, minimumSpend: bigint): WhaleReceipt[] {
  if (tx.meta?.err !== null) return [];
  const accountKeys = canonicalWhaleAccountKeys(tx);
  const preBalances = tx.meta.preBalances;
  const postBalances = tx.meta.postBalances;
  if (!accountKeys
    || !Array.isArray(preBalances)
    || !Array.isArray(postBalances)
    || preBalances.length !== accountKeys.length
    || postBalances.length !== accountKeys.length
    || !preBalances.every((value) => Number.isSafeInteger(value) && value >= 0)
    || !postBalances.every((value) => Number.isSafeInteger(value) && value >= 0)) return [];

  const preTokens = canonicalTokenBalances(tx.meta.preTokenBalances, accountKeys.length);
  const postTokens = canonicalTokenBalances(tx.meta.postTokenBalances, accountKeys.length);
  if (!preTokens || !postTokens) return [];
  for (const [index, before] of preTokens) {
    const after = postTokens.get(index);
    if (after && (after.owner !== before.owner || after.mint !== before.mint || after.decimals !== before.decimals)) return [];
  }

  const receipts: WhaleReceipt[] = [];
  for (const wallet of wallets) {
    const walletIndices = accountKeys.flatMap((key, index) => key === wallet ? [index] : []);
    if (walletIndices.length !== 1) continue;
    const walletIndex = walletIndices[0];
    const spentLamports = BigInt(preBalances[walletIndex]) - BigInt(postBalances[walletIndex]);
    if (spentLamports < minimumSpend) continue;

    const deltas = new Map<string, bigint>();
    for (const token of preTokens.values()) {
      if (token.owner === wallet) deltas.set(token.mint, (deltas.get(token.mint) ?? 0n) - token.amount);
    }
    for (const token of postTokens.values()) {
      if (token.owner === wallet) deltas.set(token.mint, (deltas.get(token.mint) ?? 0n) + token.amount);
    }
    for (const [mint, tokenDelta] of deltas) {
      if (mint !== WRAPPED_SOL_MINT && tokenDelta > 0n) receipts.push({ wallet, mint, spentLamports, tokenDelta });
    }
  }
  return receipts;
}

function accountKey(value: RpcAccountKey | undefined): string | undefined {
  if (typeof value === 'string') return value;
  return typeof value === 'object' && typeof value.pubkey === 'string' ? value.pubkey : undefined;
}

function transactionAccountKeys(tx: PoolTransaction): Array<string | undefined> {
  return [
    ...(tx.transaction?.message?.accountKeys ?? []),
    ...(tx.meta?.loadedAddresses?.writable ?? []),
    ...(tx.meta?.loadedAddresses?.readonly ?? []),
  ].map(accountKey);
}

function resolveAccountKey(value: RpcAccountKey | undefined, messageKeys: Array<string | undefined>): string | undefined {
  return typeof value === 'number' ? messageKeys[value] : accountKey(value);
}

function transactionInstructions(tx: PoolTransaction): LocatedInstruction[] | undefined {
  const topLevel = tx.transaction?.message?.instructions ?? [];
  const innerGroups = tx.meta?.innerInstructions ?? [];
  const seenParentIndices = new Set<number>();
  for (const group of innerGroups) {
    if (!Number.isInteger(group.index)
      || group.index! < 0
      || group.index! >= topLevel.length
      || seenParentIndices.has(group.index!)
      || !Array.isArray(group.instructions)) return undefined;
    seenParentIndices.add(group.index!);
  }
  return [
    ...topLevel.map((instruction, instructionIndex) => ({
      instruction,
      instructionLocation: 'top_level' as const,
      instructionIndex,
    })),
    ...innerGroups.flatMap((group) => group.instructions!.map((instruction, instructionIndex) => ({
      instruction,
      instructionLocation: 'inner' as const,
      instructionIndex,
      parentInstructionIndex: group.index!,
    }))),
  ];
}

function findProgramInstruction(
  tx: PoolTransaction,
  program: string,
  layouts: readonly SolanaInstructionLayout[],
): LocatedInstruction | undefined {
  const messageKeys = transactionAccountKeys(tx);
  return transactionInstructions(tx)?.find(({ instruction }) => {
    const instructionProgram = resolveAccountKey(instruction.programId, messageKeys)
      ?? (typeof instruction.programIdIndex === 'number' ? messageKeys[instruction.programIdIndex] : undefined);
    return instructionProgram === program
      && typeof instruction.data === 'string'
      && layouts.some((layout) => validateSolanaInstructionData(layout, instruction.data!));
  });
}

function instructionAccounts(tx: PoolTransaction, located: LocatedInstruction | undefined): string[] | undefined {
  const messageKeys = transactionAccountKeys(tx);
  const accounts = located?.instruction.accounts?.map((account) => resolveAccountKey(account, messageKeys));
  if (!accounts || accounts.some((account) => !account)) return undefined;
  return accounts as string[];
}

function parsedPool(poolAddress: string, mintAddress: string, located: LocatedInstruction): ParsedPoolCreation {
  return {
    poolAddress,
    mintAddress,
    instructionLocation: located.instructionLocation,
    instructionIndex: located.instructionIndex,
    ...(located.parentInstructionIndex === undefined ? {} : { parentInstructionIndex: located.parentInstructionIndex }),
  };
}

function instructionProgram(tx: PoolTransaction, instruction: RpcInstruction): string | undefined {
  const messageKeys = transactionAccountKeys(tx);
  return resolveAccountKey(instruction.programId, messageKeys)
    ?? (typeof instruction.programIdIndex === 'number' ? messageKeys[instruction.programIdIndex] : undefined);
}

function nonWrappedSolMint(first: string | undefined, second: string | undefined): string | undefined {
  if (!first || !second || first === second) return undefined;
  if (first === WRAPPED_SOL_MINT && second !== WRAPPED_SOL_MINT) return second;
  if (second === WRAPPED_SOL_MINT && first !== WRAPPED_SOL_MINT) return first;
  return undefined;
}

/** PumpSwap account roles pinned to the official Pump AMM IDL:
 * pool=0, base_mint=3, quote_mint=4.
 * https://github.com/pump-fun/pump-public-docs/blob/main/idl/pump_amm.json
 */
function parsePumpSwapCreatePool(tx: PoolTransaction): ParsedPoolCreation | undefined {
  if (tx.meta?.err !== null) return undefined;
  const instruction = findProgramInstruction(tx, PUMP_SWAP_AMM, ['pumpSwapCreatePool']);
  const accounts = instructionAccounts(tx, instruction);
  if (!instruction || !accounts || accounts.length !== 18) return undefined;
  const poolAddress = accounts[0];
  const mintAddress = nonWrappedSolMint(accounts[3], accounts[4]);
  if (!poolAddress || !mintAddress || new Set([poolAddress, accounts[3], accounts[4]]).size !== 3) return undefined;
  return parsedPool(poolAddress, mintAddress, instruction);
}

/** Pump.fun roles pinned to the official Pump IDL: mint=0, bonding_curve=2. */
function parsePumpFunCreate(tx: PoolTransaction): ParsedPoolCreation | undefined {
  if (tx.meta?.err !== null) return undefined;
  for (const schema of PUMP_FUN_CREATE_SCHEMAS) {
    const instruction = findProgramInstruction(tx, PUMP_FUN, [schema.layout]);
    const accounts = instructionAccounts(tx, instruction);
    if (!instruction || !accounts || accounts.length !== schema.accountCount) continue;
    const mintAddress = accounts[0];
    const poolAddress = accounts[2];
    if (poolAddress && mintAddress && poolAddress !== mintAddress) return parsedPool(poolAddress, mintAddress, instruction);
  }
  return undefined;
}

/** Raydium AMM v4 Initialize2 roles from official program source:
 * opcode=1; pool=4; 21-account mints=8/9; 19-account mints=7/8.
 */
function parseRaydiumAmmInitialize2(tx: PoolTransaction): ParsedPoolCreation | undefined {
  if (tx.meta?.err !== null) return undefined;
  const instruction = transactionInstructions(tx)?.find((candidate) => {
    if (instructionProgram(tx, candidate.instruction) !== RAYDIUM_AMM || typeof candidate.instruction.data !== 'string') return false;
    const decoded = decodeBase58(candidate.instruction.data);
    return decoded?.length === 26 && decoded[0] === 1;
  });
  const accounts = instructionAccounts(tx, instruction);
  if (!instruction || !accounts || accounts.length !== 21 && accounts.length !== 19) return undefined;
  const coinMintIndex = accounts.length === 21 ? 8 : 7;
  const pcMintIndex = accounts.length === 21 ? 9 : 8;
  const poolAddress = accounts[4];
  const mintAddress = nonWrappedSolMint(accounts[coinMintIndex], accounts[pcMintIndex]);
  if (!poolAddress || !mintAddress || poolAddress === mintAddress) return undefined;
  return parsedPool(poolAddress, mintAddress, instruction);
}

/** Raydium CPMM account roles pinned to initialize/initialize_with_permission in the official IDL. */
function parseRaydiumCpmmInitialize(tx: PoolTransaction): ParsedPoolCreation | undefined {
  if (tx.meta?.err !== null) return undefined;
  for (const schema of RAYDIUM_CPMM_INITIALIZE_SCHEMAS) {
    const instruction = findProgramInstruction(tx, RAYDIUM_CPMM, [schema.layout]);
    const accounts = instructionAccounts(tx, instruction);
    if (!instruction || !accounts || accounts.length !== schema.accountCount) continue;
    const poolAddress = accounts[schema.pool];
    const mintAddress = nonWrappedSolMint(accounts[schema.mint0], accounts[schema.mint1]);
    if (poolAddress && mintAddress && poolAddress !== mintAddress) return parsedPool(poolAddress, mintAddress, instruction);
  }
  return undefined;
}

/** Meteora DLMM roles pinned to all five current initialize variants in the official IDL. */
function parseMeteoraInitialize(tx: PoolTransaction): ParsedPoolCreation | undefined {
  if (tx.meta?.err !== null) return undefined;
  for (const schema of METEORA_INITIALIZE_SCHEMAS) {
    const instruction = findProgramInstruction(tx, METEORA_DLMM, [schema.layout]);
    const accounts = instructionAccounts(tx, instruction);
    if (!instruction || !accounts || accounts.length !== schema.accountCount) continue;
    const poolAddress = accounts[schema.pool];
    const mintAddress = nonWrappedSolMint(accounts[schema.mintX], accounts[schema.mintY]);
    if (poolAddress && mintAddress
      && new Set([poolAddress, accounts[schema.mintX], accounts[schema.mintY]]).size === 3) {
      return parsedPool(poolAddress, mintAddress, instruction);
    }
  }
  return undefined;
}

/** Moonshot/Moonit TokenMint roles pinned to the official V4 SDK IDL: curve=2, mint=3. */
function parseMoonshotTokenMint(tx: PoolTransaction): ParsedPoolCreation | undefined {
  if (tx.meta?.err !== null) return undefined;
  const instruction = findProgramInstruction(tx, MOONSHOT, ['moonitTokenMint']);
  const accounts = instructionAccounts(tx, instruction);
  if (!instruction || !accounts || accounts.length !== 11) return undefined;
  const poolAddress = accounts[2];
  const mintAddress = accounts[3];
  if (!poolAddress || !mintAddress || poolAddress === mintAddress) return undefined;
  return parsedPool(poolAddress, mintAddress, instruction);
}

function parsePoolCreation(program: string, tx: PoolTransaction): ParsedPoolCreation | undefined {
  if (program === PUMP_SWAP_AMM) return parsePumpSwapCreatePool(tx);
  if (program === PUMP_FUN) return parsePumpFunCreate(tx);
  if (program === RAYDIUM_AMM) return parseRaydiumAmmInitialize2(tx);
  if (program === RAYDIUM_CPMM) return parseRaydiumCpmmInitialize(tx);
  if (program === METEORA_DLMM) return parseMeteoraInitialize(tx);
  if (program === MOONSHOT) return parseMoonshotTokenMint(tx);
  return undefined;
}

/**
 * WebSocket-based Solana RPC provider.
 * Subscribes to DEX program logs in real-time to detect new pool creations
 * milliseconds after they appear on-chain.
 */
export class SolanaRpcProvider implements MarketProvider {
  private ws: WebSocket | null = null;
  private pendingSnapshots: PendingPoolSnapshot[] = [];
  private fetchInFlight?: Promise<MarketSnapshot[]>;
  private knownPools = new Map<string, number>();
  private knownPoolsCleanupCounter = 0;
  private subscribed = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private subscriptionAckTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectAttempt = 0;
  private connectionGeneration = 0;
  private destroyed = false;
  private requestId = 1;
  private transactionRetryDelayMs = 250;
  // Whale tracking
  readonly whaleActivity: Map<string, number> = new Map();
  private readonly whaleWallets: ReadonlySet<string>;
  private readonly minWhaleTxLamports: bigint;
  private pendingRequests = new Map<number, { resolve: (value: unknown) => void; timer: ReturnType<typeof setTimeout> }>();
  private readonly pendingSubscriptionRequests = new Set<number>();
  private readonly activeSubscriptions = new Set<number>();
  private readonly diagnostics: string[] = [];
  private whaleCleanupTimer: ReturnType<typeof setInterval> | null = null;
  private readonly activeAbortControllers = new Set<AbortController>();
  private readonly poolRequestsInFlight = new Set<string>();
  private readonly whaleRequestsInFlight = new Set<string>();
  private readonly transactionFetches = new Map<string, { startedAt: number; promise: Promise<PoolTransaction | null> }>();
  // RPC endpoints
  private readonly wsEndpoint: string;
  private readonly httpEndpoint: string;

  constructor(
    whaleWallets: string[] = [],
    minWhaleTxSol = 5,
    wsEndpoint = SOLANA_RPC_WS,
    httpEndpoint = SOLANA_RPC_HTTP,
    private readonly httpTimeoutMs = DEFAULT_HTTP_TIMEOUT_MS,
  ) {
    if (!Number.isInteger(httpTimeoutMs) || httpTimeoutMs <= 0) throw new Error('HTTP timeout must be a positive integer');
    const normalizedWhaleWallets = Array.from(new Set(whaleWallets.map((wallet) => wallet.trim()).filter(Boolean)));
    if (normalizedWhaleWallets.length > MAX_WHALE_WALLETS) throw new Error(`whale wallet count exceeds ${MAX_WHALE_WALLETS}`);
    this.whaleWallets = new Set(normalizedWhaleWallets);
    const minimumLamports = minWhaleTxSol * 1_000_000_000;
    if (!Number.isSafeInteger(minimumLamports) || minimumLamports <= 0) throw new Error('minimum whale transaction must resolve to a positive safe lamport integer');
    this.minWhaleTxLamports = BigInt(minimumLamports);
    this.wsEndpoint = wsEndpoint;
    this.httpEndpoint = httpEndpoint;
    this.connect();
    this.whaleCleanupTimer = setInterval(() => this.cleanupWhaleActivity(), 600_000).unref();
  }

  destroy(): void {
    this.destroyed = true;
    this.connectionGeneration += 1;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.subscriptionAckTimer) clearTimeout(this.subscriptionAckTimer);
    if (this.whaleCleanupTimer) clearInterval(this.whaleCleanupTimer);

    this.activeAbortControllers.forEach((controller) => controller.abort());
    this.activeAbortControllers.clear();
    const socket = this.ws;
    this.ws = null;
    socket?.close();
    this.transactionFetches.clear();
  }

  fetchSnapshots(): Promise<MarketSnapshot[]> {
    if (!this.fetchInFlight) {
      const run = this.fetchSnapshotsOnce();
      this.fetchInFlight = run;
      const clear = () => { if (this.fetchInFlight === run) this.fetchInFlight = undefined; };
      void run.then(clear, clear);
    }
    return this.fetchInFlight;
  }

  drainDiagnostics(): string[] {
    return this.diagnostics.splice(0);
  }

  private pushDiagnostic(message: string): void {
    if (this.diagnostics.length >= 100) this.diagnostics.shift();
    this.diagnostics.push(message);
  }

  private async fetchSnapshotsOnce(): Promise<MarketSnapshot[]> {
    const pools = this.pendingSnapshots.slice(-MAX_PENDING_SNAPSHOTS);
    this.pendingSnapshots = [];

    // Discovery owns identity and provenance only. Never call a market-data
    // aggregator here: exact-pair quoting belongs to CompositeProvider so it can
    // use independent fallbacks without dropping the discovered pool identity.
    const observedAt = new Date().toISOString();
    return pools.map((pool) => ({
      ...pool,
      observedAt,
      priceUsd: 0,
    }));
  }

  private enqueuePending(snapshot: PendingPoolSnapshot): void {
    if (this.pendingSnapshots.length >= MAX_PENDING_SNAPSHOTS) this.pendingSnapshots.shift();
    this.pendingSnapshots.push(snapshot);
  }

  /* ─── WebSocket ─────────────────────────────────────── */

  private connect(): void {
    if (this.destroyed) return;
    const generation = ++this.connectionGeneration;
    try {
      const socket = new WebSocket(this.wsEndpoint);
      this.ws = socket;
      socket.addEventListener('open', () => this.onOpen(socket, generation));
      socket.addEventListener('message', (event) => this.onMessage(event, socket, generation));
      socket.addEventListener('close', () => this.onClose(socket, generation));
      socket.addEventListener('error', () => this.onError(socket, generation));
    } catch {
      if (generation === this.connectionGeneration) this.ws = null;
      this.scheduleReconnect();
    }
  }

  private isCurrentSocket(socket: WebSocket | null, generation: number): socket is WebSocket {
    return !this.destroyed && socket !== null && this.ws === socket && this.connectionGeneration === generation;
  }

  private clearSubscriptionAckDeadline(): void {
    if (this.subscriptionAckTimer) clearTimeout(this.subscriptionAckTimer);
    this.subscriptionAckTimer = null;
  }

  private onOpen(socket: WebSocket | null = this.ws, generation = this.connectionGeneration): void {
    if (!this.isCurrentSocket(socket, generation)) return;
    console.log(JSON.stringify({ event: 'solana_ws_connected' }));
    this.subscribed = false;
    this.pendingSubscriptionRequests.clear();
    this.activeSubscriptions.clear();
    this.clearSubscriptionAckDeadline();

    for (const program of DEX_PROGRAMS) {
      const id = this.requestId++;
      this.pendingSubscriptionRequests.add(id);
      socket.send(JSON.stringify({
        jsonrpc: '2.0',
        id,
        method: 'logsSubscribe',
        params: [{ mentions: [program] }, { commitment: 'processed' }],
      }));
    }

    // Keep this socket's discovery contract exact: six program-scoped subscriptions.
    // Whale analysis consumes those same DEX log notifications and must never add
    // wallet-scoped subscriptions that can mask missing program acknowledgements.
    this.subscriptionAckTimer = setTimeout(() => {
      this.subscriptionAckTimer = null;
      if (!this.isCurrentSocket(socket, generation) || this.subscribed) return;
      this.onError(
        socket,
        generation,
        `discovery: solana_ws: subscription_timeout pending=${this.pendingSubscriptionRequests.size}`,
      );
    }, SUBSCRIPTION_ACK_TIMEOUT_MS).unref();
  }

  private onMessage(event: MessageEvent, socket: WebSocket | null = this.ws, generation = this.connectionGeneration): void {
    if (!this.isCurrentSocket(socket, generation)) return;
    let msg: unknown;
    try { msg = JSON.parse(event.data as string); } catch { return; }
    if (!msg || typeof msg !== 'object') return;
    const packet = msg as Record<string, unknown>;

    // Handle pending RPC request responses
    if (typeof packet.id === 'number') {
      const pending = this.pendingRequests.get(packet.id);
      if (pending) {
        clearTimeout(pending.timer);
        pending.resolve(packet.result ?? null);
        this.pendingRequests.delete(packet.id);
        return;
      }
      if (this.pendingSubscriptionRequests.delete(packet.id)) {
        if (typeof packet.result === 'number') {
          this.activeSubscriptions.add(packet.result);
        } else {
          const error = packet.error as { code?: unknown; message?: unknown } | undefined;
          const code = typeof error?.code === 'number' || typeof error?.code === 'string' ? String(error.code) : 'unknown';
          const message = typeof error?.message === 'string'
            ? error.message.replace(/[\r\n]+/g, ' ').slice(0, 160)
            : 'unknown';
          this.pushDiagnostic(`discovery: solana_ws: subscription_rejected code=${code} message=${message}`);
          this.onError(socket, generation, '');
          return;
        }
        this.subscribed = this.pendingSubscriptionRequests.size === 0
          && this.activeSubscriptions.size === DEX_PROGRAMS.length;
        if (this.subscribed) {
          this.clearSubscriptionAckDeadline();
          this.reconnectAttempt = 0;
        }
      }
      return;
    }

    // Handle log subscription
    if (packet.method === 'logsNotification') {
      const params = packet.params as { result?: { context?: { slot?: number }; value?: { err?: unknown; signature?: string; logs?: string[] } } } | undefined;
      const value = params?.result?.value;
      if (!value || value.err || !value.signature) return;
      const logs = value.logs ?? [];
      const triggeredPrograms = DEX_PROGRAMS.filter((program) => this.detectNewPool(program, logs));
      if (triggeredPrograms.length > 0) {
        // Hydrate only positive create/init triggers. The exact instruction parser
        // remains authoritative and rejects unrelated CPI or ambiguous log text.
        // This prevents successful swaps from becoming one HTTP getTransaction
        // request each while preserving one shared hydration for mixed-protocol txs.
        this.scheduleNewPool(triggeredPrograms, value.signature, {
          receiptAt: new Date().toISOString(),
          ...(typeof params?.result?.context?.slot === 'number' ? { slot: params.result.context.slot } : {}),
        });
      }
      this.detectWhaleTx(value.signature, logs);
    }
  }

  private onError(socket: WebSocket, generation: number, diagnostic = 'discovery: solana_ws: error'): void {
    if (!this.isCurrentSocket(socket, generation)) return;
    this.clearSubscriptionAckDeadline();
    this.ws = null;
    this.subscribed = false;
    this.pendingSubscriptionRequests.clear();
    this.activeSubscriptions.clear();
    socket.close();
    if (diagnostic) this.pushDiagnostic(diagnostic);
    this.scheduleReconnect();
  }

  private onClose(socket: WebSocket | null = this.ws, generation = this.connectionGeneration): void {
    if (!this.isCurrentSocket(socket, generation)) return;
    this.clearSubscriptionAckDeadline();
    this.ws = null;
    this.subscribed = false;
    this.pendingSubscriptionRequests.clear();
    this.activeSubscriptions.clear();
    this.pushDiagnostic('discovery: solana_ws: disconnected');
    this.scheduleReconnect();
  }

  private scheduleReconnect(): void {
    if (this.destroyed || this.reconnectTimer) return;
    const delayMs = Math.min(
      MAX_RECONNECT_DELAY_MS,
      INITIAL_RECONNECT_DELAY_MS * 2 ** Math.min(this.reconnectAttempt, 16),
    );
    this.reconnectAttempt += 1;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delayMs);
  }

  /* ─── Pool creation detection ──────────────────────────── */

  private detectNewPool(program: string, logs: string[]): string | undefined {
    const programLog = logs.find((log) => log.startsWith(`Program ${program}`));
    if (!programLog) return undefined;
    if (program === PUMP_SWAP_AMM) {
      return logs.some((log) => log.trim().toLowerCase() === 'program log: instruction: createpool') ? program : undefined;
    }
    if (program === PUMP_FUN) {
      return logs.some((log) => {
        const normalized = log.trim().toLowerCase();
        return normalized === 'program log: instruction: create' || normalized === 'program log: instruction: createv2';
      }) ? program : undefined;
    }
    if (program === MOONSHOT) {
      return logs.some((log) => log.trim().toLowerCase() === 'program log: instruction: tokenmint') ? program : undefined;
    }
    const entry = logs.find(
      (log) => {
        const normalized = log.toLowerCase();
        return normalized.includes('initialize') || normalized.includes('create') || normalized.includes('init') || normalized.includes('initialize2');
      },
    );
    return entry ? program : undefined;
  }

  /* ─── Whale transaction detection ─────────────────────── */

  private cleanupWhaleActivity(): void {
    const cutoff = Date.now() - 3_600_000; // 1 hour ago
    for (const [mint, ts] of this.whaleActivity) {
      if (ts < cutoff) this.whaleActivity.delete(mint);
    }
  }

  private detectWhaleTx(signature: string, logs: string[]): void {
    if (this.whaleWallets.size === 0) return;
    const dexLog = logs.find((log) => DEX_PROGRAMS.some((program) => log.includes(program)));
    if (!dexLog) return;
    if (this.destroyed || this.whaleRequestsInFlight.has(signature) || this.whaleRequestsInFlight.size >= MAX_WHALE_REQUESTS) return;
    this.whaleRequestsInFlight.add(signature);

    // Share the same bounded, recent signature transaction lookup with pool parsing.
    this.fetchConfirmedTransaction(signature).then((transaction) => {
      if (!transaction?.transaction) return;
      const receipts = parseWhaleReceipts(transaction, this.whaleWallets, this.minWhaleTxLamports);
      const now = Date.now();
      for (const receipt of receipts) {
        if (!this.whaleActivity.has(receipt.mint) && this.whaleActivity.size >= MAX_WHALE_ACTIVITY) {
          const oldest = this.whaleActivity.keys().next().value as string | undefined;
          if (oldest) this.whaleActivity.delete(oldest);
        }
        this.whaleActivity.set(receipt.mint, now);
        console.log(JSON.stringify({
          event: 'whale_detected', wallet: receipt.wallet, mint: receipt.mint, signature,
          spentLamports: receipt.spentLamports.toString(), tokenDelta: receipt.tokenDelta.toString(),
        }));
      }
    }).catch(() => {}).finally(() => this.whaleRequestsInFlight.delete(signature));
  }

  private scheduleNewPool(
    programs: string | readonly string[],
    signature: string,
    receipt: { receiptAt: string; slot?: number },
  ): void {
    if (this.destroyed || this.poolRequestsInFlight.has(signature) || this.poolRequestsInFlight.size >= MAX_POOL_REQUESTS) return;
    this.poolRequestsInFlight.add(signature);
    void this.onNewPool(programs, signature, receipt).finally(() => this.poolRequestsInFlight.delete(signature));
  }

  private async onNewPool(
    programs: string | readonly string[],
    signature: string,
    receipt: { receiptAt: string; slot?: number } = { receiptAt: new Date().toISOString() },
  ): Promise<void> {
    try {
      // A processed log can precede confirmed transaction availability. Retry
      // only null consistency-window responses; RPC errors still fail immediately.
      const tx = await this.fetchConfirmedTransaction(signature);
      if (!tx?.transaction || tx.meta?.err !== null) return;
      if (typeof tx.blockTime !== 'number' || !Number.isFinite(tx.blockTime)) return;

      const candidates = typeof programs === 'string' ? [programs] : Array.from(new Set(programs));
      for (const program of candidates) {
        const parsedPool = parsePoolCreation(program, tx);
        const dexName = DEX_NAMES[program];
        if (!parsedPool || !dexName) continue;
        const { poolAddress, mintAddress } = parsedPool;

        const now = Date.now();
        // Cooldown-gated re-emit: allow an already-seen pool to be re-offered so the
        // scanner can re-evaluate it as it ripens (Laag A), suppress within-window floods.
        const lastSeen = this.knownPools.get(poolAddress);
        if (lastSeen !== undefined && now - lastSeen < REEMIT_COOLDOWN_MS) continue;
        this.knownPools.set(poolAddress, now);
        this.cleanupKnownPoolsIfNeeded();
        const pairCreatedAt = new Date(tx.blockTime * 1_000).toISOString();

        this.enqueuePending({
          pairId: poolAddress,
          mint: mintAddress,
          symbol: mintAddress.slice(0, 6).toUpperCase(),
          source: `solana_rpc_ws_${dexName}`,
          firstSeenAt: receipt.receiptAt,
          pairCreatedAt,
          discovery: {
            signature,
            ...(typeof receipt.slot === 'number' ? { slot: receipt.slot } : {}),
            programId: program,
            instructionLocation: parsedPool.instructionLocation,
            instructionIndex: parsedPool.instructionIndex,
            ...(parsedPool.parentInstructionIndex === undefined ? {} : { parentInstructionIndex: parsedPool.parentInstructionIndex }),
            receiptAt: receipt.receiptAt,
          },
        });

        console.log(JSON.stringify({
          event: 'solana_rpc_new_pool',
          pool: poolAddress,
          mint: mintAddress,
          dex: dexName,
          signature: signature.slice(0, 16),
        }));
      }
    } catch (error) {
      console.warn(JSON.stringify({
        event: 'solana_rpc_pool_parse_error',
        signature: signature.slice(0, 16),
        message: error instanceof Error ? error.message : String(error),
      }));
    }
  }

  private fetchConfirmedTransaction(signature: string): Promise<PoolTransaction | null> {
    const now = Date.now();
    const existing = this.transactionFetches.get(signature);
    if (existing && now - existing.startedAt <= RECENT_TRANSACTION_FETCH_TTL_MS) return existing.promise;
    if (existing) this.transactionFetches.delete(signature);

    const promise = this.fetchConfirmedTransactionUncached(signature);
    this.transactionFetches.set(signature, { startedAt: now, promise });
    while (this.transactionFetches.size > MAX_RECENT_TRANSACTION_FETCHES) {
      const oldest = this.transactionFetches.keys().next().value as string | undefined;
      if (!oldest) break;
      this.transactionFetches.delete(oldest);
    }
    return promise;
  }

  private async fetchConfirmedTransactionUncached(signature: string): Promise<PoolTransaction | null> {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const tx = await this.rpcRequest('getTransaction', [
        signature,
        { encoding: 'jsonParsed', commitment: 'confirmed', maxSupportedTransactionVersion: 0 },
      ]) as PoolTransaction | null;
      if (tx !== null) return tx;
      if (attempt < 2) await new Promise<void>((resolve) => setTimeout(resolve, this.transactionRetryDelayMs));
    }
    return null;
  }

  /* ─── RPC calls ───────────────────────────────────────── */

  /** Clear knownPools periodically to prevent unbounded memory growth */
  private cleanupKnownPoolsIfNeeded(): void {
    this.knownPoolsCleanupCounter++;
    if (this.knownPoolsCleanupCounter >= 5000) {
      this.knownPools.clear();
      this.knownPoolsCleanupCounter = 0;
    }
  }

  /* ─── RPC calls ───────────────────────────────────────── */

  private fetchWithTimeout(input: string, init?: RequestInit): Promise<Response> {
    return fetchHttpWithTimeout(
      defaultHttpFetcher,
      input,
      init,
      this.httpTimeoutMs,
      this.activeAbortControllers,
    );
  }

  private rpcRequest(method: string, params: unknown[]): Promise<unknown> {
    // Try HTTP POST as fallback; WebSocket is used for subscriptions only
    return this.fetchWithTimeout(this.httpEndpoint, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: this.requestId++, method, params }),
    }).then(async (response) => {
      if (!response.ok) throw new Error(`Solana RPC HTTP ${response.status}`);
      const data = await response.json() as { result?: unknown; error?: unknown };
      if (data.error) throw new Error(`Solana RPC error: ${JSON.stringify(data.error)}`);
      return data.result;
    });
  }
}