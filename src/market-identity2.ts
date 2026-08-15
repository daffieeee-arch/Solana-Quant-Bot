import { makeTradeId } from './portfolio.js';

/** Fase-QH: protocol-specifieke MarketIdentity (discriminated union).
 *  Ieder marketType verplicht zijn eigen identiteitsvelden; er is geen
 *  monolithisch contract dat voor alle markten dezelfde velden eist.
 *  gx:<mint> mag NOOIT als canonical market identity. */

export type PumpBondingCurveIdentity = {
  kind: 'pump_bonding_curve';
  tradeId: string;            // canonical = tradeId (immutable, uniek)
  mint: string;
  protocol: 'pump';
  programId: string;
  marketId: string;           // = bonding-curve adres
  bondCurve: string;
  relatedCurveVault?: string;
  baseMint: string;
  quoteMint: string;
  baseDecimals: number;
  quoteDecimals: number;
  sourceTimestamp: string;
  entryPriceSource: 'STREAM' | 'TITAN' | 'LOCAL_STATE' | 'REGISTRY' | 'RPC';
  markPriceSource: 'STREAM' | 'TITAN' | 'LOCAL_STATE' | 'REGISTRY' | 'RPC';
  boundedExitFallback: boolean;
  strategyVersion: string;
  schemaVersion: number;
};

export type AmmCpmmIdentity = {
  kind: 'amm_cpmm';
  tradeId: string;
  mint: string;
  protocol: 'raydium' | 'meteora' | 'orca' | string;
  programId: string;
  marketId: string;           // = canonical pool-state (AmmInfo/PoolState account)
  lpMint?: string;            // afzonderlijke LP-tokenmint (NIET de pool-id)
  baseVault: string;
  quoteVault: string;
  baseMint: string;
  quoteMint: string;
  baseDecimals: number;
  quoteDecimals: number;
  sourceTimestamp: string;
  entryPriceSource: PumpBondingCurveIdentity['entryPriceSource'];
  markPriceSource: PumpBondingCurveIdentity['markPriceSource'];
  boundedExitFallback: boolean;
  strategyVersion: string;
  schemaVersion: number;
};

export type ClmmIdentity = {
  kind: 'clmm';
  tradeId: string;
  mint: string;
  protocol: string;
  programId: string;
  marketId: string;           // = pool
  tokenVaultA: string;
  tokenVaultB: string;
  tickState: string;          // tick/price-state identiteit
  baseMint: string;
  quoteMint: string;
  baseDecimals: number;
  quoteDecimals: number;
  sourceTimestamp: string;
  entryPriceSource: PumpBondingCurveIdentity['entryPriceSource'];
  markPriceSource: PumpBondingCurveIdentity['markPriceSource'];
  boundedExitFallback: boolean;
  strategyVersion: string;
  schemaVersion: number;
};

export type MarketIdentity = PumpBondingCurveIdentity | AmmCpmmIdentity | ClmmIdentity;

const SOL = 'So11111111111111111111111111111111111111112';

export function buildPumpIdentity(i: {
  tradeId: string; mint: string; programId: string; curve: string; relatedCurveVault?: string;
  quoteMint?: string; baseDecimals: number; quoteDecimals: number; sourceTimestamp: string;
  entryPriceSource: PumpBondingCurveIdentity['entryPriceSource']; markPriceSource?: PumpBondingCurveIdentity['markPriceSource'];
  strategyVersion: string; schemaVersion: number;
}): PumpBondingCurveIdentity | null {
  // Reviewer-fix (deleg_5181dd26, 1a): verwerp ook de gx:<mint>-only-vorm in de
  // builder zelf — niet alleen op gate-niveau. Ontbrekende/korte curve of mint-
  // afgeleide identity → ongeldig (nooit canonical).
  if (!i.curve || i.curve.length < 10 || i.curve.toLowerCase() === `gx:${i.mint.toLowerCase()}`) return null;
  if (!i.mint || !i.programId || !i.baseDecimals || !i.quoteDecimals || !i.sourceTimestamp) return null;
  return {
    kind: 'pump_bonding_curve', tradeId: i.tradeId, mint: i.mint, protocol: 'pump',
    programId: i.programId, marketId: i.curve, bondCurve: i.curve,
    relatedCurveVault: i.relatedCurveVault, baseMint: i.mint, quoteMint: i.quoteMint ?? SOL,
    baseDecimals: i.baseDecimals, quoteDecimals: i.quoteDecimals, sourceTimestamp: i.sourceTimestamp,
    entryPriceSource: i.entryPriceSource, markPriceSource: i.markPriceSource ?? i.entryPriceSource,
    boundedExitFallback: true, strategyVersion: i.strategyVersion, schemaVersion: i.schemaVersion,
  };
}

