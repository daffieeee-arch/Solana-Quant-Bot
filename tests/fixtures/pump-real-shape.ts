import { createHash } from 'node:crypto';
import { PublicKey } from '@solana/web3.js';
import { deriveBondingCurve, PUMP_PROGRAM_ID } from '../../src/pump-parser.js';

/**
 * Real-shape Pump.fun transaction fixturecorpus (OFFLINE).
 *
 * Provenance:
 *  - Pump IDL: pump-fun/pump-public-docs `idl/pump.json` @ commit 3c6721a67c0b206b
 *    (sha256 b90bc471327f671449271d5d1d42354d…), versie 0.1.0 — opgehaald 2026-08-15.
 *  - Discriminators: SHA256("global:<name>")[0:8] (Anchor), geverifieerd tegen de IDL.
 *  - Account-pubkeys: deterministische geldige 32-byte pubkeys (lokaal gegenereerd);
 *    de bonding-curve per fixture = DERIVED officiële PDA (findProgramAddressSync).
 *  - Geen credentials/private data; geen Triton PAYG-endpoorts gebruikt.
 */

const disc = (name: string) => createHash('sha256').update(`global:${name}`).digest().subarray(0, 8).toString('hex');
const mk = (label: string) => new PublicKey(createHash('sha256').update(label).digest()).toBase58();

export const FIXTURE_MINT = mk('fixture-mint-A');
export const FIXTURE_FEE = mk('fixture-fee');
export const FIXTURE_USER = mk('fixture-user');
export const FIXTURE_CURVE = deriveBondingCurve(FIXTURE_MINT); // officiële PDA-relatie

/** V1-layout: [program, fee, mint, curve, user] — alle pubkeys geldig. */
export function v1Accounts(): string[] { return [PUMP_PROGRAM_ID, FIXTURE_FEE, FIXTURE_MINT, FIXTURE_CURVE, FIXTURE_USER]; }

export type Fixture = {
  name: string;
  signatureHint: string;   // reproduceerbare id (geen echte private data)
  variant: string;         // IDL instruction-naam
  discriminatorHex: string;
  accounts: string[];
  acctCount: number;
  expected: 'buy' | 'sell' | undefined;
  note: string;
  topLogs?: boolean;
  topLevel?: boolean;
};

export const PUMP_FIXTURES: Fixture[] = [
  { name: 'legacy_buy', signatureHint: 'fx-buy-v1', variant: 'buy', discriminatorHex: disc('buy'), accounts: v1Accounts(), acctCount: 16, expected: 'buy', note: 'v1-buy, inner CPI (16 accts), log aanwezig maar disc wint' },
  { name: 'legacy_sell', signatureHint: 'fx-sell-v1', variant: 'sell', discriminatorHex: disc('sell'), accounts: v1Accounts(), acctCount: 14, expected: 'sell', note: 'v1-sell' },
  { name: 'buy_v2', signatureHint: 'fx-buy-v2', variant: 'buy_v2', discriminatorHex: disc('buy_v2'), accounts: v1Accounts(), acctCount: 27, expected: 'buy', note: 'v2 (BondingCurveV3 wrapper), 27 accts' },
  { name: 'sell_v2', signatureHint: 'fx-sell-v2', variant: 'sell_v2', discriminatorHex: disc('sell_v2'), accounts: v1Accounts(), acctCount: 26, expected: 'sell', note: 'v2-sell' },
  { name: 'buy_exact_quote_in_v2', signatureHint: 'fx-eq-v2', variant: 'buy_exact_quote_in_v2', discriminatorHex: disc('buy_exact_quote_in_v2'), accounts: v1Accounts(), acctCount: 27, expected: 'buy', note: 'quote-in variant' },
  { name: 'buy_exact_sol_in', signatureHint: 'fx-esol', variant: 'buy_exact_sol_in', discriminatorHex: disc('buy_exact_sol_in'), accounts: v1Accounts(), acctCount: 16, expected: 'buy', note: 'sol-in exact' },
  { name: 'missing_logs', signatureHint: 'fx-nologs', variant: 'buy', discriminatorHex: disc('buy'), accounts: v1Accounts(), acctCount: 16, expected: 'buy', topLogs: true, note: 'ontbrekende/afwijkende logs — discriminator wint' },
  { name: 'top_level_instruction', signatureHint: 'fx-toplevel', variant: 'sell', discriminatorHex: disc('sell'), accounts: v1Accounts(), acctCount: 14, expected: 'sell', topLevel: true, note: 'pump-instructie op top-level (niet inner)' },
  { name: 'unknown_discriminator', signatureHint: 'fx-unknown', variant: 'create', discriminatorHex: disc('create'), accounts: v1Accounts(), acctCount: 14, expected: undefined, note: 'onbekende discriminator → fail-closed' },
  { name: 'mint_curve_mismatch', signatureHint: 'fx-mismatch', variant: 'buy', discriminatorHex: disc('buy'), accounts: [PUMP_PROGRAM_ID, FIXTURE_FEE, FIXTURE_MINT, mk('wrong-curve'), FIXTURE_USER], acctCount: 16, expected: undefined, note: 'curve ≠ derived-PDA én niet in accounts → fail-closed' },
  { name: 'not_pump_program', signatureHint: 'fx-raydium', variant: 'buy', discriminatorHex: disc('buy'), accounts: ['675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8', FIXTURE_FEE, FIXTURE_MINT, FIXTURE_CURVE, FIXTURE_USER], acctCount: 16, expected: undefined, note: 'non-pump programma (Raydium) → undefined' },
  { name: 'live_sell_mainnet', signatureHint: 'real-4RNa86Ef', variant: 'sell', discriminatorHex: 'e6345c8dd8b14540', accounts: v1Accounts(), acctCount: 17, expected: 'sell', note: 'LIVE mainnet sell-disc (custom dispatcher) — disc uit real-trades.json sig 4RNa86Ef; a2=mint a3=curve derived-PDA bewezen' },
  { name: 'live_buy_mainnet', signatureHint: 'real-buy', variant: 'buy', discriminatorHex: '0094d0da1f435eb0', accounts: v1Accounts(), acctCount: 16, expected: 'buy', note: 'LIVE mainnet buy-disc via FLASHX8-router (reviewer-dump)' },
];

/** Bouwt een geyser-achtige payload uit een fixture. */
export function fixturePayload(f: Fixture): unknown {
  const instruction = { programIdIndex: 0, accounts: Array.from({ length: f.acctCount }, (_, i) => i % f.accounts.length), data: Buffer.concat([Buffer.from(f.discriminatorHex, 'hex'), Buffer.alloc(40)]) };
  const logs: string[] = f.topLogs ? [] : [`Program log: Instruction: ${f.variant}`];
  const innerInstructions = [{ instructions: [instruction] }];
  return {
    transaction: { transaction: { transaction: { message: { accountKeys: f.accounts, instructions: f.topLevel ? [instruction] : [] } }, meta: { logMessages: logs, innerInstructions: f.topLevel ? [] : innerInstructions } } },
  };
}