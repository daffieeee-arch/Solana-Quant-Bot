import { buildPumpIdentity, buildAmmIdentity, type MarketIdentity } from './market-identity2.js';

/** Fase-LIVE: MarketIdentity upstream-wiring.
 *  Bouwt de protocol-specifieke identity rechtstreeks uit de reeds-gedecodeerde
 *  Vixen/geyser-event-data — GEEN tweede parallelle parser. Wanneer een canonical
 *  pool/curve/vault-identiteit in het event ontbreekt → identity = undefined
 *  (geen fallback naar gx:<mint>). */

export type UpstreamIdentityResult =
  | { identity: MarketIdentity }
  | { identity: undefined; reason: string };

/** Pump.fun bonding-curve uit Vixen pump/genericLaunch event (curve-adres). */
export function buildPumpIdentityFromDecode(i: {
  tradeId: string; mint: string; programId: string; curve: string; relatedCurveVault?: string;
  baseDecimals: number; quoteDecimals: number; sourceTimestamp: string; entryPriceSource: MarketIdentity['entryPriceSource']; strategyVersion?: string;
}): MarketIdentity | null {
  if (!i.curve || i.curve.length < 10 || i.curve.toLowerCase() === `gx:${i.mint.toLowerCase()}`) return null;
  return buildPumpIdentity({
    tradeId: i.tradeId, mint: i.mint, programId: i.programId || '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P',
    curve: i.curve, relatedCurveVault: i.relatedCurveVault, baseDecimals: i.baseDecimals, quoteDecimals: i.quoteDecimals,
    sourceTimestamp: i.sourceTimestamp, entryPriceSource: i.entryPriceSource, strategyVersion: i.strategyVersion ?? 'contra', schemaVersion: 1,
  });
}

/** AMMv4/CPMM uit event (vaults + lpMint). */
export function buildAmmIdentityFromDecode(i: {
  tradeId: string; mint: string; programId: string; marketId: string; baseVault: string; quoteVault: string;
  baseMint?: string; baseDecimals: number; quoteDecimals: number; sourceTimestamp: string; entryPriceSource: MarketIdentity['entryPriceSource']; strategyVersion?: string;
}): MarketIdentity | null {
  if (!i.baseVault || !i.quoteVault) return null;
  return buildAmmIdentity({
    tradeId: i.tradeId, mint: i.mint, programId: i.programId, marketId: i.marketId, baseVault: i.baseVault, quoteVault: i.quoteVault,
    baseMint: i.baseMint, baseDecimals: i.baseDecimals, quoteDecimals: i.quoteDecimals,
    sourceTimestamp: i.sourceTimestamp, entryPriceSource: i.entryPriceSource, strategyVersion: i.strategyVersion ?? 'contra', schemaVersion: 1,
  });
}

/** Generieke multi-DEX txn (CLMM/Meteora/Orca etc.): alleen mint — geen canonical
 *  pool/curve/vault-identiteit in het event. Retourneert identity=undefined met
 *  reden; NOOIT gx:<mint>-fallback. */
export function buildIdentityFromGeneric(i: {
  tradeId: string; mint: string; curve?: string; programId: string; baseDecimals: number; quoteDecimals: number; sourceTimestamp: string; entryPriceSource: MarketIdentity['entryPriceSource']; strategyVersion?: string;
}): UpstreamIdentityResult {
  // als de generieke txn een expliciet curve-adres meeleverde én het een pump-mint
  // is, kunnen we een pump identity bouwen
  if (i.curve && i.curve.length >= 10 && !i.curve.toLowerCase().startsWith('gx:')) {
    const pump = buildPumpIdentityFromDecode({ tradeId: i.tradeId, mint: i.mint, programId: i.programId, curve: i.curve, baseDecimals: i.baseDecimals, quoteDecimals: i.quoteDecimals, sourceTimestamp: i.sourceTimestamp, entryPriceSource: i.entryPriceSource, strategyVersion: i.strategyVersion });
    if (pump) return { identity: pump };
  }
  // geen pool/curve/vault-identiteit → undefined (b.v. CLMM zonder tick/pool in event)
  return { identity: undefined, reason: 'no_canonical_pool_identity_in_event' };
}
