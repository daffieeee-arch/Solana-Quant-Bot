/** Display contract over OF1_MINT_TIMELINE_1; no wire decoding or new facts. */
export type Json = null | boolean | string | Json[] | { [key: string]: Json };
export type ObjectValue = { [key: string]: Json };
export const MAX_RESPONSE_BYTES = 8 * 1024 * 1024;
export const INPUT_NAMES = ['timeline', 'lifecycle', 'collection', 'plan'] as const;
export type InputName = typeof INPUT_NAMES[number];
export type InputHashes = Record<InputName, string>;
export type Trade = { record_sha256: string; record: ObjectValue & {
  event_reported: ObjectValue; token_balance_context: ObjectValue | null;
  schema: string; bronze_record_sha256: string; transaction_status: string;
} };
export type Package = ObjectValue & {
  package_id: string; bronze_record_sha256: string; collection_source_id: string;
  collection_batch_id: string; collection_role: string; slice_class: string;
  slot: string; transaction_index: string; transaction_status: string;
  transaction_error: Json; atomic_observation_package: true;
  signatures: string[]; inclusion: Record<string, boolean>;
  balance_observations: Array<{ observation_index: string; observation: ObjectValue }>;
  silver_facts: Trade[]; diagnostics: ObjectValue[]; instructions: ObjectValue[];
  receipt: ObjectValue; source: ObjectValue; effective_at: ObjectValue;
};
export interface Timeline {
  schema: 'OF1_MINT_TIMELINE_1'; mint: string; research_ready: false;
  selection_class: 'POSTHOC_DESCRIPTIVE_LIFECYCLE_FRAGMENT'; display_complete: true;
  counts: ObjectValue & { transactions: string; silver_facts: string; balance_observations: string;
    status: Record<string, string>; by_role: Record<string, Record<string, string>> };
  bindings: ObjectValue & { collection_sha256: string; plan_sha256: string; batches: ObjectValue[] };
  collection: ObjectValue; limits: string[]; transactions: Package[];
}
export interface Lifecycle {
  schema: 'B5_LIFECYCLE_ACCEPTANCE_PROPOSAL_1'; mint: string; research_ready: false;
  counts: ObjectValue; collection: ObjectValue; facts: ObjectValue[];
  phases: Array<{ phase: string; status: string; evidence: string; limit: string }>;
}
export interface Inspection {
  schema: 'OF1_MINT_INSPECTOR_1'; state: 'READY'; inputs: InputHashes;
  timeline: Timeline; lifecycle: Lifecycle;
}
export class ContractError extends Error {
  constructor() { super('INSPECTOR_CONTRACT_INVALID'); }
}
export function check(ok: unknown): asserts ok { if (!ok) throw new ContractError(); }
export function object(value: unknown): Record<string, unknown> {
  check(value !== null && typeof value === 'object' && !Array.isArray(value));
  return value as Record<string, unknown>;
}
export function list(value: unknown): unknown[] { check(Array.isArray(value) && value.length <= 10000); return value; }
function text(value: unknown): asserts value is string { check(typeof value === 'string' && value.length <= 262144); }
export function hash(value: unknown): asserts value is string { check(typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)); }
export function uint(value: unknown): asserts value is string { check(typeof value === 'string' && /^(0|[1-9][0-9]{0,39})$/.test(value)); }
function equalCount(value: unknown, count: number): void { uint(value); check(value === String(count)); }

/** Reject numeric JSON anywhere, before display can round an integer. Keep nulls. */
export function exactTree(value: unknown, depth = 0, budget = { left: 100000 }): void {
  check(depth <= 32 && --budget.left >= 0);
  if (value === null || typeof value === 'boolean') return;
  if (typeof value === 'string') { text(value); return; }
  if (Array.isArray(value)) { list(value).forEach(v => exactTree(v, depth + 1, budget)); return; }
  for (const [key, child] of Object.entries(object(value))) {
    check(key.length <= 256); exactTree(child, depth + 1, budget);
  }
}
export function packageIdentity(p: Package): string { return `${p.collection_source_id}:${p.bronze_record_sha256}`; }

