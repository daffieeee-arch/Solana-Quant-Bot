import { describe, it, expect, beforeEach } from 'vitest';
import { parsePumpTxn, firstMintPerCurve, parseGenericSwap } from '../src/providers/triton-geyser.js';
const RAYD = '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8';

// Bouwt een geyser-SDK payload: { transaction: { transaction: { transaction, meta } } }
// met een pump buy CPI in innerInstructions. accountKeys zijn strings (decodeKey
// retourneert strings direct); de CPI accounts verwijzen naar indices. Echte order:
// [global(0), feeRecipient(1), mint(2), bondingCurve(3), user(4), …] — de curve is
// het account DIRECT NA de mint (extern geverifieerd; NIET ervoor).
const CURVE = '7VtfL8fvgNfhz17qKRMjzQEXgbdpnHHHQRh54R9jP2RJ';
const MINT = '2ZA8NQS7hx4Gpump2ZA8NQS7hx4GpumpXXXXpump';
const USER = '9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin';

function buildPumpPayload(kind: 'Buy' | 'Sell', curve: string, mint: string): unknown {
  // 18 account-keys: [pump(0), feeRecipient(1), mint(2), curve(3), user(4), 13 extra zoals echte buys]
  const extras = Array.from({ length: 13 }, (_, i) => `Account${i}`.padEnd(44, 'x'));
  const accountKeys = ['6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P', 'FeeRecipient111111111111111111111111111111', mint, curve, USER, ...extras];
  // CPI accounts verwijst naar indices [0..17]; mint=2, curve=3
  const cpiAccountIdx = Array.from({ length: 18 }, (_, i) => i);
  return {
    transaction: {
      transaction: {
        transaction: {
          message: { accountKeys },
        },
        meta: {
          logMessages: [`Program log: Instruction: ${kind}`],
          innerInstructions: [
            {
              instructions: [
                {
                  programIdIndex: 0,
                  accounts: Buffer.from(cpiAccountIdx),
                  data: '3gV2gUA8',
                },
              ],
            },
          ],
        },
      },
    },
  };
}

describe('parsePumpTxn (geyser raw txn → buy/sell identiteit)', () => {
  beforeEach(() => firstMintPerCurve.clear());

  it('parseert een pump buy txn: mint + curve uit CPI account-indices', () => {
    const parsed = parsePumpTxn(buildPumpPayload('Buy', CURVE, MINT));
    expect(parsed).toBeDefined();
    expect(parsed?.kind).toBe('buy');
    expect(parsed?.mint).toBe(MINT);
    expect(parsed?.curve).toBe(CURVE);
  });

  it('parseert een pump sell txn', () => {
    const parsed = parsePumpTxn(buildPumpPayload('Sell', CURVE, MINT));
    expect(parsed?.kind).toBe('sell');
    expect(parsed?.mint).toBe(MINT);
  });

  it('geeft undefined voor een txn zonder pump buy/sell logs', () => {
    const payload = {
      transaction: {
        transaction: {
          transaction: {},
          meta: { logMessages: ['Program log: Instruction: Deposit'] },
        },
      },
    };
    expect(parsePumpTxn(payload)).toBeUndefined();
  });

  it('v2: herkent een SellV2- txn (BondingCurveV3-wrapper) zonder de fee-wallet als curve te nemen', () => {
    // pump.fun v2 (2025/26): logs "Instruction: SellV2" via 6Vo3245…-wrapper.
    // Payload-diepte identiek aan buildPumpPayload (v1-builder werkt wél).
    const extras = Array.from({ length: 13 }, (_, i) => `Account${i}`.padEnd(44, 'x'));
    const accountKeys = ['6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P', 'FeeRecipient111111111111111111111111111111', 'mintV2Token1111111111111111111111111111111', CURVE, USER, ...extras];
    const cpiAccountIdx = Array.from({ length: 18 }, (_, i) => i);
    const payload = {
      transaction: {
        transaction: {
          transaction: {
            message: { accountKeys },
          },
          meta: {
            logMessages: ['Program 6Vo3245eszAb5wuqEMw8mGdbfRUdKbHhDHP5LcaGuTAB invoke [2]', 'Program log: Instruction: SellV2', 'Program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P invoke [3]'],
            innerInstructions: [{ instructions: [{ programIdIndex: 0, accounts: Buffer.from(cpiAccountIdx), data: '3gV2gUA8' }] }],
          },
        },
      },
    };
    const parsed = parsePumpTxn(payload);
    // kern-parity: een v2-SellV2-txn wordt nu WEL herkend (was undefined vóór de fix)
    expect(parsed).toBeDefined();
    expect(parsed?.kind).toBe('sell');
    // de curve mag NOOIT de fee-wallet zijn (accs[1]) — dat was de oude bug
    expect(parsed?.curve).not.toBe('FeeRecipient111111111111111111111111111111');
  });
});

