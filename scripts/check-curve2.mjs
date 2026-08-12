import { readFileSync } from 'node:fs';
// base58 encode helper
const ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';
function b58encode(bytes) {
  let num = 0n;
  for (const b of bytes) num = num * 256n + BigInt(b);
  let out = '';
  while (num > 0n) { out = ALPHABET[Number(num % 58n)] + out; num /= 58n; }
  for (const b of bytes) { if (b === 0) out = '1' + out; else break; }
  return out || '1';
}

const secrets = readFileSync(new URL('../secrets.txt', import.meta.url), 'utf8');
let token = '';
for (const l of secrets.split('\n')) {
  const m = l.match(/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i);
  if (m) { token = m[1]; break; }
}
const RPC = `https://johnb-mainnet-2781.mainnet.rpcpool.com/${token}`;
const CURVE = '4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf';
const ALON_MINT = 'AwkGxATLzLhMoJzYpqPnuqG32JavQ5YjYEpwPBL8pump';

const res = await fetch(RPC, {
  method: 'POST', headers: { 'Content-Type': 'application/json', 'x-token': token },
  body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'getAccountInfo', params: [CURVE, { encoding: 'base64' }] }),
});
const j = await res.json();
const data = j?.result?.value?.data?.[0];
const buf = Buffer.from(data, 'base64');
// offset 0 = discriminator (8 bytes), offset 8 = mint? of offset 4? probeer beide
const mintCandidates = {
  'offset 8 (na discriminator)': buf.slice(8, 40),
  'offset 4': buf.slice(4, 36),
};
for (const [name, slice] of Object.entries(mintCandidates)) {
  console.log(name, '->', b58encode(slice), '| is Alon:', b58encode(slice) === ALON_MINT);
}
const readU64 = (off) => Number(buf.readBigUInt64LE(off));
console.log('virtualTokenReserves@64:', readU64(64));
console.log('virtualSolReserves@72:', readU64(72));
const priceSolPerToken = readU64(72) / readU64(64);
console.log('prijs SOL/token (virtueel):', priceSolPerToken);