export function buildAmmIdentity(i: {
  tradeId: string; mint: string; programId: string; marketId: string; baseVault: string; quoteVault: string;
  baseMint?: string; baseDecimals: number; quoteDecimals: number; sourceTimestamp: string; lpMint?: string;
  entryPriceSource: AmmCpmmIdentity['entryPriceSource']; strategyVersion: string; schemaVersion: number;
}): AmmCpmmIdentity | null {
  if (!i.marketId || !i.baseVault || !i.quoteVault) return null;
  if (i.marketId.toLowerCase() === i.mint.toLowerCase() || i.marketId.startsWith('gx:') || i.marketId === i.lpMint) return null;
  if (!i.baseDecimals || !i.quoteDecimals || !i.sourceTimestamp) return null;
  return {
    kind: 'amm_cpmm', tradeId: i.tradeId, mint: i.mint, protocol: 'raydium',
    programId: i.programId, marketId: i.marketId, lpMint: i.lpMint, baseVault: i.baseVault, quoteVault: i.quoteVault,
    baseMint: i.baseMint ?? i.mint, quoteMint: SOL, baseDecimals: i.baseDecimals, quoteDecimals: i.quoteDecimals,
    sourceTimestamp: i.sourceTimestamp, entryPriceSource: i.entryPriceSource, markPriceSource: i.entryPriceSource,
    boundedExitFallback: true, strategyVersion: i.strategyVersion, schemaVersion: i.schemaVersion,
  };
}

export function buildClmmIdentity(i: {
  tradeId: string; mint: string; programId: string; marketId: string; tokenVaultA: string; tokenVaultB: string; tickState: string;
  baseDecimals: number; quoteDecimals: number; sourceTimestamp: string;
  entryPriceSource: ClmmIdentity['entryPriceSource']; strategyVersion: string; schemaVersion: number;
}): ClmmIdentity | null {
  if (!i.marketId || !i.tokenVaultA || !i.tokenVaultB || !i.tickState) return null;
  if (i.marketId.startsWith('gx:') || i.marketId.toLowerCase() === i.mint.toLowerCase()) return null;
  if (!i.baseDecimals || !i.quoteDecimals || !i.sourceTimestamp) return null;
  return {
    kind: 'clmm', tradeId: i.tradeId, mint: i.mint, protocol: 'raydium', programId: i.programId,
    marketId: i.marketId, tokenVaultA: i.tokenVaultA, tokenVaultB: i.tokenVaultB, tickState: i.tickState,
    baseMint: i.mint, quoteMint: 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',
    baseDecimals: i.baseDecimals, quoteDecimals: i.quoteDecimals, sourceTimestamp: i.sourceTimestamp,
    entryPriceSource: i.entryPriceSource, markPriceSource: i.entryPriceSource,
    boundedExitFallback: true, strategyVersion: i.strategyVersion, schemaVersion: i.schemaVersion,
  };
}

export function isCompleteIdentity(id: MarketIdentity | null | undefined): boolean {
  if (!id) return false;
  if (id.marketId.startsWith('gx:') || id.marketId === id.mint) return false;
  if (!id.baseDecimals || !id.quoteDecimals || !id.sourceTimestamp) return false;
  if (id.kind === 'pump_bonding_curve') return Boolean(id.bondCurve && id.bondCurve.length >= 10);
  if (id.kind === 'amm_cpmm') return Boolean(id.baseVault && id.quoteVault);
  if (id.kind === 'clmm') return Boolean(id.tokenVaultA && id.tokenVaultB && id.tickState);
  return false;
}
