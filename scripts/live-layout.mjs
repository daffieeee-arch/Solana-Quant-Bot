import { readFileSync } from 'node:fs';
const secrets = readFileSync(new URL('../secrets.txt', import.meta.url), 'utf8');
let token = '';
for (const l of secrets.split('\n')) { const m = l.match(/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i); if (m) { token = m[1]; break; } }
const RPC = 'https://johnb-mainnet-2781.mainnet.rpcpool.com/' + token;
async function rpc(method, params) { const res = await fetch(RPC, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }) }); return (await res.json()).result; }

// wsol/ed25519 base58 — minimal implementation
const ALPH = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';
function toBase58(bytes) {
  const digits = [0];
  for (const b of bytes) {
    let carry = b;
    for (let i = 0; i < digits.length; i++) { carry += digits[i] << 8; digits[i] = carry % 58; carry = (carry / 58) | 0; }
    while (carry > 0) { digits.push(carry % 58); carry = (carry / 58) | 0; }
  }
  let str = '';
  for (const d of digits) str = ALPH[d] + str;
  // leading zeros
  let nz = 0; while (bytes[nz] === 0) { nz++; }
  for (let i = 0; i < nz; i++) str = '1' + str;
  return str;
}

const MINT = 'EDhUFVWuz6jbV5p3aZa8XCp5cgR1j47NewjQKNWNpump';
const large = await rpc('getTokenLargestAccounts', [MINT]);
const top = large?.value?.[0]?.address;
const acct = await rpc('getAccountInfo', [top, { encoding: 'jsonParsed' }]);
const owner = acct?.value?.data?.parsed?.info?.owner;
const curveAcct = await rpc('getAccountInfo', [owner, { encoding: 'base64' }]);
const buf = Buffer.from(curveAcct?.value?.data?.[0] || '', 'base64');
console.log('curve bytes:', buf.length);
// probeer pubkeys op diverse offset-paren
for (const mintOff of [0, 8]) {
  for (const creatorOff of [32, 40]) {
    try {
      const m = toBase58(buf.slice(mintOff, mintOff + 32));
      const c = toBase58(buf.slice(creatorOff, creatorOff + 32));
      const match = m === MINT ? ' <- MINT!' : '';
      console.log(`mint@${mintOff} creator@${creatorOff}: mint=${m.slice(0,10)}${match} creator=${c.slice(0,10)}`);
    } catch { console.log(`mint@${mintOff} cr@${creatorOff}: decode fout`); }
  }
}
// de juiste layout: discriminator(8)+mint(8)+creator(40) → reserves @72/@80
const r64 = (o) => Number(buf.readBigUInt64LE(o));
console.log('--- lay-out A (disc@0,mint@8,creator@40): ---');
console.log('  virtualToken@72:', (r64(72)/1e6).toFixed(0), 'tokens | virtualSol@80:', (r64(80)/1e9).toFixed(4), 'SOL');
console.log('--- lay-out B (disc@0,mint@8,creator@40) alternatieve res offsets: ---');
for (const [t,s] of [[64,72],[72,80],[80,88]]) {
  console.log('  tok@'+t, (r64(t)/1e6).toFixed(0), 'tokens | sol@'+s, (r64(s)/1e9).toFixed(4), 'SOL | prijs', (r64(s)/1e9)/(r64(t)/1e6) ? ((r64(s)/1e9)/(r64(t)/1e6)).toExponential(2) : '?');
}