import grpc from '@grpc/grpc-js';
import protoLoader from '@grpc/proto-loader';
import fs from 'node:fs';

const raw = fs.readFileSync('/opt/data/solana-paper-scanner/secrets.txt', 'utf8');
const line = raw.split('\n').find((l) => /value x token/i.test(l));
const token = line.match(/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/)[1];
const host = 'johnb-mainnet-2781.mainnet.rpcpool.com:443';

const packageDef = protoLoader.loadSync('/tmp/old-faithful.proto', {
  keepCase: true, longs: String, enums: String, defaults: true, oneofs: true,
});
const proto = grpc.loadPackageDefinition(packageDef);
const svc = proto.OldFaithful?.OldFaithful;
if (!svc) { console.log('service niet gevonden in proto'); process.exit(1); }
const meta = new grpc.Metadata();
meta.add('x-token', token);
const client = new svc(host, grpc.credentials.createSsl(), {});
await new Promise((res) => client.waitForReady(7000, res));
console.log('OldFaithful gRPC VERBONDEN op', host);
client.GetVersion({}, meta, (err, res) => {
  if (err) { console.log('GetVersion fout:', err.message.slice(0, 150)); }
  else console.log('GetVersion:', JSON.stringify(res));
  // Probeer StreamTransactions met een slot-filter (1 slot terug van nu)
  try {
    client.GetSlot({}, meta, (err2, slotRes) => {
      if (err2) { console.log('GetSlot fout:', err2.message.slice(0, 100)); client.close(); return; }
      console.log('GetSlot:', JSON.stringify(slotRes));
      client.close();
    });
  } catch (e) { console.log('GetSlot ex:', e.message.slice(0, 80)); client.close(); }
});