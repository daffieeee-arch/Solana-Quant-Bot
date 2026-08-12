import { readFileSync } from 'node:fs';
import { parseGenericSwap } from '../dist/providers/triton-geyser.js';
const WSOL = 'So11111111111111111111111111111111111111112';
const MINT = 'AirDrop1pumptokenExample111111111111111111pump';
const RAYD = '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8';
const payload = {
  transaction: {
    transaction: {
      transaction: { message: { accountKeys: ['AAA', RAYD, WSOL, MINT], instructions: [{ programIdIndex: 1 }] } },
    },
    meta: {
      logMessages: ['Program log: Instruction: Swap'],
      preTokenBalances: [
        { accountIndex: 2, mint: WSOL, uiTokenAmount: { amount: '10000000000', decimals: 9 } },
        { accountIndex: 3, mint: MINT, uiTokenAmount: { amount: '0', decimals: 6 } },
      ],
      postTokenBalances: [
        { accountIndex: 2, mint: WSOL, uiTokenAmount: { amount: '8000000000', decimals: 9 } },
        { accountIndex: 3, mint: MINT, uiTokenAmount: { amount: '1000000', decimals: 6 } },
      ],
    },
  },
};
const inner = payload.transaction.transaction;
console.log('inner.transaction?.message?.accountKeys:', JSON.stringify(inner.transaction?.message?.accountKeys));
console.log('inner.meta?.logMessages:', JSON.stringify(inner.meta?.logMessages));
console.log('preTokenBalances count:', (inner.meta?.preTokenBalances ?? []).length);
const logs = inner.meta?.logMessages ?? [];
const match = logs.some((l) => /Program log: Instruction: (Swap|Swap2|BuyV2|SellV2|BuyStable|SellStable|Buy|Sell)/.test(l));
console.log('log-match:', match);
console.log('parse:', JSON.stringify(parseGenericSwap(payload)));