describe('firstMintPerCurve verse-launch filter', () => {
  beforeEach(() => firstMintPerCurve.clear());

  it('laat de eerste mint op een curve door en blokkeert een tweede mint (gedeelde curve)', () => {
    firstMintPerCurve.set(CURVE, MINT);
    expect(firstMintPerCurve.get(CURVE)).toBe(MINT);
    // een event dat een andere mint op dezelfde curve claimt → gefilterd
    expect(firstMintPerCurve.has(CURVE) && firstMintPerCurve.get(CURVE) !== 'MINT_ANDERS').toBe(true);
  });
});
describe('parseGenericSwap (multi-DEX)', () => {
  it('herkent een Raydium-swap en pakt de verse mint uit token-balances', () => {
    const payload = {
      transaction: {
        transaction: {
          transaction: {
            message: {
              accountKeys: ['999', RAYD, '1.1', '1.1'],
              instructions: [{ programIdIndex: 1 }],
            },
            signatures: ['sig'],
          },
          meta: {
            logMessages: ['Program log: Instruction: Swap'],
            preTokenBalances: [{ mint: 'So11111111111111111111111111111111111111112', owner: 'x' }],
            postTokenBalances: [{ mint: 'LIVEpumpCointeMAINt11111111111111111111pump', owner: 'y' }],
          },
        },
      },
    };
    const got = parseGenericSwap(payload);
    expect(got?.mint).toBe('LIVEpumpCointeMAINt11111111111111111111pump');
    expect(got?.program).toBe(RAYD);
  });

  it('negeert stables en geeft undefined zonder swap-log', () => {
    const p = {
      transaction: {
        transaction: {
          transaction: {},
          meta: {
            logMessages: [],
            preTokenBalances: [{ mint: 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v' }],
          },
        },
      },
    };
    expect(parseGenericSwap(p)).toBeUndefined();
  });

  it('balans-pricing: leidt execution-prijs + swap-diepte af uit WSOL- en token-deltas', () => {
    const WSOL = 'So11111111111111111111111111111111111111112';
    const MINT = 'AirDrop1pumptokenExample111111111111111111pump';
    // WSOL-token-account (index 2) gaat van 10 → 8 WSOL (2 WSOL = 2e9 raw verkocht)
    // base-token account (index 3) gaat van 0 → 1.000.000 raw (1e6/10^6 = 1 token)
    const wsolRaw = '10000000000'; // 10 WSOL, 9 decimals
    const payload = {
      transaction: {
        transaction: {
          transaction: {
            message: { accountKeys: ['AAA', RAYD, WSOL, MINT], instructions: [{ programIdIndex: 1 }] },
          },
          meta: {
            logMessages: ['Program log: Instruction: Swap'],
            preTokenBalances: [
              { accountIndex: 2, mint: WSOL, uiTokenAmount: { amount: wsolRaw, decimals: 9 } },
              { accountIndex: 3, mint: MINT, uiTokenAmount: { amount: '0', decimals: 6 } },
            ],
            postTokenBalances: [
              { accountIndex: 2, mint: WSOL, uiTokenAmount: { amount: '8000000000', decimals: 9 } },
              { accountIndex: 3, mint: MINT, uiTokenAmount: { amount: '1000000', decimals: 6 } },
            ],
          },
        },
      },
    };
    const got = parseGenericSwap(payload)!;
    expect(got?.kind).toBe('buy'); // meer tokens + minder WSOL = gekocht
    expect(got?.priceLamportsPerToken).toBeDefined();
    // 2 WSOL (2e9 lamports) voor 1 token → 2e9 lamports per token
    expect(got?.priceLamportsPerToken!).toBeCloseTo(2_000_000_000, 0);
    expect(got?.baseRawDelta).toBe(1_000_000);
    expect(got?.wsolRawDelta).toBe(2_000_000_000);
  });
});
