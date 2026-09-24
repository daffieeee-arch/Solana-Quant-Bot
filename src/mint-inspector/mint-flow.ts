/** Validation and binding only. Descriptive arithmetic belongs to Python. */
import { check, exactTree, hash, INPUT_NAMES, list, object, uint, type Inspection } from './contract.js';

export const FLOW_GROUPS = ['ALL', 'ORIGINAL_SELECTION', 'POSTHOC_DESCRIPTIVE_CONTEXT'] as const;
export type FlowGroup = typeof FLOW_GROUPS[number];
export const FLOW_METRICS = ['buy_token_raw', 'sell_token_raw', 'gross_token_raw', 'net_token_raw',
  'unique_event_users', 'buy_facts', 'sell_facts', 'facts'] as const;
export type FlowTotals = Record<typeof FLOW_METRICS[number], string>;
export type FlowPackage = {
  package_id: string; bronze_record_sha256: string; collection_source_id: string;
  collection_role: string; slice_class: string; slot: string; transaction_index: string; transaction_status: string;
  fact_hashes: string[]; package_totals: FlowTotals; cumulative: Record<FlowGroup, FlowTotals>;
};
export interface MintFlow {
  sha256: string;
  report: {
    schema: 'OF1_MINT_FLOW_1'; state: 'READY'; research_ready: false; mint: string;
    selection_class: 'POSTHOC_DESCRIPTIVE_LIFECYCLE_FRAGMENT'; inputs: Inspection['inputs'];
    producer: { name: string; version: string; source_sha256: Record<string, string> };
    quote_volume: { state: 'UNAVAILABLE'; quote_mint_identity: 'UNKNOWN'; quote_decimals: null; amount_raw: null };
    totals: Record<FlowGroup, FlowTotals>; packages: FlowPackage[];
  };
}
function keys(value: Record<string, unknown>, expected: readonly string[]) {
  check(Object.keys(value).sort().join(',') === [...expected].sort().join(','));
}
function totals(value: unknown): void {
  const t = object(value); keys(t, FLOW_METRICS);
  for (const key of FLOW_METRICS) {
    if (key === 'net_token_raw') check(typeof t[key] === 'string' && /^-?(0|[1-9][0-9]{0,39})$/.test(t[key]) && t[key] !== '-0');
    else uint(t[key]);
  }
}
function groups(value: unknown): void {
  const g = object(value); keys(g, FLOW_GROUPS); FLOW_GROUPS.forEach(key => totals(g[key]));
}

export function parseMintFlow(value: unknown, inspection: Inspection): MintFlow {
  exactTree(value);
  const envelope = object(value); hash(envelope.sha256);
  const r = object(envelope.report), t = inspection.timeline;
  check(r.schema === 'OF1_MINT_FLOW_1' && r.state === 'READY' && r.research_ready === false
    && r.selection_class === t.selection_class && r.mint === t.mint);
  const inputs = object(r.inputs); keys(inputs, INPUT_NAMES);
  INPUT_NAMES.forEach(name => check(inputs[name] === inspection.inputs[name]));
  const producer = object(r.producer), sources = object(producer.source_sha256);
  check(producer.name === 'research/columnar-query/mint_flow.py' && producer.version === 'ADMITTED_EVENT_TOKEN_FLOW_1');
  keys(sources, ['mint_flow.py', 'mint_timeline.py', 'manifest_reader.py']); Object.values(sources).forEach(hash);
  const quote = object(r.quote_volume);
  check(quote.state === 'UNAVAILABLE' && quote.quote_mint_identity === 'UNKNOWN' && quote.quote_decimals === null && quote.amount_raw === null);
  groups(r.totals);
  const rows = list(r.packages); check(rows.length === t.transactions.length && rows.length > 0);
  for (let i = 0; i < rows.length; i++) {
    const row = object(rows[i]), p = t.transactions[i];
    for (const key of ['package_id', 'bronze_record_sha256', 'collection_source_id', 'collection_role',
      'slice_class', 'slot', 'transaction_index', 'transaction_status'] as const) check(row[key] === p[key]);
    const refs = list(row.fact_hashes);
    check(refs.length === p.silver_facts.length && refs.every((h, j) => h === p.silver_facts[j].record_sha256));
    totals(row.package_totals); groups(row.cumulative);
    check(object(row.package_totals).facts === String(refs.length));
    // Guard producer preconditions on the exact registered facts, without
    // recomputing any sum, prefix or address statistic in TypeScript/browser.
    for (const f of p.silver_facts) {
      const e = f.record.event_reported, amount = e.token_amount_raw_u64;
      check(typeof amount === 'string' && /^(0|[1-9][0-9]{0,19})$/.test(amount) && BigInt(amount) <= 18446744073709551615n);
      check(typeof e.user_address === 'string' && e.user_address.length > 0 && e.user_address.length <= 128 && e.user_address.trim() === e.user_address);
      check(f.record.quote_mint_identity === 'UNKNOWN' && f.record.quote_decimals === null);
    }
  }
  const last = object(object(rows[rows.length - 1]).cumulative), full = object(r.totals);
  for (const group of FLOW_GROUPS) for (const metric of FLOW_METRICS) check(object(last[group])[metric] === object(full[group])[metric]);
  check(object(full.ALL).facts === t.counts.silver_facts);
  for (const role of FLOW_GROUPS.slice(1)) check(object(full[role]).facts === (t.counts.by_role[role]?.silver_facts ?? '0'));
  return value as MintFlow;
}
