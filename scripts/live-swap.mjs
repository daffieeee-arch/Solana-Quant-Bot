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
  const keys = (tx.transaction?.message?.accountKeys ?? []).map((k) => typeof k === 'string' ? k : k?.pubkey);
  console.log('--- top-level ---');
  for (const ix of tx.transaction?.message?.instructions ?? []) {
    const pd = keys[ix.programIdIndex];
    console.log('I', (pd||'').slice(0,10), '|', (ix.data||'').slice(0,30));
  }
  console.log('--- inner (TokenzQd transfers) ---');
  for (const g of tx.meta?.innerInstructions ?? []) {
    for (const ix of g.instructions ?? []) {
      const pd = keys[ix.programIdIndex];
      const parsed = ix.parsed;
      if (pd === 'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb' || pd === 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA') {
        console.log('  Token', ix.parsed?.type || ix.programIdIndex, '|', JSON.stringify(parsed?.info?.transferAmount ?? parsed?.info?.amount ?? '').slice(0,40), '| mint', (parsed?.info?.mint || '').slice(0,10), '| auth', (parsed?.info?.authority || '').slice(0,10));
      }
    }
  }
  // SOL transfers via system program
  console.log('--- System transfers (SOL) ---');
  for (const g of tx.meta?.innerInstructions ?? []) {
    for (const ix of g.instructions ?? []) {
      const pd = keys[ix.programIdIndex];
      if (pd === '11111111111111111111111111111111' && ix.parsed?.type === 'transfer') {
        console.log('  SOL', JSON.stringify(ix.parsed?.info?.lamports), '| from', (ix.parsed?.info?.source||'').slice(0,10), '-> to', (ix.parsed?.info?.destination||'').slice(0,10));
      }
    }
  }
  break;
}