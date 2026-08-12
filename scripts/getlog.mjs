import tls from 'node:tls';
import fs from 'node:fs';

const keyRaw = fs.readFileSync('/opt/data/solana-paper-scanner/secrets/truenasapikey.txt', 'utf8').trim();
const key = keyRaw.includes('\n') || keyRaw.includes(':')
  ? (keyRaw.split('\n').find((l) => /^(api_key|key|hermestruenas)\s*[:=]/.test(l)) ?? '').replace(/^[^:]*[:=]\s*/, '').trim()
  : keyRaw;

const HOST = '192.168.1.234';
const PORT = 443;
const sock = tls.connect({ host: HOST, port: PORT, rejectUnauthorized: false, servername: HOST });
let buf = '';
const pending = new Map();
let idc = 0;
function finish(s) { try { sock.end(); } catch {} console.log(s); }
function send(o) { sock.write(JSON.stringify(o) + '\n'); }
function rpc(method, params, id) { return new Promise((res, rej) => { pending.set(id, { res, rej }); send({ jsonrpc: '2.0', id, method, params }); }); }
sock.on('data', (d) => {
  buf += d.toString('utf8');
  let i;
  while ((i = buf.indexOf('\n')) >= 0) {
    const line = buf.slice(0, i); buf = buf.slice(i + 1);
    const t = line.trim(); if (!t) continue;
    try {
      const obj = JSON.parse(t);
      if (obj && obj.id && pending.has(obj.id)) { const p = pending.get(obj.id); pending.delete(obj.id); obj.error ? p.rej(new Error(JSON.stringify(obj.error))) : p.res(obj.result); }
    } catch {}
  }
});
sock.on('error', (e) => finish('SOCKERR ' + e.message));
(async () => {
  await rpc('auth.login_with_api_key', [key], ++idc);
  const inst = await rpc('app.get_instance', ['solana-bot', { extra: { retrieve_config: false } }], ++idc);
  const cid = inst?.active_workloads?.container_details?.[0]?.id;
  if (!cid) { finish('NO CID'); return; }
  // probeer app.log met container id
  try { const dl = await rpc('app.log', [{ name: 'solana-bot' }], ++idc); finish('LOG ' + JSON.stringify(dl).slice(0, 200)); return; } catch (e) {}
  try { const dl = await rpc('app.log', [cid, true], ++idc); finish('LOG2 ' + JSON.stringify(dl).slice(0, 300)); return; } catch (e) { finish('LOGFAIL ' + e.message); }
})().catch((e) => finish('ERR ' + e.message));