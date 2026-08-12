import { TritonReserveReader } from '../dist/providers/triton-reserves.js';
import { readFileSync } from 'node:fs';

const secrets = readFileSync(new URL('../secrets.txt', import.meta.url), 'utf8');
let token = '';
for (const l of secrets.split('\n')) {
  const m = l.match(/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i);
  if (m) { token = m[1]; break; }
}
const reader = new TritonReserveReader('johnb-mainnet-2781.mainnet.rpcpool.com', token);

// Alon's curve gaf eerder 2.7e8 SOL virtual reserves (gedeelde/liquidated account)
const CURVE = '4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf';
const depth = await reader.fetchPumpDepth(CURVE, 74);
console.log('Alon-curve depth:', depth ? JSON.stringify(depth) : 'undefined (sanity-check correct!)');