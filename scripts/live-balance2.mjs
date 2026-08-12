import { readFileSync } from 'node:fs';
import { parseGenericSwap } from '../dist/providers/triton-geyser.js';
const secrets = readFileSync(new URL('../secrets.txt', import.meta.url), 'utf8');
let token = '';
for (const l of secrets.split('\n')) { const m = l.match(/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i); if (m) { token = m[1]; break; } }
const RPC = 'https://johnb-mainnet-2781.mainnet.rpcpool.com/' + token;
async function rpc(method, params) { const res = await fetch(RPC, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }) }); return (await res.json()).result; }
const MINT = 'EDhUFVWuz6jbV5p3aZa8XCp5cgR1j47NewjQKNWNpump';
const sigs = await rpc('getSignaturesForAddress', [MINT, { limit: 15 }]);
let checked = 0;
for (const s of (sigs ?? [])) {
  const tx = await rpc('getTransaction', [s.signature, { maxSupportedTransactionVersion: 0, encoding: 'json' }]);
  if (!tx) continue;
  const logs = tx.meta?.logMessages ?? [];
  if (!logs.some((l) => /Instruction: (Swap|Buy|Sell)/.test(l))) continue;
  const payload = { transaction: { transaction: tx } };
  const g = parseGenericSwap(payload);
  console.log('sig', s.signature.slice(0, 8), '| kind', g?.kind, '| price(L/tok)', g?.priceLamportsPerToken, '| wsolDelta', g?.wsolRawDelta, '| baseDelta', g?.baseRawDelta, '| mint', g?.mint?.slice(0, 8));
  if (++checked >= 4) break;
}
if (!checked) console.log('geen swap-txns gevonden in recente 15');