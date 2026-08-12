import { readFileSync } from 'node:fs';

// Triton x-token uit secrets.txt halen
const secrets = readFileSync(new URL('../secrets.txt', import.meta.url), 'utf8');
let token = '';
const lines = secrets.split('\n');
for (const l of lines) {
  const m = l.match(/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i);
  if (m) { token = m[1]; break; }
}
console.log('token-len:', token.length);

const RPC = `https://johnb-mainnet-2781.mainnet.rpcpool.com/${token}`;
const CURVE = '4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf';

const res = await fetch(RPC, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', 'x-token': token },
  body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'getAccountInfo', params: [CURVE, { encoding: 'base64' }] }),
});
const j = await res.json();
const data = j?.result?.value?.data?.[0];
console.log('HTTP', res.status, '| data-len:', data ? data.length : 'geen data');
if (data) {
  const buf = Buffer.from(data, 'base64');
  console.log('bytes:', buf.length);
  console.log('eerste 36 bytes (mint-pos):', Buffer.from(buf.slice(4, 36)).toString('hex').slice(0, 24) + '...');
  // decode reserves zoals decodePumpCurve (u64 LE op 64 + 72)
  const readU64 = (off) => Number(buf.readBigUInt64LE(off));
  console.log('virtualTokenReserves@64:', readU64(64));
  console.log('virtualSolReserves@72:', readU64(72));
}