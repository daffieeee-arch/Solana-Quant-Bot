import { readFileSync } from 'node:fs';
import { parsePumpTxn } from '../dist/providers/triton-geyser.js';
const secrets = readFileSync(new URL('../secrets.txt', import.meta.url), 'utf8');
let token = '';
for (const l of secrets.split('\n')) { const m = l.match(/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i); if (m) { token = m[1]; break; } }
const RPC = 'https://johnb-mainnet-2781.mainnet.rpcpool.com/' + token;
async function rpc(method, params) { const res = await fetch(RPC, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }) }); return (await res.json()).result; }
const MINT = 'EDhUFVWuz6jbV5p3aZa8XCp5cgR1j47NewjQKNWNpump';
// parsePumpTxn geeft curve via geyser-factory payload: transaction.message.accountKeys + meta
// Deze factory gebruikt programma-log + innerInstructions. Ik gebruik de getTransaction json
// (message.accountKeys als strings, innerInstructions met programIdIndex/accounts).
const sigs = await rpc('getSignaturesForAddress', [MINT, { limit: 15 }]);
for (const s of (sigs ?? [])) {
  const tx = await rpc('getTransaction', [s.signature, { maxSupportedTransactionVersion: 0, encoding: 'json' }]);
  if (!tx) continue;
  const logs = tx.meta?.logMessages ?? [];
  if (!logs.some((l) => /Instruction: (Buy|Sell)/.test(l))) continue;
  const payload = { transaction: { transaction: tx } };
  const p = parsePumpTxn(payload);
  console.log('sig', s.signature.slice(0, 8), '| pump:', JSON.stringify(p));
  if (p?.curve) {
    // decode de curve-account met de juiste offsets
    const acct = await rpc('getAccountInfo', [p.curve, { encoding: 'base64' }]);
    const buf = Buffer.from(acct?.value?.data?.[0] || '', 'base64');
    console.log('  curve bytes:', buf.length, '| discriminator:', buf.slice(0, 8).toString('hex'));
    if (buf.length >= 88) {
      const readU64 = (off) => Number(buf.readBigUInt64LE(off));
      console.log('  tokres@72', readU64(72).toLocaleString(), '| solres@80', (readU64(80)/1e9).toFixed(2), '| liq', Math.round(2*(readU64(80)/1e9)*74));
      console.log('  tokres@80', readU64(80).toLocaleString(), '| solres@88', (readU64(88)/1e9).toFixed(2), '| liqB', Math.round(2*(readU64(88)/1e9)*74));
      console.log('  tokres@64', readU64(64).toLocaleString(), '| solres@72', (readU64(72)/1e9).toFixed(2), '| liqC', Math.round(2*(readU64(72)/1e9)*74));
    }
    break;
  }
}