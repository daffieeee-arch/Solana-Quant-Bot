import { readFileSync } from 'node:fs';
const secrets = readFileSync(new URL('../secrets.txt', import.meta.url), 'utf8');
let token = '';
for (const l of secrets.split('\n')) { const m = l.match(/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i); if (m) { token = m[1]; break; } }
const RPC = 'https://johnb-mainnet-2781.mainnet.rpcpool.com/' + token;
async function rpc(method, params) { const res = await fetch(RPC, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }) }); return (await res.json()).result; }
const MINT = 'EDhUFVWuz6jbV5p3aZa8XCp5cgR1j47NewjQKNWNpump';
const sigs = await rpc('getSignaturesForAddress', [MINT, { limit: 8 }]);
const WSOL = 'So11111111111111111111111111111111111111112';
for (const s of (sigs ?? [])) {
  const tx = await rpc('getTransaction', [s.signature, { maxSupportedTransactionVersion: 0, encoding: 'json' }]);
  if (!tx) continue;
  const keys = (tx.transaction?.message?.accountKeys ?? []).map((k) => typeof k === 'string' ? k : k?.pubkey);
  const wsolIdx = keys.indexOf(WSOL);
  const pre = tx.meta?.preBalances ?? [];
  const post = tx.meta?.postBalances ?? [];
  const logs = tx.meta?.logMessages ?? [];
  const hasSwap = logs.some((l) => /Instruction: (Swap|Buy|Sell)/.test(l));
  console.log('sig', s.signature.slice(0, 8),
    '| keys', keys.length, '| wsolIdx', wsolIdx,
    '| pre[wsol]', wsolIdx>=0?pre[wsolIdx]:'na', 'post[wsol]', wsolIdx>=0?post[wsolIdx]:'na',
    '| hasSwap', hasSwap,
    '| preBalances?', pre.length, '| postBalances?', post.length,
    '| preTokenBal?', (tx.meta?.preTokenBalances??[]).filter(b=>b&&b.mint===MINT).length,
    '| accKeys-vorm', typeof (tx.transaction?.message?.accountKeys ?? [])[0]);
}