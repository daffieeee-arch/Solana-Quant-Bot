import { readFileSync } from 'node:fs';
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
const CURVE = 'FAwtZ7PyDmB1RApY6WDG2rstGHTtvDeAhKnvTjvGZ9Ar';
const acct = await rpc('getAccountInfo', [CURVE, { encoding: 'base64' }]);
const buf = Buffer.from(acct?.value?.data?.[0] || '', 'base64');
console.log('curve bytes:', buf.length);
const readU64 = (off) => Number(buf.readBigUInt64LE(off));
const hx = (off, len = 16) => buf.slice(off, off + len).toString('hex');
const liq = (sol) => Math.round(2 * sol * 74).toLocaleString();
console.log('discriminator (0-8):', hx(0, 8));
console.log('offset  8 mint?:   ', hx(8), '...');
console.log('offset 40 creator?:', hx(40), '...');
console.log('--- hypotheses ---');
console.log('A: token@72=', (readU64(72)/1e6).toFixed(0), 'tokens | sol@80=', (readU64(80)/1e9).toFixed(4), 'SOL | liq', liq(readU64(80)/1e9));
console.log('C: token@64=', (readU64(64)/1e6).toFixed(0), 'tokens | sol@72=', (readU64(72)/1e9).toFixed(4), 'SOL | liq', liq(readU64(72)/1e9));
console.log('D: token@80=', (readU64(80)/1e6).toFixed(0), 'tokens | sol@88=', (readU64(88)/1e9).toFixed(4), 'SOL | liq', liq(readU64(88)/1e9));
console.log('E: token@72=', (readU64(72)/1e6).toFixed(0), 'tokens | sol@88=', (readU64(88)/1e9).toFixed(4), 'SOL | liq', liq(readU64(88)/1e9));