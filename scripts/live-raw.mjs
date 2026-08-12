import { readFileSync } from 'node:fs';
const secrets = readFileSync(new URL('../secrets.txt', import.meta.url), 'utf8');
let token = '';
for (const l of secrets.split('\n')) { const m = l.match(/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i); if (m) { token = m[1]; break; } }
const RPC = 'https://johnb-mainnet-2781.mainnet.rpcpool.com/' + token;
async function rpc(method, params) { const res = await fetch(RPC, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }) }); return (await res.json()).result; }
const MINT = 'EDhUFVWuz6jbV5p3aZa8XCp5cgR1j47NewjQKNWNpump';
const sigs = await rpc('getSignaturesForAddress', [MINT, { limit: 15 }]);
for (const s of (sigs ?? [])) {
  const tx = await rpc('getTransaction', [s.signature, { maxSupportedTransactionVersion: 0, encoding: 'jsonParsed' }]);
  if (!tx) continue;
  const logs = tx.meta?.logMessages ?? [];
  if (!logs.some((l) => /Instruction: (Buy|Sell)/.test(l))) continue;
  console.log('=== sig', s.signature.slice(0, 10), '===');
  const keys = (tx.transaction?.message?.accountKeys ?? []).map((k) => typeof k === 'object' ? k.pubkey : k);
  console.log('keys[0..4]:', keys.slice(0, 5).map((k) => (k || '').slice(0, 10)));
  console.log('--- top-level instrs (programIdIndex + data) ---');
  for (const ix of tx.transaction?.message?.instructions ?? []) {
    const idx = ix.programIdIndex ?? ix.accountKeys?.[0];
    console.log('  pgmIdx', JSON.stringify(ix.programIdIndex), '| pgm', (keys[ix.programIdIndex] || '').slice(0, 12), '| data', (ix.data || '').slice(0, 20), '| parsed?', JSON.stringify(ix.parsed?.type || ''));
  }
  console.log('--- inner instrs ---');
  let n = 0;
  for (const g of tx.meta?.innerInstructions ?? []) {
    for (const ix of g.instructions ?? []) {
      const pgm = keys[ix.programIdIndex];
      console.log('  g', g.index, 'pgm', (pgm || '').slice(0, 12), 'type', ix.parsed?.type || '-', '| info:', JSON.stringify(ix.parsed?.info || {}).slice(0, 110));
      if (++n >= 18) break;
    }
    if (n >= 18) break;
  }
  break;
}