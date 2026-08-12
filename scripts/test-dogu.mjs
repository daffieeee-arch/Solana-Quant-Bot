import { TritonReserveReader } from '../dist/providers/triton-reserves.js';
import { readFileSync } from 'node:fs';

const secrets = readFileSync(new URL('../secrets.txt', import.meta.url), 'utf8');
let token = '';
for (const l of secrets.split('\n')) {
  const m = l.match(/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i);
  if (m) { token = m[1]; break; }
}
const reader = new TritonReserveReader('johnb-mainnet-2781.mainnet.rpcpool.com', token);

// DOGU = pump-mint (eindigt op pump) → mint-derivatielaag
const DOGU = 'NKGA2PBErAZXjkeRHjw3VCfVB6CxXBquG7VnCY4pump';
const depth = await reader.fetchPumpDepthByMint(DOGU, 74);
console.log('DOGU depth:', depth ? JSON.stringify(depth) : 'undefined');
if (depth) {
  const sol = depth.quoteReserve / 10 ** (depth.quoteDecimals ?? 9);
  console.log('~liquidityUsd:', Math.round(2 * sol * 74));
}