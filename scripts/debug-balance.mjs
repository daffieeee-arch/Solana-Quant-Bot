import { parseGenericSwap } from '../dist/providers/triton-geyser.js';
const WSOL = 'So11111111111111111111111111111111111111112';
const MINT = 'AirDrop1pumptokenExample111111111111111111pump';
const RAYD = '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8';
const payload = {
  transaction: {
    transaction: {
      transaction: {
        message: { accountKeys: ['AAA', RAYD, WSOL, MINT] },
        instructions: [{ programIdIndex: 1 }],
      },
      meta: {
        logMessages: ['Program log: Instruction: Swap'],
        preBalances: [1, 1, 10000000000, 1],
        postBalances: [1, 1, 8000000000, 1],
        preTokenBalances: [{ accountIndex: 3, mint: MINT, uiTokenAmount: { amount: '0', decimals: 6 } }],
        postTokenBalances: [{ accountIndex: 3, mint: MINT, uiTokenAmount: { amount: '1000000', decimals: 6 } }],
      },
    },
  },
};
try {
  console.log('result:', JSON.stringify(parseGenericSwap(payload)));
} catch (e) {
  console.log('ERR:', e.message);
}