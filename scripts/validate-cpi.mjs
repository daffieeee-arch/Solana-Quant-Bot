import { readFileSync } from 'node:fs';
const secrets = readFileSync(new URL('../secrets.txt', import.meta.url), 'utf8');
let token = '';
for (const l of secrets.split('\n')) { const m = l.match(/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i); if (m) { token = m[1]; break; } }
const RPC = 'https://johnb-mainnet-2781.mainnet.rpcpool.com/' + token;
async function rpc(method, params) { const res = await fetch(RPC, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }) }); return (await res.json()).result; }
const MINT = 'EDhUFVWuz6jbV5p3aZa8XCp5cgR1j47NewjQKNWNpump';
const sigs = await rpc('getSignaturesForAddress', [MINT, { limit: 10 }]);
const oldest = sigs?.[sigs.length - 1];
const tx = await rpc('getTransaction', [oldest.signature, { maxSupportedTransactionVersion: 0, encoding: 'jsonParsed' }]);
// toon ALLE pump-instructions met hun volledige account-lijst + data (hex)
let n = 0;
for (const group of tx?.meta?.innerInstructions ?? []) {
  for (const ix of group?.instructions ?? []) {
    const pid = typeof ix?.programId === 'string' ? ix.programId : ix?.programId?.pubkey;
    if (pid !== '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P') continue;
    n += 1;
    const accs = (ix.accounts ?? []).map((a) => (typeof a === 'string' ? a : a?.pubkey));
    console.log(`=== pump instr ${n} (depth ${group.index}) | data ${ix.data} ===`);
    accs.forEach((a, i) => console.log('  ', i, a, i === 0 ? '(curve/global?)' : ''));
  }
}