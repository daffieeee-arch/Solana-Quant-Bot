// Triton One geyser-gRPC (Dragon's Mouth / Yellowstone) client factory — the
// direct raw-transaction stream with auto-reconnect, used for live discovery.
//
// Replaces (or complements) the Vixen parsed-stream seam with the raw geyser
// Subscribe: one bidi stream filtered to the launch programs, then per-program
// dispatchers decode each raw transaction into the same VixenUpdate-shaped
// payloads that TritonProvider.handlers already understand.
//
// Why this path: per Triton docs, Dragon's Mouth gRPC is ~400ms faster than
// WebSocket, supports accounts_data_slice (bandwidth) and from_slot replay.
import * as GeyserPkg from '@triton-one/yellowstone-grpc';
import bs58 from 'bs58';
import type { TritonClientFactory, TritonClientLike, TritonStreamLike, VixenUpdate } from './triton.js';

// CJS/ESM-interop: de SDK is een CJS-pakket met exports.default = Client.
// Zowel Node-ESM als TS zien de default-export anders; pak de klasse robuust.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const GeyserClient = (GeyserPkg as any).default ?? GeyserPkg;
// CommitmentLevel.PROCESSED = 0 (laagste latentie voor verse-launch discovery).
const COMMITMENT_PROCESSED = 0;

// Alle launch-programma's (multi-DEX, zie Vixen SDK ProgramAddress):
// pump.fun, Raydium AMMv4/CPMM/CLMM, Meteora AMM, Orca Whirlpool, PumpSwaps,
// Moonshot. Eén geyser-stream dekt alle programma's (dezelfde kostenstructuur).
const PUMP = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P';
const RAYDIUM_AMM = '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8';
const RAYDIUM_CPMM = 'CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C';
const LAUNCH_PROGRAMS = [
  PUMP,
  RAYDIUM_AMM,
  RAYDIUM_CPMM,
  'CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK', // Raydium CLMM
  'cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG',   // Meteora AMM
  'whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc',   // Orca Whirlpool
  'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA',   // PumpSwaps
  'MoonCVVNZFSYkqNXP6bxHLPL6QQJiMagDL3qcqUQTrG',    // Moonshot
];

/** Track active subscriber groups so reconnects re-attach filters. */
interface Subscriber {
  program: string;
  listener?: (value: VixenUpdate) => void;
  errorListener?: (error: Error) => void;
}

/** Eerste-geziene mint per bonding-curve: voorkomt ruis van gedeelde/liquideerde
 * curves waarop meerdere mints (of post-migratie junk) handelen. */
export const firstMintPerCurve = new Map<string, string>();

function decodeKey(key: unknown): string | undefined {
  if (typeof key === 'string') return key;
  if (key instanceof Uint8Array || (typeof Buffer !== 'undefined' && Buffer.isBuffer(key))) {
    try { return bs58.encode(Buffer.from(key as Uint8Array)); } catch { return undefined; }
  }
  return undefined;
}

/**
 * Bounded collector van parsePumpTxn-failure-redenen (systematic-debugging).
 * disabled by default; activeer met env DEBUG_PARSE_PUMP=1.
 * Max 50 entries, rotating (geen unbounded groei). Geen payload/secrets.
 */
export const PUMP_PARSE_DEBUG_MAX = 50;
export const pumpParseFailures: { reason: string; hasTransaction: boolean; hasLogs: boolean; hasBuy: boolean; hasSell: boolean; hasBuyV2: boolean; hasSellV2: boolean; innerCount: number; pumpCpCount: number; accountsMin: number; accountsMax: number; allAccounts: number }[] = [];

