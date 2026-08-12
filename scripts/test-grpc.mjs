import grpc from '@grpc/grpc-js';
import protoLoader from '@grpc/proto-loader';
import fs from 'node:fs';

const raw = fs.readFileSync('/opt/data/solana-paper-scanner/secrets.txt', 'utf8');
const line = raw.split('\n').find((l) => /value x token/i.test(l));
const token = line.match(/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/)[1];
const endpoint = 'johnb-mainnet-2781.mainnet.rpcpool.com:443';

const packageDef = protoLoader.loadSync('/tmp/old-faithful.proto', {
  keepCase: true,
  longs: String,
  enums: String,
  defaults: true,
  oneofs: true,
});
const proto = grpc.loadPackageDefinition(packageDef);

const meta = new grpc.Metadata();
meta.add('x-token', token);

const client = new proto.OldFaithful.OldFaithful(endpoint, grpc.credentials.createSsl(), {
  'grpc.keepalive_time_ms': 10_000,
});
client.waitForReady(10000, (err) => {
  if (err) { console.log('NIET BEREIKBAAR:', err.message.slice(0, 200)); return; }
  console.log('gRPC verbonden!');
  client.GetVersion({}, meta, (err2, res) => {
    if (err2) { console.log('GetVersion fout:', err2.message.slice(0, 200)); return; }
    console.log('GetVersion:', JSON.stringify(res));
    client.close();
  });
});