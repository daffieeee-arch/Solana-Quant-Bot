import { readFileSync } from 'node:fs';
import { parsePumpTxn, firstMintPerCurve } from '../dist/providers/triton-geyser.js';
const secrets = readFileSync(new URL('../secrets.txt', import.meta.url), 'utf8');
let token = '';
for (const l of secrets.split('\n')) {
  const m = l.match(/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i);
  if (m) { token = m[1]; break; }
}
const RPC = 'https://johnb-mainnet-2781.mainnet.rpcpool.com/' + token;
async function rpc(method, params) {
  const res = await fetch(RPC, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }) });
  return (await res.json()).result;
}
// verse pump-mint uit de discovery: pak recente txn-signatures op de mint
const MINT = 'EDhUFVWuz6jbV5p3aZa8XCp5cgR1j47NewjQKNWNpump';
const sigs = await rpc('getSignaturesForAddress', [MINT, { limit: 3 }]);
console.log('signatures:', sigs?.length);
if (sigs && sigs.length > 0) {
  const sig = sigs[0].signature;
  const tx = await rpc('getTransaction', [sig, { maxSupportedTransactionVersion: 0, encoding: 'json' }]);
  // bouw het geyser-achtige payload-formaat dat parsePumpTxn verwacht
  const payload = { transaction: tx };
  firstMintPerCurve.clear();
  const parsed = parsePumpTxn(payload);
  console.log('parsePumpTxn:', JSON.stringify(parsed));
  if (parsed) {
    const curveAcct = await rpc('getAccountInfo', [parsed.curve, { encoding: 'base64' }]);
    const buf = Buffer.from(curveAcct?.value?.data?.[0] || '', 'base64');
    console.log('curve bytes:', buf.length);
    if (buf.length >= 96) {
      const readU64 = (off) => Number(buf.readBigUInt64LE(off));
      console.log('--- hypothese B (virtualToken@80, virtualSol@88) ---');
      console.log('virtualToken@80:', readU64(80).toLocaleString(), '| tokens:', (readU64(80)/1e6).toFixed(0));
      console.log('virtualSol@88  :', readU64(88).toLocaleString(), '| SOL:', (readU64(88)/1e9).toFixed(4));
      const liq = 2 * (readU64(88)/1e9) * 74;
      console.log('liquiditeit USD (hypothese B):', Math.round(liq).toLocaleString());
      console.log('--- hypothese A (virtualToken@64, virtualSol@72) ---');
      console.log('virtualToken@64:', readU64(64).toLocaleString());
      console.log('virtualSol@72  :', readU64(72).toLocaleString(), '| SOL:', (readU64(72)/1e9).toFixed(4));
      const liqA = 2 * (readU64(72)/1e9) * 74;
      console.log('liquiditeit USD (hypothese A):', Math.round(liqA).toLocaleString());
    }
  }
}