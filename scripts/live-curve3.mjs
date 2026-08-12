import { readFileSync } from 'node:fs';
import { parsePumpTxn } from '../dist/providers/triton-geyser.js';
const secrets = readFileSync(new URL('../secrets.txt', import.meta.url), 'utf8');
let token = '';
for (const l of secrets.split('\n')) { const m = l.match(/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i); if (m) { token = m[1]; break; } }
const RPC = 'https://johnb-mainnet-2781.mainnet.rpcpool.com/' + token;
async function rpc(method, params) { const res = await fetch(RPC, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }) }); return (await res.json()).result; }
const MINT = 'EDhUFVWuz6jbV5p3aZa8XCp5cgR1j47NewjQKNWNpump';
const sigs = await rpc('getSignaturesForAddress', [MINT, { limit: 15 }]);
// correcte fabriek-payload: { transaction: { transaction: <getTransaction result> } }
// waar factory's inner = outer.transaction en keys = inner.transaction.message.accountKeys
for (const s of (sigs ?? [])) {
  const tx = await rpc('getTransaction', [s.signature, { maxSupportedTransactionVersion: 0, encoding: 'json' }]);
  if (!tx) continue;
  const logs = tx.meta?.logMessages ?? [];
  if (!logs.some((l) => /Instruction: (Buy|Sell)/.test(l))) continue;
  // probeer 3 payload-diepheden
  const p1 = parsePumpTxn({ transaction: { transaction: tx } });
  const p2 = parsePumpTxn({ transaction: { transaction: { transaction: tx } } });
  const p3 = parsePumpTxn({ transaction: { transaction: tx.transaction, meta: tx.meta } });
  console.log('sig', s.signature.slice(0, 8), '| p1', JSON.stringify(p1), '| p2', JSON.stringify(p2), '| p3', JSON.stringify(p3));
  const p = p1 ?? p2 ?? p3;
  if (p?.curve) {
    const acct = await rpc('getAccountInfo', [p.curve, { encoding: 'base64' }]);
    const buf = Buffer.from(acct?.value?.data?.[0] || '', 'base64');
    console.log('  curve bytes:', buf.length, '| disc:', buf.slice(0, 8).toString('hex'));
    if (buf.length >= 96) {
      const r64 = (o) => Number(buf.readBigUInt64LE(o));
      // v1 offsets: mint@8, creator@40, tokenRes@72, solRes@80, supply@88
      console.log('  [v1] tok@72=' + r64(72).toLocaleString(), 'sol@80=' + (r64(80)/1e9).toFixed(2), 'SOL', 'liq=' + Math.round(2*(r64(80)/1e9)*74));
    }
    break;
  }
}