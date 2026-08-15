import { describe, expect, it } from 'vitest';
import { createHash } from 'node:crypto';
import { PUMP_DISCRIMINATORS, deriveBondingCurve, parsePumpSwap } from '../src/pump-parser.js';

const disc = (name: string) => createHash('sha256').update(`global:${name}`).digest().subarray(0, 8).toString('hex');

const MINT = '2ZA8NQS7hx4Gpump2ZA8NQS7hx4GpumpXXXXpump';
const CURVE = '7VtfL8fvgNfhz17qKRMjzQEXgbdpnHHHQRh54R9jP2RJ';
const USER = '9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin';
const FEE_RCPT = 'CebN5WGQ4jvEPvsVrU4EoHEpgzqDtSsKVK8nEx1hbsYSW';
const PUMP = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P';

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
  it('officiële discriminators kloppen (buy/sell + v2)', () => {
    expect(disc('buy')).toBe('66063d1201daebea');
    expect(disc('sell')).toBe('33e685a4017f83ad');
    expect(PUMP_DISCRIMINATORS.buy).toBe('66063d1201daebea');
    expect(PUMP_DISCRIMINATORS.sell).toBe('33e685a4017f83ad');
    expect(PUMP_DISCRIMINATORS.buyV2).toBe(disc('buy-v2'));
    expect(PUMP_DISCRIMINATORS.sellV2).toBe(disc('sell-v2'));
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

  it('buy-v2 discriminator (BondingCurveV3) herkend', () => {
    const r = parsePumpSwap(payload('buy-v2', 12));
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