function recordPumpParseFailure(payload: any, reason: string): void {
  if (!process.env.DEBUG_PARSE_PUMP) return;
  if (pumpParseFailures.length >= PUMP_PARSE_DEBUG_MAX) { pumpParseFailures.shift(); }
  const outer = payload?.transaction;
  const inner = outer?.transaction ?? outer;
  const logs: string[] = inner?.meta?.logMessages ?? [];
  let hasBuy = false, hasSell = false, hasBuyV2 = false, hasSellV2 = false;
  for (const l of logs) {
    if (l.includes('Program log: Instruction: Buy') && !l.includes('BuyV2') && !l.includes('BuyStable')) hasBuy = true;
    if (l.includes('Program log: Instruction: Sell') && !l.includes('SellV2') && !l.includes('SellStable')) hasSell = true;
    if (/Program log: Instruction: BuyV2/.test(l)) hasBuyV2 = true;
    if (/Program log: Instruction: SellV2/.test(l)) hasSellV2 = true;
  }
  let pumpCpCount = 0; let accountsMin = Infinity; let accountsMax = -1; let allAccounts = 0;
  for (const group of inner?.meta?.innerInstructions ?? []) {
    for (const ix of group?.instructions ?? []) {
      allAccounts += 1;
      const rawAccts = ix?.accounts;
      const cnt = (Buffer.isBuffer(rawAccts) ? rawAccts.length : (rawAccts?.length ?? 0));
      if (cnt < accountsMin) accountsMin = cnt;
      if (cnt > accountsMax) accountsMax = cnt;
      const keys = ((inner?.transaction?.message?.accountKeys ?? []) as unknown[]);
      const progKey = keys[ix?.programIdIndex] ?? '';
      if (String(progKey) === '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P') pumpCpCount++;
    }
  }
  pumpParseFailures.push({ reason, hasTransaction: !!payload?.transaction, hasLogs: logs.length > 0, hasBuy, hasSell, hasBuyV2, hasSellV2, innerCount: (inner?.meta?.innerInstructions ?? []).length, pumpCpCount, accountsMin: accountsMin === Infinity ? 0 : accountsMin, accountsMax: accountsMax === -1 ? 0 : accountsMax, allAccounts });
}

/** Extract pump.fun buy/sell CPI from a raw geyser transaction payload. */
export function parsePumpTxn(payload: any): { mint: string; curve: string; kind: 'buy' | 'sell' } | undefined {
  // SDK-payload: { transaction: { transaction: { signature, transaction, meta, index } } }
  const outer = payload?.transaction;
  const inner = outer?.transaction ?? outer; // {signature,isVote,transaction,meta,index}
  const logs: string[] = inner?.meta?.logMessages ?? [];
  // v1: "Instruction: Buy|Sell" — v2 (2025/26): "Instruction: BuyV2|SellV2" via
  // het BondingCurveV3-wrapper-program (6Vo3245…). Zonder v2-herkenning werden
  // alle moderne pump-txns gemist → geen curve → nooit geprijsd.
  const isBuy = logs.some((l) => l.includes('Program log: Instruction: Buy') && !l.includes('BuyV2') && !l.includes('BuyStable'));
  const isSell = logs.some((l) => l.includes('Program log: Instruction: Sell') && !l.includes('SellV2') && !l.includes('SellStable'));
  const isBuyV2 = logs.some((l) => /Program log: Instruction: BuyV2/.test(l));
  const isSellV2 = logs.some((l) => /Program log: Instruction: SellV2/.test(l));
  if (!isBuy && !isSell && !isBuyV2 && !isSellV2) return undefined;
  const kind: 'buy' | 'sell' = isBuy || isBuyV2 ? 'buy' : 'sell';
  const keys = ((inner?.transaction?.message?.accountKeys ?? []) as unknown[]).map(decodeKey);
  // pump buy/sell CPI is in the inner instructions (depth ≥1); find the CPI whose
  // program is the pump program and which carries ≥10 accounts (buy list).
  for (const group of inner?.meta?.innerInstructions ?? []) {
    for (const ix of group?.instructions ?? []) {
      const prog = keys[ix?.programIdIndex];
      if (prog !== PUMP) continue;
      const rawAccounts = ix?.accounts;
      if (!rawAccounts || rawAccounts.length < 10) continue;
      const idxs = Array.from(Buffer.isBuffer(rawAccounts) ? rawAccounts : Uint8Array.from(rawAccounts));
      const accs = idxs.map((i: number) => keys[i]);
      // pump buy/sell account order (extern geverifieerd, StackExchange/SDK):
      // [global(0), feeRecipient(1), mint(2), bondingCurve(3), associatedBondingCurve(4), …]
      // → de bonding curve is het account DIRECT NA de mint (+1). Het account vóór
      // de mint (−1) is de gedeelde feeRecipient — die oude code pakte de fee-wallet
      // i.p.v. de curve: pumps werden nooit geprijsd én firstMintPerCurve werd keyed
      // op de fee-wallet (massive discovery-loss na de eerste mint per fee-wallet).
      // v2-route: [mint(0), curve(1), tokenVault(2), …] — mint staat vóóraan.
      const mint = accs.find((a) => a && /pump$/i.test(a))
        ?? accs.find((a) => a && a !== 'So11111111111111111111111111111111111111112'
          && a !== 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
          && a !== 'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb'
          && a !== 'ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL'
          && a !== '11111111111111111111111111111111'
          && a !== '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P'
          && a !== '6Vo3245eszAb5wuqEMw8mGdbfRUdKbHhDHP5LcaGuTAB');
      if (!mint) continue;
      const mintIdx = accs.indexOf(mint);
      const curveIdx = mintIdx + 1;
      const curve = curveIdx < accs.length ? accs[curveIdx] : undefined;
      if (!curve || curve === mint) continue;
      // Sanity-check: de curve mag geen bekende systeem-account zijn (feeRecipient
      // is doorgaans een van de eerste accounts; de curve is altijd ≠ mint en
      // bevat doorgaans 'pump'-ongelabelde bytes). Verwerp feeRecipient (= accs[1])
      // expliciet zodat een verkeerde positie niet stilletjes de fee-wallet prijst.
      if (accs[1] && curve === accs[1]) continue;
      return { mint, curve, kind };
    }
  }
  return undefined;
}

