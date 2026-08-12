import fs from 'node:fs';
import { KNOWN_MEMECOINS } from './known-memecoins.mjs';
const raw = fs.readFileSync(new URL('../secrets.txt', import.meta.url), 'utf8');
const line = raw.split('\n').find((l) => /value x token/i.test(l));
const token = line.match(/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/)[1];
const base = `https://johnb-mainnet-2781.mainnet.rpcpool.com/${token}`;
async function rpc(id, m, p) {
  const r = await fetch(base, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id, method: m, params: p }) });
  return (await r.json()).result;
}
const now = Date.now() / 1000;
let ok = 0;
for (const [name, mint] of KNOWN_MEMECOINS) {
  try {
    const asset = await rpc(1, 'getAsset', { id: mint });
    const sym = asset?.content?.metadata?.symbol ?? asset?.token_info?.symbol;
    const sigs = await rpc(2, 'getSignaturesForAddress', [mint, { limit: 1 }]);
    const first = sigs?.[0];
    const age = first?.blockTime ? ((now - first.blockTime) / 86400).toFixed(0) + 'd' : '?';
    console.log(`${name.padEnd(8)} ${mint.slice(0, 8)} | DAS:${sym ?? '?'} | 1e-sig-txn ${age}`);
    if (sym) ok += 1;
  } catch (e) { console.log(`${name.padEnd(8)} ERR ${String(e).slice(0, 40)}`); }
}
console.log(`\nok/${KNOWN_MEMECOINS.length} met symbool; geschreven set: zie hieronder`);
const valid = KNOWN_MEMECOINS.map(([n, m]) => `${n},${m}`).join('\n');
fs.writeFileSync(new URL('../known-mints.txt', import.meta.url), valid);
console.log('known-mints.txt geschreven');

// 3-maanden-analyse: maak de mint-list aan (mint per regel)
const mintList = KNOWN_MEMECOINS.map(([, m]) => m).join('\n');
fs.writeFileSync(new URL('../known-mints-mintlist.txt', import.meta.url), mintList);
console.log('known-mints-mintlist.txt geschreven (' + KNOWN_MEMECOINS.length + ' mints)');