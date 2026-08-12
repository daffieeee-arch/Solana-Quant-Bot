import { readFileSync } from 'node:fs';
const secrets = readFileSync(new URL('../secrets.txt', import.meta.url), 'utf8');
let token = '';
for (const l of secrets.split('\n')) {
  const m = l.match(/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i);
  if (m) { token = m[1]; break; }
}
const RPC = `https://johnb-mainnet-2781.mainnet.rpcpool.com/${token}`;
const MINT = 'EDhUFVWuz6jbV5p3aZa8XCp5cgR1j47NewjQKNWNpump';

async function rpc(method, params) {
  const res = await fetch(RPC, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }) });
  return (await res.json()).result;
}

const largest = await rpc('getTokenLargestAccounts', [MINT]);
console.log('largest accounts:', JSON.stringify(largest?.value?.slice(0, 3)));
const top = largest?.value?.[0]?.address;
if (top) {
  const acct = await rpc('getAccountInfo', [top, { encoding: 'jsonParsed' }]);
  console.log('top holder owner:', acct?.value?.data?.parsed?.info?.owner?.slice(0, 12));
  console.log('top holder amount:', acct?.value?.data?.parsed?.info?.tokenAmount?.amount, '| decimals:', acct?.value?.data?.parsed?.info?.tokenAmount?.decimals);
  const curve = acct?.value?.data?.parsed?.info?.owner;
  if (curve) {
    const curveAcct = await rpc('getAccountInfo', [curve, { encoding: 'base64' }]);
    const data = curveAcct?.value?.data?.[0];
    console.log('curve data len:', data ? data.length : 'geen');
    if (data) {
      const buf = Buffer.from(data, 'base64');
      const readU64 = (off) => Number(buf.readBigUInt64LE(off));
      console.log('virtualTokenReserves@64:', readU64(64));
      console.log('virtualSolReserves@72:', readU64(72));
      console.log('sol-reserves (sol):', readU64(72) / 1e9, '| liqUSD ~', 2 * readU64(72) / 1e9 * 74);
    }
  }
}