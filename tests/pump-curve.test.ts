import { describe, expect, it } from 'vitest';
import { decodePumpCurve, pumpCurveToDepth } from '../src/pump-curve.js';

function u64le(n: bigint): Buffer {
  const b = Buffer.alloc(8);
  b.writeBigUInt64LE(n);
  return b;
}

function buildCurveBase64(virtualToken: bigint, virtualSol: bigint, totalSupply: bigint): string {
  // mint(32) + creator(32) + virtual_token(8) + virtual_sol(8) + total_supply(8)
  const buf = Buffer.alloc(88);
  buf.fill(0);
  u64le(virtualToken).copy(buf, 64);
  u64le(virtualSol).copy(buf, 72);
  u64le(totalSupply).copy(buf, 80);
  return buf.toString('base64');
}

describe('Pump.fun bonding-curve decoder (Triton-first self-calc)', () => {
  it('decodes virtual reserves into a PoolDepth with WSOL quote', () => {
    // 2_000_000_000e6 token reserves? no: 1_000_000_000 (1B units) token, 30 SOL (3e10 lamports)
    const b64 = buildCurveBase64(1_000_000_000n, 30_000_000_000n, 1_000_000_000n);
    const curve = decodePumpCurve(b64);
    expect(curve).toBeDefined();
    expect(curve!.virtualTokenReserves).toBe(1_000_000_000);
    expect(curve!.virtualSolReserves).toBe(30_000_000_000);

    const depth = pumpCurveToDepth(curve!);
    expect(depth).toBeDefined();
    expect(depth!.quoteReserve).toBe(30_000_000_000); // SOL
    expect(depth!.baseReserve).toBe(1_000_000_000);   // token
    expect(depth!.quoteDecimals).toBe(9);
    expect(depth!.baseDecimals).toBe(6);
    expect(depth!.bondingCurve).toBe(true);
  });

  it('fails closed on non-positive reserves or short buffer', () => {
    expect(decodePumpCurve('')).toBeUndefined();
    const b64 = buildCurveBase64(0n, 0n, 0n);
    const curve = decodePumpCurve(b64);
    expect(pumpCurveToDepth(curve!)).toBeUndefined();
  });
});
