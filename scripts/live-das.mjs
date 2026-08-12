import { readFileSync } from 'node:fs';
const secrets = readFileSync(new URL('../secrets.txt', import.meta.url), 'utf8');
let token = '';
for (const l of secrets.split('\n')) { const m = l.match(/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i); if (m) { token = m[1]; break; } }
const RPC = 'https://johnb-mainnet-2781.mainnet.rpcpool.com/' + token;
async function rpc(method, params) { const res = await fetch(RPC, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }) }); return (await res.json()).result; }
const MINT = 'EDhUFVWuz6jbV5p3aZa8XCp5cgR1j47NewjQKNWNpump';
// DAS getAsset
try {
  const a = await rpc('getAsset', [MINT]);
  if (a) {
    console.log('symbol:', a.content?.metadata?.symbol, '| name:', a.content?.metadata?.name);
    console.log('supply:', a.supply?.printable_supply, '| decimals:', a.supply?.decimals);
  } else console.log('getAsset: null');
} catch (e) { console.log('getAsset err:', e.message || e); }
// fallback: token-account supply (1 call)
const acct = await rpc('getTokenSupply', [MINT]);
console.log('supply:', JSON.stringify(acct?.value));
// marketcap via largest-accounts op de curve-houder = prijs-schatting
const large = await rpc('getTokenLargestAccounts', [MINT]);
console.log('largest volgorde (eerste 3):', JSON.stringify(large?.value?.slice(0,3).map(x=>({addr:x.address.slice(0,8),amount:x.uiAmount}))));