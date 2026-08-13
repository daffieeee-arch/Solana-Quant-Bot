import type { PaperPosition } from './portfolio.js';

/** Fase-Q: canonical MarketIdentity-contract + fail-closed entry-gates.
 *  Bij elke papierpositie moet een volledige, persistente marktidentiteit bestaan
 *  zodat prijs/route/pool na discovery-uitval opnieuw te verkrijgen is. */

export type MarketIdentity = {
  /** canonical = tradeId (immutable, uniek, stabiel) */
  tradeId: string;
  mint: string;
  protocol: string;            // e.g. 'pump' | 'raydium' | 'meteora' | 'orca'
  programId: string;           // on-chain program
  marketType: 'pump_bonding_curve' | 'amm_v4' | 'clmm' | 'cpmm' | 'concentrated' | 'unknown';
  /** canonical pool/market/bonding-curve ID (niet gx:<mint>-only) */
  marketId: string;
  bondCurve?: string;
  vaults?: { base?: string; quote?: string };
  baseMint: string;
  quoteMint: string;
  baseDecimals: number;
  quoteDecimals: number;
  entryTxSignature?: string;
  entrySlot?: number;
  entryTimestamp: string;      // ISO
  marketEventTimestamp?: string;
  entryPriceSource: 'STREAM' | 'TITAN' | 'LOCAL_STATE' | 'REGISTRY' | 'RPC';
  entryPrice?: number;
  strategyVersion: string;
  schemaVersion: number;
};

/** Marktidentiteit voor een geldige Pump.fun bonding-curve wordt geaccepteerd
 *  zolang er een ECHT curve-adres is (niet gx:<mint>). */
export function hasUsableExitRoute(id: MarketIdentity): boolean {
  if (!id || !id.marketId) return false;
  // gx:<mint>-only (geen curve/vault) = geen uitvoerbare route
  if (id.marketId.startsWith('gx:') && id.marketId.toLowerCase().endsWith(id.mint.toLowerCase())) return false;
  if (id.marketType === 'pump_bonding_curve' && !id.bondCurve) return false;
  if (id.marketType.startsWith('amm') || id.marketType === 'clmm' || id.marketType === 'cpmm' || id.marketType === 'concentrated') {
    if (!id.vaults?.base && !id.marketId) return false;
  }
  if (id.baseDecimals === undefined || id.baseDecimals <= 0) return false;
  if (id.quoteDecimals === undefined || id.quoteDecimals <= 0) return false;
  return true;
}

export function validateMarketIdentity(id: MarketIdentity): boolean {
  if (!id) return false;
  if (!id.mint || !id.protocol || !id.programId) return false;
  if (!id.baseMint || !id.quoteMint) return false;
  if (!id.entryTimestamp || !id.entryPriceSource) return false;
  return hasUsableExitRoute(id);
}

export type EntryGateResult = { ok: true } | { ok: false; reason: string };

/** Fail-closed: canonical market identity + usable exit route vereist. */
export function requireCanonicalMarketIdentity(id: MarketIdentity | undefined): EntryGateResult {
  if (!id) return { ok: false, reason: 'missing_market_identity' };
  if (!validateMarketIdentity(id)) return { ok: false, reason: 'invalid_market_identity' };
  if (!hasUsableExitRoute(id)) return { ok: false, reason: 'no_usable_exit_route' };
  return { ok: true };
}

/** Fail-closed: recente market activity vereist (maxAgeMs grens). */
export function requireRecentMarketActivity(id: MarketIdentity, nowMs: number, maxAgeMs: number): EntryGateResult {
  const ref = id.marketEventTimestamp ? Date.parse(id.marketEventTimestamp) : Date.parse(id.entryTimestamp);
  if (!Number.isFinite(ref)) return { ok: false, reason: 'missing_market_timestamp' };
  const age = nowMs - ref;
  if (age > maxAgeMs) return { ok: false, reason: 'stale_market_data' };
  return { ok: true };
}

/** Builder — valideert dat we nooit een gx:<mint>-only identiteit bouwen. */
export function buildMarketIdentity(i: Omit<MarketIdentity, 'schemaVersion' | 'entryPriceSource' | 'strategyVersion'> & { entryPriceSource?: MarketIdentity['entryPriceSource']; strategyVersion?: string }): MarketIdentity | null {
  const id: MarketIdentity = { ...i, entryPriceSource: i.entryPriceSource ?? 'STREAM', strategyVersion: i.strategyVersion ?? 'unknown', schemaVersion: 1 } as MarketIdentity;
  if (!validateMarketIdentity(id)) return null;
  return id;
}

/** Toekomstig PaperPosition-veld: optionele market identity op de positie. */
export type PaperPositionWithIdentity = PaperPosition & { marketIdentity?: MarketIdentity };
