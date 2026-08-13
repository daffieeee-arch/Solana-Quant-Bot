import { describe, expect, it } from 'vitest';
import { DecimalsResolver } from '../src/decimals-resolver.js';

describe('decimals resolver', () => {
  it('gebruikt eerst reeds-gedecodeerde event/state decimals (geen RPC)', () => {
    const calls: string[] = [];
    const r = new DecimalsResolver(async (mints: string[]) => { calls.push(...mints); return new Map(); });
    const d = r.resolveKnown('M1', 6, 'M2', 9);
    expect(d).toEqual({ base: 6, quote: 9 });
    expect(calls.length).toBe(0);
  });

  it('valt terug op gebatchte getMultipleAccounts fallback, dedup + cached', async () => {
    let batchCalls = 0;
    const r = new DecimalsResolver(async (mints: string[]) => {
      batchCalls += 1;
      return new Map([[mints[0]!, 6], [mints[1]!, 9]]);
    });
    const d1 = await r.resolveWithRpc('M1', 'M2');
    expect(d1).toEqual({ base: 6, quote: 9 });
    expect(batchCalls).toBe(1);
    // tweede oproep → gecached, geen extra RPC
    await r.resolveWithRpc('M1', 'M2');
    expect(batchCalls).toBe(1);
    // gelijktijdige in-flight dedup
    await Promise.all([r.resolveWithRpc('M1', 'M2'), r.resolveWithRpc('M1', 'M2')]);
    expect(batchCalls).toBe(1);
  });

  it('ontbrekende decimals → undefined (fail-closed)', async () => {
    const r = new DecimalsResolver(async () => new Map());
    const d = await r.resolveWithRpc('UNKNOWN1', 'UNKNOWN2');
    expect(d).toBeUndefined();
  });
});
