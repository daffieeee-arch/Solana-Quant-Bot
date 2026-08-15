import { describe, expect, it } from 'vitest';
import { createHash } from 'node:crypto';
import { PUMP_DISCRIMINATORS, deriveBondingCurve, parsePumpSwap } from '../src/pump-parser.js';
const disc = (name: string) => createHash('sha256').update(`global:${name}`).digest().subarray(0, 8).toString('hex');

// Echte geldige pump-mints (geldige 32-byte base58 pubkeys)
const MINT = 'C9cAPKjWG8dsujybrn6LhXnTxAx3Y6Z9HVtrfQ1Cn8Hy';
const USER = 'g6iB2cJB3nt4zdXdgWAwq4dXGFEK9bRip6vh5YzBbro';
const FEE_RCPT = '7XZdzfpY31KRp18UdERdaQ8LGjowd8MekGdMD8JJ4i7J';
const PUMP = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P';
// CURVE = echte derived bonding-curve PDA van MINT (structurele PDA-relatie)
const CURVE = deriveBondingCurve(MINT);

function payload(discName: string, acctCount: number, opts?: { noLogs?: boolean; program?: string }): unknown {
  const program = opts?.program ?? PUMP;
  const keys = [program, FEE_RCPT, MINT, CURVE, USER];
  const data = Buffer.concat([Buffer.from(disc(discName), 'hex'), Buffer.alloc(40)]);
  return {
    transaction: {
      transaction: {
        transaction: { message: { accountKeys: keys } },
        meta: {
          logMessages: opts?.noLogs ? [] : [`Program log: Instruction: Buy`],
          innerInstructions: [{ instructions: [{ programIdIndex: 0, accounts: Array.from({ length: acctCount }, (_, i) => i), data }] }],
        },
      },
    },
  };
}

describe('pump-parser structureel (discriminator/account-gebaseerd, log-matching niet primair)', () => {
  it('officiële discriminators kloppen (buy/sell + v2 met underscore + exact-quote-in)', () => {
    expect(disc('buy')).toBe('66063d1201daebea');
    expect(disc('sell')).toBe('33e685a4017f83ad');
    // v2-varianten gebruiken UNDERSCORE (official IDL) — hyphen is fout
    expect(disc('buy_v2')).toBe('b817ee6167c5d33d');
    expect(disc('sell_v2')).toBe('5df6823ce7e940b2');
    expect(PUMP_DISCRIMINATORS.buy).toBe('66063d1201daebea');
    expect(PUMP_DISCRIMINATORS.sell).toBe('33e685a4017f83ad');
    expect(PUMP_DISCRIMINATORS.buyV2).toBe(disc('buy_v2'));
    expect(PUMP_DISCRIMINATORS.sellV2).toBe(disc('sell_v2'));
    expect(PUMP_DISCRIMINATORS.buyExactQuoteInV2).toBe('c2ab1c46684d5b2f');
    expect(PUMP_DISCRIMINATORS.buyExactSolIn).toBe('38fc74089edfcd5f');
  });

  it('bonding-curve PDA afleidbaar/deterministisch uit seeds', () => {
    const c = deriveBondingCurve(MINT);
    expect(c).toBeTruthy();
    expect(c).not.toBe(MINT);
    expect(c.length).toBeGreaterThan(30);
    expect(deriveBondingCurve(MINT)).toBe(c);
    expect(deriveBondingCurve(MINT)).not.toBe(deriveBondingCurve('OtherMintpump'));
  });

  it('v1 buy via discriminator (dummy logs aanwezig, discriminor wint)', () => {
    const r = parsePumpSwap(payload('buy', 10));
    expect(r).toBeTruthy();
    expect(r!.kind).toBe('buy');
    expect(r!.mint).toBe(MINT);
    expect(r!.curve).toBe(CURVE);
  });

  it('buy_v2 discriminator (officiële underscore) herkend', () => {
    const r = parsePumpSwap(payload('buy_v2', 27));
    expect(r).toBeTruthy();
    expect(r!.kind).toBe('buy');
  });

  it('buy_exact_quote_in_v2 discriminator herkend', () => {
    const r = parsePumpSwap(payload('buy_exact_quote_in_v2', 27));
    expect(r).toBeTruthy();
    expect(r!.kind).toBe('buy');
  });

  it('buy_exact_sol_in discriminator herkend', () => {
    const r = parsePumpSwap(payload('buy_exact_sol_in', 16));
    expect(r).toBeTruthy();
    expect(r!.kind).toBe('buy');
  });

  it('sell via discriminator herkend', () => {
    const r = parsePumpSwap(payload('sell', 9));
    expect(r).toBeTruthy();
    expect(r!.kind).toBe('sell');
  });

  it('onbekende discriminator → undefined', () => {
    expect(parsePumpSwap(payload('create', 10))).toBeUndefined();
  });

  it('niet-Pump programma wordt niet geparsed', () => {
    expect(parsePumpSwap(payload('buy', 10, { program: '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8' }))).toBeUndefined();
  });
});