const STABLES = new Set([
  'So11111111111111111111111111111111111111112', // WSOL
  'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', // USDC
  'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB', // USDT
]);

/**
 * Generieke multi-DEX swap-parse: haalt de verse base-mint uit een willekeurige
 * swap-txn (niet alleen pump). Mint = de niet-stable token-mint die in de
 * pre/postTokenBalances verandert. Programma-lijn herkend via de top-level
 * instruction programIdIndex. Dit dekt Raydium AMM/CPMM/CLMM, Meteora, Orca,
 * PumpSwaps, Moonshot — de 9 programma's uit LAUNCH_PROGRAMS.
 */
export function parseGenericSwap(payload: any): { mint: string; program: string; kind: 'buy' | 'sell' | undefined; priceLamportsPerToken?: number; baseRawDelta?: number; wsolRawDelta?: number; baseDecimalsForPrice?: number } | undefined {
  const outer = payload?.transaction;
  const inner = outer?.transaction ?? outer;
  const logs: string[] = inner?.meta?.logMessages ?? [];
  if (!logs.some((l) => /Program log: Instruction: (Swap|Swap2|BuyV2|SellV2|BuyStable|SellStable|Buy|Sell)/.test(l))) return undefined;
  // program-lijn uit top-level instructions (programIdIndex → accountKeys)
  const keys = ((inner?.transaction?.message?.accountKeys ?? []) as unknown[]).map(decodeKey);
  const topIx = (inner?.transaction?.message?.instructions ?? [])[0];
  const program = topIx ? keys[topIx.programIdIndex] : undefined;
  // mint uit token-balances: eerste niet-stable mint die verscheen (verse launch)
  const mintKeys = new Set<string>();
  for (const b of [...(inner?.meta?.preTokenBalances ?? []), ...(inner?.meta?.postTokenBalances ?? [])]) {
    const m = b?.mint;
    if (typeof m === 'string' && !STABLES.has(m)) mintKeys.add(m);
  }
  // kies de kleinste/mint-achtige (niet-WSOL, liefst eindigend op 'pump' of klein)
  const picked = [...mintKeys].sort((a, b) => a.length - b.length)[0];
  if (!picked) return undefined;
  // buy/sell afleiden uit de WSOL balance-delta van de gebruiker: meer WSOL na
  // de swap = verkocht (sell), minder = gekocht (buy). Fallback: undefined.
  let kind: 'buy' | 'sell' | undefined;
  // execution-price (SOL lamports per base-token eenheid) uit de txn-balances —
  // ROBUUST voor v1 én v2 (geen curve-account-decode nodig, die is onbetrouwbaar
  // door wisselende account-volgordes). De exacte swap-hoeveelheid zit in de
  // pre/postTokenBalances van én de base-token-account én het WSOL-account.
  let priceLamportsPerToken: number | undefined;
  let baseRawDelta: number | undefined;
  let wsolRawDelta: number | undefined;
  let baseDecimalsForPrice: number | undefined;
  try {
    const preTb = new Map<string, { amount: number; decimals: number; mint?: string }>();
    for (const b of inner?.meta?.preTokenBalances ?? []) {
      if (!b || b.accountIndex === undefined) continue;
      // key = `accountIndex:mint` — consistent met de post-loop lookups
      preTb.set(`${b.accountIndex}:${b.mint ?? ''}`, { amount: b.uiTokenAmount?.amount ? Number(b.uiTokenAmount.amount) : 0, decimals: b.uiTokenAmount?.decimals ?? 0, mint: b.mint });
    }
    // Token-delta van de gepickte mint (trader-ATA: kleinste niet-nul delta).
    let minTokenDelta: number | undefined;
    let minDecimals = 0;
    for (const b of inner?.meta?.postTokenBalances ?? []) {
      if (!b || b.mint !== picked) continue;
      const key = `${b.accountIndex}:${b.mint}`;
      const preAmt = preTb.get(key)?.amount ?? 0;
      const postAmt = b.uiTokenAmount?.amount ? Number(b.uiTokenAmount.amount) : 0;
      const delta = Math.abs(postAmt - preAmt);
      if (delta > 0 && (minTokenDelta === undefined || delta < minTokenDelta)) {
        minTokenDelta = delta;
        minDecimals = b.uiTokenAmount?.decimals ?? 0;
      }
    }
    // SOL-delta uit de NATIVE lamport-balances (preBalances/postBalances). Pump
    // gebruikt GEEN WSOL-token-account — de SOL beweegt als native lamports.
    let wsolTokenDelta = 0;
    // WSOL-token-pad (sommige DEX zoals Raydium gebruiken WSOL) — valideer beide.
    for (const b of inner?.meta?.postTokenBalances ?? []) {
      if (!b || b.mint !== 'So11111111111111111111111111111111111111112' || b.accountIndex === undefined) continue;
      const preAmt = preTb.get(`${b.accountIndex}:${b.mint}`)?.amount ?? 0;
      const postAmt = b.uiTokenAmount?.amount ? Number(b.uiTokenAmount.amount) : 0;
      const d = Math.abs(postAmt - preAmt);
      if (d > wsolTokenDelta) wsolTokenDelta = d;
    }
    let wsolDelta = wsolTokenDelta;
    // native-SOL-Δ: neem de GROOTSTE absoluut SOL-delta over ALLE accounts. De
    // trader (signer) is een ánder account dan hun token-account (die index-koppeling
    // faalde). Bij een sell ontvangt de gebruiker SOL (curve geeft SOL); bij een buy
    // geeft de gebruiker SOL. De grootste beweging = de swap-omvang.
    const preSol = (inner?.meta?.preBalances ?? []) as number[];
    const postSol = (inner?.meta?.postBalances ?? []) as number[];
    let bestSolDelta = 0;
    let bestSolSign = 0;
    for (let i = 0; i < preSol.length; i++) {
      const d = (postSol[i] ?? 0) - (preSol[i] ?? 0);
      if (Math.abs(d) > bestSolDelta) { bestSolDelta = Math.abs(d); bestSolSign = d; }
    }
    if (wsolTokenDelta > 0) {
      // WSOL-token-pad (Raydium/CPMM/CLMM): netto WSOL-token-delta. Meer WSOL na
      // swap = verkoper ontvangt = SELL; minder = BUY.
      let netWsol = 0;
      for (const b of inner?.meta?.postTokenBalances ?? []) {
        if (!b || b.mint !== 'So11111111111111111111111111111111111111112' || b.accountIndex === undefined) continue;
        const preAmt = preTb.get(`${b.accountIndex}:${b.mint}`)?.amount ?? 0;
        const postAmt = b.uiTokenAmount?.amount ? Number(b.uiTokenAmount.amount) : 0;
        netWsol += (postAmt - preAmt);
      }
      kind = netWsol > 0 ? 'sell' : 'buy';
    } else if (bestSolDelta > 0) {
      // Native-SOL-pad (pump.fun): de curve/relayer heeft de grootste Δ. Bij een
      // SELL geeft de curve SOL (negatief, de verkoper wordt uitbetaald); bij een
      // BUY ontvangt de curve SOL (positief).
      wsolDelta = Math.max(wsolDelta, bestSolDelta);
      kind = bestSolSign < 0 ? 'sell' : 'buy';
    }
    if (wsolDelta > 0) {
      // kind is al bepaald uit de richting van het GROOTSTE SOL-account (de
      // gebruiker): meer SOL = SELL, minder = BUY. Geen verdere override.
      if (minTokenDelta && minTokenDelta > 0) {
        // base-hoeveelheid in "token-eenheden" (10^decimals): execute-prijs =
        // SOL-lamports ÷ (token-units / 10^decimals) → SOL per token.
        const tokenUnits = minTokenDelta / (10 ** minDecimals);
        const solOut = wsolDelta / 1e9;
        if (tokenUnits > 0) priceLamportsPerToken = (solOut / tokenUnits) * 1e9;
        // ruwe deltas voor een synthetische PoolDepth met echte liquiditeit-
        // ondergrens (swap-omvang = minimale pool-diepte, niet inflatief).
        baseRawDelta = minTokenDelta;
        wsolRawDelta = wsolDelta;
        baseDecimalsForPrice = minDecimals;
      }
    }
  } catch {
    kind = undefined;
  }
  return { mint: picked, program: program ?? '', kind, priceLamportsPerToken, baseRawDelta, wsolRawDelta, baseDecimalsForPrice };
}

