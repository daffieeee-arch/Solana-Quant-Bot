import { readFileSync } from 'node:fs';
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
  if (!logs.some((l) => /Instruction: (Swap|Buy|Sell)/.test(l))) continue;
  console.log('=== sig', s.signature.slice(0, 10), 'logs:', logs.filter((l) => /Instruction/i.test(l)).map((l) => l.split(':').pop()).join(','));
  const keys = (tx.transaction?.message?.accountKeys ?? []).map((k) => typeof k === 'string' ? k : k?.pubkey);
  const pre = tx.meta?.preBalances ?? [];
  const post = tx.meta?.postBalances ?? [];
  console.log('SOL-native account-deltas (lamports):');
  keys.forEach((k, i) => {
    const d = (post[i] ?? 0) - (pre[i] ?? 0);
    if (Math.abs(d) > 1000) console.log('  idx', i, '|', (k||'').slice(0,12), '| delta', d, '=', (d/1e9).toFixed(4), 'SOL');
  });
  // toon token-hoeveelheid die de curve beweegt
  for (const b of tx.meta?.postTokenBalances ?? []) {
    const preb = (tx.meta?.preTokenBalances ?? []).find((x) => x.accountIndex === b.accountIndex);
    if (b.mint === MINT) {
      const preAmt = preb?.uiTokenAmount?.amount ? Number(preb.uiTokenAmount.amount) : 0;
      const postAmt = b.uiTokenAmount?.amount ? Number(b.uiTokenAmount.amount) : 0;
      if (Math.abs(postAmt - preAmt) > 0) console.log('  token idx', b.accountIndex, '| mint', MINT.slice(0,8), '| delta raw', postAmt - preAmt, '| ui', ((postAmt-preAmt)/1e6).toFixed(0));
    }
  }
  break;
}