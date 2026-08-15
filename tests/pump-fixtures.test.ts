import { describe, expect, it } from 'vitest';
import { PUMP_FIXTURES, fixturePayload, FIXTURE_MINT, FIXTURE_CURVE } from './fixtures/pump-real-shape.js';
import { parsePumpSwap } from '../src/pump-parser.js';

describe('real-shape Pump fixturecorpus (IDL commit 3c6721a)', () => {
  it('corpus dekking: 13 fixtures met provenance', () => {
    expect(PUMP_FIXTURES.length).toBe(13);
    const accepts = PUMP_FIXTURES.filter((f) => f.expected);
    const rejects = PUMP_FIXTURES.filter((f) => f.expected === undefined);
    expect(accepts.length).toBe(10); // buy/sell/v2/exact/geen-logs/top-level + 2 live-mainnet
    expect(rejects.length).toBe(3); // unknown/mismatch/non-pump
  });

  it('accepteer-varianten parsen correct met structurele parser (discriminor-gedreven)', () => {
    for (const f of PUMP_FIXTURES.filter((x) => x.expected)) {
      const payload = fixturePayload(f);
      const r = parsePumpSwap(payload);
      expect(r, `fixture ${f.name} moet ${f.expected} geven`).toBeTruthy();
      expect(r!.kind, `fixture ${f.name} kind`).toBe(f.expected);
      expect(r!.mint, `fixture ${f.name} mint`).toBe(FIXTURE_MINT);
      expect(r!.curve, `fixture ${f.name} curve (derived PDA)`).toBe(FIXTURE_CURVE);
    }
  });

  it('reject-varianten fail-closed (undefined)', () => {
    for (const f of PUMP_FIXTURES.filter((x) => x.expected === undefined)) {
      const r = parsePumpSwap(fixturePayload(f));
      expect(r, `fixture ${f.name} moet undefined zijn`).toBeUndefined();
    }
  });

  it('verschillende accountlayouts (v1 16, v2 27, v3 quote-in 27, sol-in 16) ongebroken', () => {
    for (const n of ['legacy_buy', 'buy_v2', 'buy_exact_quote_in_v2', 'buy_exact_sol_in', 'legacy_sell']) {
      const f = PUMP_FIXTURES.find((x) => x.name === n)!;
      const r = parsePumpSwap(fixturePayload(f));
      expect(r, `fixture ${n}`).toBeTruthy();
    }
  });
});