/** Build a client that maps raw geyser messages to VixenUpdate-shaped payloads. */
export function createGeyserClientFactory(hostOverride?: string, tokenOverride?: string): TritonClientFactory {
  return (endpoint: string, token: string) => {
    const host = hostOverride ?? endpoint;
    const apiToken = tokenOverride ?? token;
    // SDK expects scheme-qualified endpoint for TLS (e.g. https://host:443);
    // accept both bare host and URL form.
    const grpcEndpoint = /^https?:\/\//.test(host) ? host.replace(/^https?/, 'https') : `https://${host}`;
    const client = new GeyserClient(grpcEndpoint, apiToken, {}, { enabled: true });
    // Eager connect: de SDK vereist dat connect() afgerond is vóór subscribe().
    // Houd de promise vast zodat Subscribe wacht op de eerste verbinding.
    let connectPromise: Promise<void> | undefined = undefined;

    // One raw stream covers all programs; publish per-program subscriptions.
    const subscribers = new Map<string, Subscriber>();
    let rawStream: any;
    let started = false;

    const start = () => {
      if (started) return;
      started = true;
      if (!connectPromise) connectPromise = client.connect();
      const request = {
        accounts: {},
        slots: { slotMonitor: { filterByCommitment: true } },
        transactions: {
          launchPrograms: {
            accountInclude: LAUNCH_PROGRAMS,
            accountExclude: [],
            accountRequired: [],
          },
        },
        transactionsStatus: {},
        blocks: {},
        blocksMeta: {},
        entry: {},
        commitment: COMMITMENT_PROCESSED,
        accountsDataSlice: [],
        ping: undefined,
        fromSlot: undefined,
      };
      // ── Onafhankelijke herconnect-loop (structurele fix 2026-08-11) ──
      // De SDK auto-reconnect herstelt alleen de transport, NIET de subscribe-stream.
      // Als de subscribe-stroom stilvalt (timeout/error/na lange idle) was dat eerder
      // een doodbloedende discovery (0 events, streams:error) zonder herstel. Deze
      // loop: heartbeat-timer (re-subscribe bij ≥60s stilte) + error/close-trigger
      // + backoff + cap op gelijktijdige loops.
      let loopActive = false;
      let heartbeat: ReturnType<typeof setInterval> | undefined;
      const rearm = () => { if (heartbeat) clearInterval(heartbeat); };
      const stopHeartbeat = () => { if (heartbeat) { clearInterval(heartbeat); heartbeat = undefined; } };
      const runLoop = async (first: boolean) => {
        if (loopActive) return;
        loopActive = true;
        stopHeartbeat();
        if (!first) {
          await new Promise((r) => setTimeout(r, 2000)); // backoff vóór re-subscribe
        } else {
          await connectPromise;
        }
        try {
          const stream = await client.subscribe(request);
          rawStream = stream;
          let lastDataAt = Date.now();
          stream.on('data', (msg: any) => {
            lastDataAt = Date.now();
            // rearm heartbeat: stilte-meting opnieuw vanaf élk datagram
            if (!heartbeat) {
              heartbeat = setInterval(() => {
                if (Date.now() - lastDataAt > 60_000) {
                  // te lang geen data → stream is vermoedelijk dood → force re-subscribe
                  try { if (typeof stream?.cancel === 'function') stream.cancel(); } catch { /* ignore */ }
                  void runLoop(false);
                }
              }, 30_000);
            }
            let update: VixenUpdate | undefined;
            const pump = msg?.transaction ? parsePumpTxn(msg) : undefined;
            // Systematic-debugging: capture failure-redenen (enabled via DEBUG_PARSE_PUMP)
            if (!pump && msg?.transaction) {
              const outer = msg.transaction;
              const inner = outer?.transaction ?? outer;
              const logs: string[] = inner?.meta?.logMessages ?? [];
              const hasDiscLog = logs.some((l) => /Program log: Instruction: (Buy|Sell|BuyV2|SellV2)/.test(l));
              const lowerLogs = logs.join(' ').toLowerCase();
              const noPumpLog = !lowerLogs.includes('program log: instruction: buy') && !lowerLogs.includes('program log: instruction: sell');
              const keys = ((inner?.transaction?.message?.accountKeys ?? []) as unknown[]).map(decodeKey);
              const noPumpInKeys = !keys.some((k?: string) => k === '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P');
              if (noPumpLog && noPumpInKeys) recordPumpParseFailure(msg, 'no_pump_log_or_keys');
              else if (noPumpLog) recordPumpParseFailure(msg, 'log_missing_instruction');
              else {
                // logs matchen maar parsePumpTxn returned undefined — mogelijk account layout
                let reasons: string[] = [];
                for (const group of inner?.meta?.innerInstructions ?? []) {
                  for (const ix of group?.instructions ?? []) {
                    const rawAccts = ix?.accounts;
                    const cnt = Buffer.isBuffer(rawAccts) ? rawAccts.length : (rawAccts?.length ?? 0);
                    const progKey = keys[ix?.programIdIndex ?? 0];
                    if (progKey === '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P' && cnt > 0) reasons.push(`pump_cpi_${cnt}accts`);
                  }
                }
                recordPumpParseFailure(msg, reasons.length ? `log_ok_but_${reasons.join('|')}` : 'log_ok_no_pump_cpi');
              }
            }
            if (pump) {
              const known = firstMintPerCurve.get(pump.curve);
              if (known !== undefined && known !== pump.mint) return;
              firstMintPerCurve.set(pump.curve, pump.mint);
              const u = { [pump.kind]: { accounts: { mint: pump.mint, bondingCurve: pump.curve } } } as VixenUpdate;
              for (const s of subscribers.values()) {
                if (s.program === PUMP && s.listener) s.listener(u);
              }
            }
            const generic = msg?.transaction ? parseGenericSwap(msg) : undefined;
            if (generic?.mint) {
              // Pump-mints (…pump): neem de bonding curve uit dezelfde txn mee,
              // zodat onGenericLaunchUpdate de curve-reserves kan prijzen (i.p.v.
              // een prijsloze identiteit die op liquidity_below_minimum crasht).
              const curve = pump?.curve && pump.mint === generic.mint ? pump.curve : undefined;
              const u = { genericLaunch: { mint: generic.mint, curve, kind: generic.kind, priceLamportsPerToken: generic.priceLamportsPerToken, baseRawDelta: generic.baseRawDelta, wsolRawDelta: generic.wsolRawDelta, baseDecimalsForPrice: generic.baseDecimalsForPrice } } as VixenUpdate;
              for (const s of subscribers.values()) {
                if (s.listener && (s.program === generic.program || generic.program === '')) s.listener(u);
              }
            }
            void update;
          });
          stream.on('error', (e: any) => {
            for (const s of subscribers.values()) if (s.errorListener) s.errorListener(e instanceof Error ? e : new Error(String(e)));
            stopHeartbeat();
            loopActive = false; // maak guard vrij zodat re-subscribe kan starten
            void runLoop(false); // fout → re-subscribe
          });
          stream.on('close', () => {
            stopHeartbeat();
            loopActive = false; // maak guard vrij
            void runLoop(false);
          });
          loopActive = false; // deze verbinding is actief; ruim de guard op zodat
          // error/close/heartbeat een NIEUWE runLoop kunnen starten
        } catch (e: any) {
          for (const s of subscribers.values()) if (s.errorListener) s.errorListener(e instanceof Error ? e : new Error(`geyser subscribe/connect: ${String(e)}`));
          loopActive = false;
          setTimeout(() => void runLoop(false), 3000); // mislukte connect → retry
        }
      };
      void runLoop(true);
    };

    const clientLike: TritonClientLike = {
      Subscribe(request: { program: string }) {
        const sub: Subscriber = { program: request.program };
        subscribers.set(request.program, sub);
        start();
        const adapter: TritonStreamLike = {
          on(event: 'data' | 'error', listener: (arg: VixenUpdate | Error) => void) {
            if (event === 'data') sub.listener = listener as (value: VixenUpdate) => void;
            if (event === 'error') sub.errorListener = listener as (error: Error) => void;
            return adapter;
          },
          cancel() {
            subscribers.delete(request.program);
          },
        };
        return adapter;
      },
    };
    return clientLike;
  };
}

/* Snapshot voor debug-exposure (systematic-debugging capture-hook). */
export function getPumpParseFailures() { return pumpParseFailures.slice(); }