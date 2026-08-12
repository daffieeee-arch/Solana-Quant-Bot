// Test 2: geyser-gRPC Subscribe met CALL-CREDENTIALS + correcte filter-structuur.
// Yellowstone geyser verwacht: transactions{accountInclude+accountExclude+commitment}
// of blocks{commitment}; slots{filterByCommitment}. Lurk 40s en tel message-types.
import grpc from '@grpc/grpc-js';
import protoLoader from '@grpc/proto-loader';
import fs from 'node:fs';

const raw = fs.readFileSync('/opt/data/solana-paper-scanner/secrets.txt', 'utf8');
const token = raw.split('\n').find((l) => /value x token/i.test(l)).match(/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/)[1];

const packageDef = protoLoader.loadSync('/tmp/geyser.proto', {
  keepCase: true, longs: String, enums: String, defaults: true, oneofs: true,
});
const proto = grpc.loadPackageDefinition(packageDef);
const callCreds = grpc.credentials.createFromMetadataGenerator((opts, cb) => {
  const md = new grpc.Metadata(); md.add('x-token', token); cb(null, md);
});
const client = new proto.geyser.Geyser(
  'johnb-mainnet-2781.mainnet.rpcpool.com:443',
  grpc.credentials.combineChannelCredentials(grpc.credentials.createSsl(), callCreds),
  {},
);
await new Promise((r) => client.waitForReady(6000, r));

// Correcte structuur volgens geyser.proto: alleen de filter velden invullen
const req = {
  slots: { filterByCommitment: 'CONFIRMED' },
  blocks: { commitment: 1 }, // CommitmentLevel.CONFIRMED = 1
};
const stream = client.Subscribe(req);
const counts = { slot: 0, block: 0, tx: 0, ping: 0, other: 0 };
stream.on('data', (m) => {
  const oneof = m.update_oneof;
  if (oneof === 'slot') counts.slot += 1;
  else if (oneof === 'block') counts.block += 1;
  else if (oneof === 'transaction') counts.tx += 1;
  else if (oneof === 'ping') counts.ping += 1;
  else counts.other += 1;
  if (oneof === 'block' && counts.block <= 2) {
    const b = m.block;
    console.log('BLOCK slot=', b.slot, 'txns=', b.transactions?.length ?? 0);
  }
});
stream.on('error', (e) => console.log('STREAM ERR:', e.message.slice(0, 120)));
setTimeout(() => {
  console.log(`\nna 40s: ${JSON.stringify(counts)}`);
  stream.cancel(); client.close(); process.exit(0);
}, 40000);