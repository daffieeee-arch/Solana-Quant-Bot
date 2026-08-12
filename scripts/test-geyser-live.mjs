// Live-test: geyser-gRPC Subscribe op het johnb-endpoint — hoelang duurt het tot
// de eerste block-update met txns, en welke programma-IDs zitten erin?
import grpc from '@grpc/grpc-js';
import protoLoader from '@grpc/proto-loader';
import fs from 'node:fs';

const raw = fs.readFileSync('/opt/data/solana-paper-scanner/secrets.txt', 'utf8');
const line = raw.split('\n').find((l) => /value x token/i.test(l));
const token = line.match(/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/)[1];
const host = 'johnb-mainnet-2781.mainnet.rpcpool.com:443';

const packageDef = protoLoader.loadSync('/tmp/geyser.proto', {
  keepCase: true, longs: String, enums: String, defaults: true, oneofs: true,
});
const proto = grpc.loadPackageDefinition(packageDef);
const meta = new grpc.Metadata();
meta.add('x-token', token);
const client = new proto.geyser.Geyser(host, grpc.credentials.createSsl(), {});

const req = {
  accounts: {},
  slots: { filterByCommitment: 'CONFIRMED' },
  transactions: { commitment: 'CONFIRMED', accountInclude: ['6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P', '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8', 'CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C'] },
  transactionsStatus: {},
  blocks: { commitment: 'CONFIRMED' },
};
const stream = client.Subscribe(req, meta);
let slots = 0, blocks = 0, txns = 0, msgs = 0;
const t0 = Date.now();
stream.on('data', (m) => {
  msgs += 1;
  if (m.slot) slots += 1;
  if (m.block) blocks += 1;
  if (m.transaction?.transaction) txns += 1;
  if (msgs <= 3) console.log('msg:', JSON.stringify(m).slice(0, 300));
});
stream.on('error', (e) => console.log('STREAM ERR:', e.message.slice(0, 150)));
setTimeout(() => {
  console.log(`\nna ${((Date.now() - t0) / 1000).toFixed(1)}s: msgs=${msgs} slots=${slots} blocks=${blocks} transaction_msgs=${txns}`);
  stream.cancel(); client.close(); process.exit(0);
}, 15000);