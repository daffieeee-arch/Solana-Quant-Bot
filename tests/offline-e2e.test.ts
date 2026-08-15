import { describe, expect, it } from 'vitest';
import { parsePumpSwap, deriveBondingCurve, validateBondingCurve } from '../src/pump-parser.js';
import { buildPumpIdentityFromDecode } from '../src/market-identity-upstream.js';
import { evaluateEntryShadow } from '../src/entry-shadow.js';
import { createHash } from 'node:crypto';

const MINT = 'C9cAPKjWG8dsujybrn6LhXnTxAx3Y6Z9HVtrfQ1Cn8Hy';
// geldige extra accounts voor de accountlijst
const CURVE_ACCOUNT = '2Wh8by16GrzpMLFn51h52dHjMwzHguk8hXVB6kZBjAir';
const USER_ACCOUNT = 'g6iB2cJB3nt4zdXdgWAwq4dXGFEK9bRip6vh5YzBbro';
const PUMP = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P';
const disc = (n: string) => createHash('sha256').update(`global:${n}`).digest().subarray(0, 8).toString('hex');

function pumpEvent(discName: string): unknown {
  const curve = derivedCurve(); // echte officiële PDA — zet op de curve-positie
  const keys = [PUMP, FEE_ACCOUNT, MINT, curve, USER_ACCOUNT]; // v1-layout: [program, fee, mint, curve, user]
  return {
    transaction: { transaction: { transaction: { message: { accountKeys: keys } }, meta: { logMessages: [], innerInstructions: [{ instructions: [{ programIdIndex: 0, accounts: [0,1,2,3,4], data: Buffer.concat([Buffer.from(disc(discName), 'hex'), Buffer.alloc(40)]) }] }] } } },
  };
}
const FEE_ACCOUNT = '7XZdzfpY31KRp18UdERdaQ8LGjowd8MekGdMD8JJ4i7J';
function derivedCurve(): string { return deriveBondingCurve(MINT); }

describe('offline end-to-end: pump event → identity → candidate → shadow-gate (geen live Triton)', () => {
  it('recorded buy-event → structurele parse → canonical identity → WOULD_ACCEPT', () => {
    const ev = pumpEvent('buy');
    const parsed = parsePumpSwap(ev);
    expect(parsed).toBeTruthy();
    expect(parsed!.kind).toBe('buy');
    const id = buildPumpIdentityFromDecode({
      tradeId: 't1', mint: parsed!.mint, programId: PUMP, curve: parsed!.curve,
      baseDecimals: 6, quoteDecimals: 9, sourceTimestamp: new Date().toISOString(), entryPriceSource: 'STREAM',
    }) ?? undefined;
    expect(id).toBeTruthy();
    const v = evaluateEntryShadow({ identity: id, decimals: { base: 6, quote: 9 }, marketFreshMs: 0, nowMs: Date.now(), maxAgeMs: 120000 });
    expect(v.verdict).toBe('WOULD_ACCEPT');
  });

  it('sell_v2 event → structureel herkend als sell', () => {
    const parsed = parsePumpSwap(pumpEvent('sell_v2'));
    expect(parsed).toBeTruthy();
    expect(parsed!.kind).toBe('sell');
  });

  it('PDA-validatie: geparsde curve matcht derived; ongeldige faalt', () => {
    const parsed = parsePumpSwap(pumpEvent('buy'))!;
    expect(validateBondingCurve(parsed.mint, parsed.curve)).toBe(true);
    expect(validateBondingCurve(parsed.mint, 'WRONG')).toBe(false);
  });

  it('stale/interne identity-reject in shadow (fail-closed)', () => {
    const parsed = parsePumpSwap(pumpEvent('buy'))!;
    const id = buildPumpIdentityFromDecode({
      tradeId: 't2', mint: parsed.mint, programId: PUMP, curve: parsed.curve,
      baseDecimals: 6, quoteDecimals: 9, sourceTimestamp: new Date(Date.now() - 300000).toISOString(), entryPriceSource: 'STREAM',
    }) ?? undefined;
    const v = evaluateEntryShadow({ identity: id, decimals: { base: 6, quote: 9 }, marketFreshMs: 300000, nowMs: Date.now(), maxAgeMs: 120000 });
    expect(v.verdict).toBe('WOULD_REJECT');
    expect(v.reasonCode).toBe('stale_market_data');
  });
});