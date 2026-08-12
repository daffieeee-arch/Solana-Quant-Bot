// Volledige live-test: officiële Triton geyser-gRPC SDK (johnb-endpoint).
// Parsed de nested payload, herkent pump/raydium swaps in streams, 30s window.
import fs from 'node:fs';
import GeyserClient, { CommitmentLevel } from '@triton-one/yellowstone-grpc';

const raw = fs.readFileSync('/opt/data/solana-paper-scanner/secrets.txt', 'utf8');
const line = raw.split('\n').find((l) => /value x token/i.test(l));
const token = line.match(/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/)[1];
const endpoint = 'https://johnb-mainnet-2781.mainnet.rpcpool.com:443';

const PUMP = '6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P';
const RAYDIUM_AMM = '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8';
const RAYDIUM_CPMM = 'CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C';

const client = new GeyserClient(endpoint, token, {}, { enabled: true });
await client.connect();
console.log('verbonden, slot:', (await client.getSlot(CommitmentLevel.CONFIRMED)).slot);

const request = {
  accounts: {},
  slots: { slotMonitor: { filterByCommitment: true } },
  transactions: {
    pumpfun: { accountInclude: [PUMP], accountExclude: [], accountRequired: [] },
    raydium: { accountInclude: [RAYDIUM_AMM, RAYDIUM_CPMM], accountExclude: [], accountRequired: [] },
  },
  transactionsStatus: {},
  blocks: {},
  blocksMeta: {},
  entry: {},
  commitment: 0,
  accountsDataSlice: [],
  ping: undefined,
  fromSlot: undefined,
};

const stream = await client.subscribe(request);
let txCount = 0, pumpSwaps = 0, raydiumSwaps = 0, slotCount = 0;
const t0 = Date.now();
const payers = new Map();

function isSwapLog(logs) {
  return logs.some((l) => /Program log: Instruction: (Buy|Sell|Swap|Swap2)/.test(l));
}

stream.on('data', (msg) => {
  if (msg.slot) { slotCount += 1; return; }
  const outer = msg.transaction;
  if (!outer) return;
  const inner = outer.transaction; // {signature,isVote,transaction,meta,index}
  if (!inner?.transaction) return;
  txCount += 1;
  const logs = inner.meta?.logMessages ?? [];
  if (!isSwapLog(logs)) return;
  const sig = (inner.transaction.signatures?.[0] ?? '');
  const program = logs.find((l) => /^Program [A-Za-z0-9]+ invoke/.test(l))?.split(' ')[1] ?? '';
  const isPump = program === PUMP || logs.some((l) => l.includes('pump'));
  const label = isPump ? 'pump' : program === RAYDIUM_AMM ? 'raydium-amm' : program === RAYDIUM_CPMM ? 'raydium-cpmm' : program.slice(0, 8);
  if (isPump) pumpSwaps += 1; else raydiumSwaps += 1;
  if (pumpSwaps + raydiumSwaps <= 6) {
    console.log(`${label.padEnd(14)} ${sig.slice(0, 10)} | ${logs.filter((l) => /Instruction:/.test(l)).slice(0, 3).join(' ; ')}`);
  }
});
stream.on('error', (e) => console.log('STREAM ERR:', String(e.message ?? e).slice(0, 140)));
setTimeout(() => {
  const secs = (Date.now() - t0) / 1000;
  console.log(`\nna ${secs.toFixed(1)}s: txns=${txCount} (${(txCount / secs).toFixed(0)}/s) | pump-swaps=${pumpSwaps} raydium-swaps=${raydiumSwaps} slots=${slotCount}`);
  process.exit(0);
}, 30000);