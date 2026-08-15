import { describe, expect, it } from 'vitest';
import { parsePumpSwap, PUMP_DISCRIMINATORS, PUMP_PROGRAM_ID, deriveBondingCurve } from '../src/pump-parser.js';
import { buildPumpIdentityFromDecode } from '../src/market-identity-upstream.js';
import { evaluateEntryShadow } from '../src/entry-shadow.js';
import { computeExposure } from '../src/capital-accounting.js';
import { createHash } from 'node:crypto';
import { PublicKey } from '@solana/web3.js';
import type { PaperPosition } from '../src/portfolio.js';

const mk = (label: string) => new PublicKey(createHash('sha256').update(label).digest()).toBase58();
const MINT = mk('slice-mint');
const FEE = mk('slice-fee');
const USER = mk('slice-user');
const CURVE = deriveBondingCurve(MINT);

function pumpTx(discHex: string): unknown {
  const accts = [PUMP_PROGRAM_ID, FEE, MINT, CURVE, USER];
  const ix = { programIdIndex: 0, accounts: [0, 1, 2, 3, 4], data: Buffer.concat([Buffer.from(discHex, 'hex'), Buffer.alloc(40)]) };
  return { transaction: { transaction: { transaction: { message: { accountKeys: accts } }, meta: { logMessages: [], innerInstructions: [{ instructions: [ix] }] } } } };
}
function pos(tradeId: string): PaperPosition {
  return { tradeId, pairId: `gx:${MINT}`, mint: MINT, symbol: 'SLICE', openedAt: new Date().toISOString(), entryPriceUsd: 1e-6, highPriceUsd: 1e-6, allocatedLamports: 1000000, quoteMint: 'So11111111111111111111111111111111111111112', baseDecimals: 6, quoteDecimals: 9, kind: 'buy', source: 'shadow' };
}

describe('volledige offline vertical slice (geen netwerkfallback, deterministisch)', () => {
  it('pump tx → decode → identity → shadow verdict → exposure/accounting', () => {
    const parsed = parsePumpSwap(pumpTx(PUMP_DISCRIMINATORS.buy));
    expect(parsed).toBeTruthy();
    const id = buildPumpIdentityFromDecode({ tradeId: 't1', mint: parsed!.mint, programId: PUMP_PROGRAM_ID, curve: parsed!.curve, baseDecimals: 6, quoteDecimals: 9, sourceTimestamp: new Date().toISOString(), entryPriceSource: 'STREAM' }) ?? undefined;
    expect(id).toBeTruthy();
    const v = evaluateEntryShadow({ identity: id, decimals: { base: 6, quote: 9 }, marketFreshMs: 0, nowMs: Date.now(), maxAgeMs: 120000 });
    expect(v.verdict).toBe('WOULD_ACCEPT');
    // actieve positie → actieve exposure
    const active = computeExposure({ active: [pos('t1')], quarantined: [], availableLamports: 1_000_000_000, realizedPnlLamports: 0 });
    expect(active.activeExposureLamports).toBe(1_000_000);
    // quarantaine → UNKNOWN exposure, géén realized
    const q = computeExposure({ active: [], quarantined: [pos('t1')], availableLamports: 1_000_000_000, realizedPnlLamports: 0 });
    expect(q.quarantinedExposureLamports).toBe(1_000_000);
    expect(q.unknownExposureLamports).toBe(1_000_000);
    expect(q.realizedCapitalLamports).toBe(0);
  });

  it('stale identity → WOULD_REJECT (stale_market_data)', () => {
    const parsed = parsePumpSwap(pumpTx(PUMP_DISCRIMINATORS.buy))!;
    const staleId = buildPumpIdentityFromDecode({ tradeId: 't2', mint: parsed.mint, programId: PUMP_PROGRAM_ID, curve: parsed.curve, baseDecimals: 6, quoteDecimals: 9, sourceTimestamp: new Date(Date.now() - 300_000).toISOString(), entryPriceSource: 'STREAM' }) ?? undefined;
    const v = evaluateEntryShadow({ identity: staleId, decimals: { base: 6, quote: 9 }, marketFreshMs: 300_000, nowMs: Date.now(), maxAgeMs: 120_000 });
    expect(v.verdict).toBe('WOULD_REJECT');
  });

  it('no-route / geen mark → ziehler gx-only identity undefined (fail-closed)', () => {
    const gx = { transaction: { transaction: { transaction: { message: { accountKeys: [PUMP_PROGRAM_ID, FEE, MINT, mk('x'), USER] } }, meta: { logMessages: [], innerInstructions: [] } } } };
    expect(parsePumpSwap(gx)).toBeUndefined();
  });

  it('duplicate event (retry) → deterministische zelfde output, geen dubbel', () => {
    const tx = pumpTx(PUMP_DISCRIMINATORS.buy);
    expect(parsePumpSwap(tx)).toEqual(parsePumpSwap(tx));
  });

  it('onbekende decimals (UNKNOWN) → fail-closed in shadow', () => {
    const parsed = parsePumpSwap(pumpTx(PUMP_DISCRIMINATORS.buy))!;
    const id = buildPumpIdentityFromDecode({ tradeId: 't3', mint: parsed.mint, programId: PUMP_PROGRAM_ID, curve: parsed.curve, baseDecimals: 0, quoteDecimals: 0, sourceTimestamp: new Date().toISOString(), entryPriceSource: 'STREAM' }) ?? undefined;
    const v = evaluateEntryShadow({ identity: id, decimals: { base: 0, quote: 0 } as never, marketFreshMs: 0, nowMs: Date.now(), maxAgeMs: 120_000 });
    expect(v.verdict).toBe('WOULD_REJECT');
  });
});