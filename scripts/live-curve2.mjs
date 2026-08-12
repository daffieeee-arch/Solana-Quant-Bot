import { readFileSync } from 'node:fs';
import { parsePumpTxn } from '../dist/providers/triton-geyser.js';
const secrets = readFileSync(new URL('../secrets.txt', import.meta.url), 'utf8');
let token = '';
for (const l of secrets.split('\n')) { const m = l.match(/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i); if (m) { token = m[1]; break; } }
const RPC = 'https://johnb-mainnet-2781.mainnet.rpcpool.com/' + token;
async function rpc(method, params) { const res = await fetch(RPC, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }) }); return (await res.json()).result; }
const MINT = 'EDhUFVWuz6jbV5p3aZa8XCp5cgR1j47NewjQKNWNpump';
const sigs = await rpc('getSignaturesForAddress', [MINT, { limit: 15 }]);
for (const s of (sigs ?? [])) {
  const tx = await rpc('getTransaction', [s.signature, { maxSupportedTransactionVersion: 0, encoding: 'json' }]);
  if (!tx) continue;
  const logs = tx.meta?.logMessages ?? [];
  if (!logs.some((l) => /Instruction: (Buy|Sell)/.test(l))) continue;
  // fabriek-payload: { transaction: { transaction: { signature?, transaction, meta } } }
  // waar outer.transaction = { transaction: {message...}, meta }, inner = outer.transaction
  const payload = { transaction: { transaction: tx } };
  const p = parsePumpTxn(payload);
  console.log('sig', s.signature.slice(0, 8), '| pump:', JSON.stringify(p));
  if (p?.curve) {
    const acct = await rpc('getAccountInfo', [p.curve, { encoding: 'base64' }]);
    const buf = Buffer.from(acct?.value?.data?.[0] || '', 'base64');
    console.log('  curve bytes:', buf.length, '| disc:', buf.slice(0, 8).toString('hex'));
    if (buf.length >= 96) {
      const readU64 = (o) => Number(buf.readBigUInt64LE(o));
      console.log('  [A] tok@72', readU64(72).toLocaleString(), 'sol@80', (readU64(80)/1e9).toFixed(2), 'SOL liq', Math.round(2*(readU64(80)/1e9)*74));
      console.log('  [B] tok@80', readU64(80).toLocaleString(), 'sol@88', (readU64(88)/1e9).toFixed(2), 'SOL liq', Math.round(2*(readU64(88)/1e9)*74));
    }
    break;
  }
}
console.log('klaar');