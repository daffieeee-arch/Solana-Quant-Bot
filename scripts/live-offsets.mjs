import { readFileSync } from 'node:fs';
const secrets = readFileSync(new URL('../secrets.txt', import.meta.url), 'utf8');
let token = '';
for (const l of secrets.split('\n')) { const m = l.match(/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i); if (m) { token = m[1]; break; } }
const RPC = 'https://johnb-mainnet-2781.mainnet.rpcpool.com/' + token;
async function rpc(method, params) { const res = await fetch(RPC, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }) }); return (await res.json()).result; }
const MINT = 'EDhUFVWuz6jbV5p3aZa8XCp5cgR1j47NewjQKNWNpump';

// 1) getTokenLargestAccounts → de curve-houder (grootste) is de bonding curve.
// 2) De curve-houder-ACCOUNT is een token-account; de curve zelf is de PDA-OWNER.
const large = await rpc('getTokenLargestAccounts', [MINT]);
const top = large?.value?.[0]?.address;
console.log('grootste houder:', top?.slice(0, 14), '| uiAmount:', large?.value?.[0]?.uiAmount);
const acct = await rpc('getAccountInfo', [top, { encoding: 'jsonParsed' }]);
const owner = acct?.value?.data?.parsed?.info?.owner;
console.log('owner (bonding curve):', owner?.slice(0, 14));
if (!owner) { console.log('geen owner gevonden'); process.exit(0); }

// konsistentie-check: de curve-owner zou ~99.96% van supply moeten houden
const curveAcct = await rpc('getAccountInfo', [owner, { encoding: 'base64' }]);
const buf = Buffer.from(curveAcct?.value?.data?.[0] || '', 'base64');
console.log('curve bytes:', buf.length, '| discriminator:', buf.slice(0, 8).toString('hex'));
if (buf.length < 100) { console.log('te kort'); process.exit(0); }
const r64 = (o) => Number(buf.readBigUInt64LE(o));
console.log('--- alle u64 offsets van de curve ---');
console.log('  salt/disc 8:', r64(8).toLocaleString());
console.log('  u64@16:', r64(16).toLocaleString());
console.log('  u64@24:', r64(24).toLocaleString());
console.log('  u64@32:', r64(32).toLocaleString());
console.log('  u64@40:', r64(40).toLocaleString());
console.log('  u64@48:', r64(48).toLocaleString());
console.log('  u64@56:', r64(56).toLocaleString());
console.log('  u64@64:', r64(64).toLocaleString());
console.log('  u64@72:', r64(72).toLocaleString(), '→ %SOL', (r64(72)/1e9).toFixed(4));
console.log('  u64@80:', r64(80).toLocaleString(), '→ %SOL', (r64(80)/1e9).toFixed(4));
console.log('  u64@88:', r64(88).toLocaleString(), '→ %SOL', (r64(88)/1e9).toFixed(4));
console.log('  u64@96:', r64(96).toLocaleString(), '→ %SOL', (r64(96)/1e9).toFixed(4));
console.log('  u64@104:', r64(104).toLocaleString(), '→ %SOL', (r64(104)/1e9).toFixed(4));
// toon ook de mint/creator als pubkeys (hex slices)
const hex = (o, n) => buf.slice(o, o + n).toString('hex');
console.log('  bytes 8-40 (mint?):', hex(8, 32).slice(0, 20));
console.log('  bytes 40-72 (creator?):', hex(40, 32).slice(0, 20));