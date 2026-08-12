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
  const keys = (tx.transaction?.message?.accountKeys ?? []);
  console.log('accountKeys w/z programIdIndex:');
  keys.forEach((k, i) => {
    if (typeof k === 'object' && k.pubkey) console.log('  ', i, (k.pubkey || '').slice(0, 14), '| signer', k.signer, '| writable', k.writable);
  });
  console.log('--- alle inner parsed instructies ---');
  for (const g of tx.meta?.innerInstructions ?? []) {
    for (const ix of g.instructions ?? []) {
      const p = ix.parsed;
      if (p?.type === 'transferChecked' || p?.type === 'transfer' || p?.type === 'transferNative') {
        const info = p.info || {};
        console.log('  ', p.type, '|', (info.mint || '').slice(0, 8) || `SOL`, '| amount', info.amount ?? info.transferAmount ?? info.lamports, '| dec', info.tokenAmount?.decimals ?? (info.mint&&'6'));
      }
    }
  }
  console.log('--- log ' + (logs.filter(l => /TransferChecked|Transfer/i.test(l))[0] || 'geen transfer-log'));
  break;
}