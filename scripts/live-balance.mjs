import { readFileSync } from 'node:fs';
import { parseGenericSwap, parsePumpTxn } from '../dist/providers/triton-geyser.js';
const secrets = readFileSync(new URL('../secrets.txt', import.meta.url), 'utf8');
let token = '';
for (const l of secrets.split('\n')) { const m = l.match(/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i); if (m) { token = m[1]; break; } }
const RPC = 'https://johnb-mainnet-2781.mainnet.rpcpool.com/' + token;
async function rpc(method, params) { const res = await fetch(RPC, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }) }); return (await res.json()).result; }
// verse pump-mint uit de discovery
const MINT = 'EDhUFVWuz6jbV5p3aZa8XCp5cgR1j47NewjQKNWNpump';
const sigs = await rpc('getSignaturesForAddress', [MINT, { limit: 5 }]);
console.log('sigs:', sigs?.length);
for (const s of (sigs ?? []).slice(0, 3)) {
  const tx = await rpc('getTransaction', [s.signature, { maxSupportedTransactionVersion: 0, encoding: 'json' }]);
  if (!tx) continue;
  // bouw het SDK-achtige payload formaat dat de geyser-factory produceert:
  // de factory gebruikt inner = outer.transaction waar outer.payload.transaction
  const payload = { transaction: { transaction: tx } };
  const g = parseGenericSwap(payload);
  const p = parsePumpTxn(payload);
  console.log('sig', s.signature.slice(0, 10), '| gen:', JSON.stringify(g), '| pump:', JSON.stringify(p));
}