import type { PoolDepth } from './scoring.js';

/**
 * Pump.fun `BondingCurve` account decode (Triton-first, fully on-chain).
 *
 * Layout (see pump-public-docs IDL):
 *   offset 0   mint                : 32 bytes
 *   offset 32  creator             : 32 bytes
 *   offset 64  virtual_token_reserves (u64)
 *   offset 72  virtual_sol_reserves     (u64)
 *   offset 80  token_total_supply       (u64)
 *   offset 88  real_token_reserves      (u64)
 *   offset 96  real_sol_reserves        (u64)
 *   offset 104 open_timestamp          (i64)
 *
 * The bonding curve implements a virtual constant-product curve in WSOL terms:
 *   price (SOL per token) = virtualSolReserves / virtualTokenReserves.
 * We return those as a PoolDepth so the existing `spotPriceUsd` computes a real
 * USD price (base=None; the quote is WSOL, so callers pass SOL/USD as quote-unit).
 */

export type PumpCurveReserves = {
  virtualTokenReserves: number;
  virtualSolReserves: number;
  tokenTotalSupply: number;
};

/** Decode a Pump.fun bonding-curve base64 account back to virtual reserves (big-endian-safe, LE u64). */
export function decodePumpCurve(base64: string): PumpCurveReserves | undefined {
  let bytes: Uint8Array;
  try {
    bytes = new Uint8Array(Buffer.from(base64, 'base64'));
  } catch {
    return undefined;
  }
  // need at least through token_total_supply (offset 80 + 8 = 88); een account van
  // 80-87 bytes maakte `BigInt(bytes[i])` met undefined → TypeError (crash).
  if (bytes.length < 88) return undefined;
  const readU64 = (offset: number): number => {
    // read as BigInt to stay exact, then to number (reserves often below 2^53)
    let n = 0n;
    for (let i = offset + 7; i >= offset; i -= 1) n = (n << 8n) | BigInt(bytes[i]!);
    return Number(n);
  };
  const virtualTokenReserves = readU64(64);
  const virtualSolReserves = readU64(72);
  const tokenTotalSupply = readU64(80);
  if (!Number.isFinite(virtualTokenReserves) || !Number.isFinite(virtualSolReserves)) return undefined;
  return { virtualTokenReserves, virtualSolReserves, tokenTotalSupply };
}

/**
 * Build a PoolDepth from decoded Pump.fun bonding-curve virtual reserves.
 * Base = the traded token (6 decimals per Pump.fun convention), quote = WSOL (9 decimals).
 */
export function pumpCurveToDepth(curve: PumpCurveReserves): PoolDepth | undefined {
  if (!curve || curve.virtualTokenReserves <= 0 || curve.virtualSolReserves <= 0) return undefined;
  return {
    // quote first (WSOL) — matches the existing PoolDepth ordering (quoteReserve/baseReserve)
    quoteReserve: curve.virtualSolReserves,
    baseReserve: curve.virtualTokenReserves,
    quoteDecimals: 9, // WSOL
    baseDecimals: 6,  // Pump.fun tokens use 6 decimals
    bondingCurve: true,
  };
}
