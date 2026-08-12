import grpc from '@grpc/grpc-js';
import protoLoader from '@grpc/proto-loader';
import fs from 'node:fs';

const raw = fs.readFileSync('/opt/data/solana-paper-scanner/secrets.txt', 'utf8');
const line = raw.split('\n').find((l) => /value x token/i.test(l));
const token = line.match(/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/)[1];

for (const host of ['johnb-mainnet-2781.mainnet.rpcpool.com:443', 'api.rpcpool.com:443']) {
  try {
    const packageDef = protoLoader.loadSync('/tmp/geyser.proto', {
      keepCase: true, longs: String, enums: String, defaults: true, oneofs: true,
    });
    const proto = grpc.loadPackageDefinition(packageDef);
    const svc = proto.geyser?.Geyser;
    const Ctor = svc;
    const meta = new grpc.Metadata();
    meta.add('x-token', token);
    const client = new Ctor(host, grpc.credentials.createSsl(), {});
    await new Promise((res) => client.waitForReady(7000, res));
    console.log(host, '→ VERBONDEN, methoden:', Object.getPrototypeOf(client) ? Object.keys(Object.getPrototypeOf(client)).filter((k) => k && k !== 'constructor' && typeof client[k] === 'function').join(',') : '?');
    client.close();
  } catch (e) {
    console.log(host, '→ nee:', String(e.message || e).slice(0, 90));
  }
}