import { describe, expect, it } from 'vitest';
import { parsePumpSwap, deriveBondingCurve, validateBondingCurve, PUMP_PROGRAM_ID, PUMP_DISCRIMINATORS } from '../src/pump-parser.js';
import { PUMP_FIXTURES, fixturePayload, FIXTURE_MINT, FIXTURE_CURVE } from './fixtures/pump-real-shape.js';
import { createHash } from 'node:crypto';
import { PublicKey } from '@solana/web3.js';

const mk = (label: string) => new PublicKey(createHash('sha256').update(label).digest()).toBase58();
const FEE = mk('fee-rt');
const USER = mk('user-rt');

function buildPayload(opts: { disc: string; accts: string[]; topLevel?: boolean; noLogs?: boolean; versioned?: boolean }): unknown {
  const instruction = { programIdIndex: 0, accounts: Array.from({ length: opts.accts.length }, (_, i) => i), data: Buffer.concat([Buffer.from(opts.disc, 'hex'), Buffer.alloc(40)]) };
  const message: Record<string, unknown> = { accountKeys: opts.accts };
  if (opts.versioned) { message.version = 0; message.addressTableLookups = []; }
  if (opts.topLevel) message.instructions = [instruction];
  return {
    transaction: { transaction: { transaction: { message }, meta: { logMessages: opts.noLogs ? [] : ['Program log: Instruction: Buy'], innerInstructions: opts.topLevel ? [] : [{ instructions: [instruction] }] } } },
  };
}

describe('offline replaycampagne (local fixtures only, geen netwerk)', () => {
  it('officiële IDL-varianten parsen (buy/sell/v2/exact + top-level + inner + no-logs)', () => {
    const cases: Array<{ name: string; disc: string; topLevel?: boolean; noLogs?: boolean }> = [
      { name: 'legacy_buy inner', disc: PUMP_DISCRIMINATORS.buy },
      { name: 'legacy_sell inner', disc: PUMP_DISCRIMINATORS.sell },
      { name: 'buy_v2 top-level', disc: PUMP_DISCRIMINATORS.buyV2, topLevel: true },
      { name: 'sell_v2 inner', disc: PUMP_DISCRIMINATORS.sellV2 },
      { name: 'exact_quote inner', disc: PUMP_DISCRIMINATORS.buyExactQuoteInV2 },
      { name: 'exact_sol inner no-logs', disc: PUMP_DISCRIMINATORS.buyExactSolIn, noLogs: true },
    ];
    for (const c of cases) {
      const accts = [PUMP_PROGRAM_ID, FEE, FIXTURE_MINT, FIXTURE_CURVE, USER];
      const p = buildPayload({ disc: c.disc, accts, topLevel: c.topLevel, noLogs: c.noLogs });
      const r = parsePumpSwap(p);
      expect(r, c.name).toBeTruthy();
      expect(r!.mint).toBe(FIXTURE_MINT);
      expect(r!.curve).toBe(FIXTURE_CURVE);
    }
  });

  it('versioned transaction + loaded addresses (structureel ongebroken)', () => {
    const accts = [PUMP_PROGRAM_ID, FEE, FIXTURE_MINT, FIXTURE_CURVE, USER];
    const p = buildPayload({ disc: PUMP_DISCRIMINATORS.buyV2, accts, versioned: true });
    const r = parsePumpSwap(p);
    expect(r).toBeTruthy();
  });

  it('observed-runtime-dispatchers slagen alleen met bewezen layouts', () => {
    for (const f of PUMP_FIXTURES.filter((x) => x.name.startsWith('live_'))) {
      const r = parsePumpSwap(fixturePayload(f));
      expect(r, f.name).toBeTruthy();
    }
    // observed-disc met ONJUISTE curve (niet derived-PDA in accounts) → fail-closed
    const bad = buildPayload({ disc: PUMP_DISCRIMINATORS.liveSell, accts: [PUMP_PROGRAM_ID, FEE, mk('wrong-mint'), mk('wrong-curve'), USER] });
    expect(parsePumpSwap(bad)).toBeUndefined();
  });

  it('unknown disc / verkeerde program-id / mint-curve mismatch / invalid layout / niet-pump → fail-closed', () => {
    const unknown = buildPayload({ disc: 'cc00000000000000', accts: [PUMP_PROGRAM_ID, FEE, FIXTURE_MINT, FIXTURE_CURVE, USER] });
    expect(parsePumpSwap(unknown)).toBeUndefined();
    const wrongProg = buildPayload({ disc: PUMP_DISCRIMINATORS.buy, accts: ['675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8', FEE, FIXTURE_MINT, FIXTURE_CURVE, USER] });
    expect(parsePumpSwap(wrongProg)).toBeUndefined();
    const mismatched = buildPayload({ disc: PUMP_DISCRIMINATORS.buy, accts: [PUMP_PROGRAM_ID, FEE, FIXTURE_MINT, mk('wrong-curve'), USER] });
    expect(parsePumpSwap(mismatched)).toBeUndefined();
    const invalid = buildPayload({ disc: PUMP_DISCRIMINATORS.buy, accts: [PUMP_PROGRAM_ID, 'short'] });
    expect(parsePumpSwap(invalid)).toBeUndefined();
  });

  it('PDA-validatie: derived exact-match; verkeerde curve of verkeerde program-id faalt', () => {
    const mintPk = new PublicKey(FIXTURE_MINT);
    const [pda] = PublicKey.findProgramAddressSync([Buffer.from('bonding-curve'), mintPk.toBuffer()], new PublicKey(PUMP_PROGRAM_ID));
    expect(validateBondingCurve(FIXTURE_MINT, pda.toBase58())).toBe(true);
    expect(validateBondingCurve(FIXTURE_MINT, deriveBondingCurve(mk('other')))).toBe(false);
    const [raydiumPda] = PublicKey.findProgramAddressSync([Buffer.from('bonding-curve'), mintPk.toBuffer()], new PublicKey('675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8'));
    expect(validateBondingCurve(FIXTURE_MINT, raydiumPda.toBase58())).toBe(false);
  });
});