export function parseInspection(value: unknown): Inspection {
  exactTree(value);
  const envelope = object(value);
  check(envelope.schema === 'OF1_MINT_INSPECTOR_1' && envelope.state === 'READY');
  const inputs = object(envelope.inputs); INPUT_NAMES.forEach(name => hash(inputs[name]));
  const t = object(envelope.timeline), l = object(envelope.lifecycle);
  check(t.schema === 'OF1_MINT_TIMELINE_1' && l.schema === 'B5_LIFECYCLE_ACCEPTANCE_PROPOSAL_1');
  check(t.research_ready === false && l.research_ready === false && t.display_complete === true);
  check(t.selection_class === 'POSTHOC_DESCRIPTIVE_LIFECYCLE_FRAGMENT');
  text(t.mint); check(/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(t.mint) && l.mint === t.mint);
  const binding = object(t.bindings), counts = object(t.counts), collection = object(t.collection);
  check(binding.collection_sha256 === inputs.collection && binding.plan_sha256 === inputs.plan);
  check(object(l.collection).sha256 === inputs.collection && object(l.collection).plan_sha256 === inputs.plan);
  check(collection.plan_sha256 === inputs.plan && collection.research_ready === false);
  const batches = list(binding.batches).map(object);
  const packages = list(t.transactions).map(object);
  const identities = new Set<string>(), facts = new Set<string>();
  let previous: [bigint, bigint, string] | undefined;
  let balances = 0, diagnoses = 0, instructions = 0;
  const statuses: Record<string, number> = { OK: 0, ERROR: 0 };
  const roles: Record<string, { transactions: number; silver_facts: number; balance_observations: number; balance_packages: number }> = {};
  for (const p of packages) {
    hash(p.bronze_record_sha256); text(p.collection_source_id); text(p.package_id);
    uint(p.slot); uint(p.transaction_index);
    check(p.atomic_observation_package === true);
    check(p.package_id === `${p.collection_source_id}:${p.bronze_record_sha256}` && !identities.has(p.package_id));
    identities.add(p.package_id);
    const pos: [bigint, bigint, string] = [BigInt(p.slot), BigInt(p.transaction_index), p.package_id];
    if (previous) check(pos[0] > previous[0] || (pos[0] === previous[0] && (pos[1] > previous[1]
      || (pos[1] === previous[1] && pos[2] > previous[2]))));
    previous = pos;
    const effective = object(p.effective_at);
    check(effective.slot === p.slot && effective.transaction_index_in_slot === p.transaction_index);
    check(p.collection_role === 'ORIGINAL_SELECTION' || p.collection_role === 'POSTHOC_DESCRIPTIVE_CONTEXT');
    check(p.slice_class === (p.collection_role === 'ORIGINAL_SELECTION' ? 'RESEARCH_SAMPLING' : 'ENGINEERING_VALIDATION_ONLY'));
    if (p.collection_role === 'ORIGINAL_SELECTION') check(object(p.sample_identity).sample_class === 'RESEARCH_SAMPLING');
    const matches = batches.filter(b => b.batch_id === p.collection_batch_id && b.source_id === p.collection_source_id);
    check(matches.length === 1 && list(matches[0].selected_slots).includes(p.slot)); hash(matches[0].parquet_manifest_sha256);
    const receipt = object(p.receipt), source = object(p.source);
    hash(receipt.sha256); hash(receipt.raw_sha256); hash(source.raw_sha256);
    check(source.raw_sha256 === receipt.raw_sha256 && source.receipt_sequence === receipt.sequence);
    uint(source.raw_section_offset); uint(source.raw_section_length); uint(receipt.raw_bytes);
    check(BigInt(source.raw_section_offset) + BigInt(source.raw_section_length) <= BigInt(receipt.raw_bytes));
    text(source.transaction_node_cid_hex); text(receipt.path);
    list(p.signatures).forEach(text);
    const inclusion = object(p.inclusion); check(Object.values(inclusion).some(v => v === true));
    check(Object.values(inclusion).every(v => typeof v === 'boolean'));
    check(['OK', 'ERROR', 'UNKNOWN'].includes(String(p.transaction_status)));
    const status = String(p.transaction_status); statuses[status] = (statuses[status] ?? 0) + 1;
    const trades = list(p.silver_facts).map(object), observations = list(p.balance_observations).map(object);
    check(status === 'OK' || trades.length === 0);
    for (const f of trades) {
      hash(f.record_sha256); check(!facts.has(f.record_sha256)); facts.add(f.record_sha256);
      const r = object(f.record);
      check(r.bronze_record_sha256 === p.bronze_record_sha256 && r.atomic_observation_package === true
        && r.transaction_status === 'OK' && r.research_ready === false);
      text(r.schema); const event = object(r.event_reported);
      check(event.mint_address === t.mint && typeof event.is_buy === 'boolean');
      if (r.token_balance_context !== null) object(r.token_balance_context);
    }
    let lastObservation = -1n;
    for (const o of observations) {
      uint(o.observation_index); check(BigInt(o.observation_index) > lastObservation); lastObservation = BigInt(o.observation_index);
      const record = object(o.observation); check(record.mint === t.mint);
      check(['PRE', 'POST'].includes(String(record.side)));
      if (record.amount_u64 !== null) uint(record.amount_u64);
      if (record.decimals !== null) uint(record.decimals);
    }
    list(p.diagnostics).forEach(d => { const row = object(d); text(row.kind); text(row.mint_attribution); text(row.json_pointer); });
    list(p.instructions).forEach(i => { const row = object(i); uint(row.outer_index); if (row.inner_order !== null) uint(row.inner_order); text(row.kind); });
    balances += observations.length; diagnoses += list(p.diagnostics).length; instructions += list(p.instructions).length;
    const role = roles[String(p.collection_role)] ??= { transactions: 0, silver_facts: 0, balance_observations: 0, balance_packages: 0 };
    role.transactions++; role.silver_facts += trades.length; role.balance_observations += observations.length;
    role.balance_packages += observations.length > 0 ? 1 : 0;
  }
  equalCount(counts.transactions, packages.length); equalCount(counts.silver_facts, facts.size);
  equalCount(counts.balance_observations, balances); equalCount(counts.diagnoses, diagnoses); equalCount(counts.instruction_references, instructions);
  for (const [key, n] of Object.entries(statuses)) equalCount(object(counts.status)[key], n);
  check(Object.keys(object(counts.status)).every(key => key in statuses));
  for (const [role, values] of Object.entries(roles)) for (const [key, n] of Object.entries(values)) equalCount(object(object(counts.by_role)[role])[key], n);
  check(Object.keys(object(counts.by_role)).every(key => key in roles));
  list(t.limits).forEach(text);
  for (const key of ['transactions', 'silver_facts', 'balance_observations']) check(object(l.counts)[key] === counts[key]);
  const lifecycleFacts = list(l.facts).map(f => object(f).silver_record_sha256);
  check(lifecycleFacts.length === facts.size && new Set(lifecycleFacts).size === facts.size && lifecycleFacts.every(f => typeof f === 'string' && facts.has(f)));
  const phases = list(l.phases); check(phases.length > 0 && phases.length <= 16);
  phases.forEach(p => ['phase', 'status', 'evidence', 'limit'].forEach(key => text(object(p)[key])));
  return value as Inspection;
}
