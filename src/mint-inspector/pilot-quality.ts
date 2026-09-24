/** Presentation validation of an existing report, never Raw/Pump interpretation. */
import { check, exactTree, hash, list, object, uint, type InputHashes, type ObjectValue } from './contract.js';

export const PILOT_INPUT_NAMES = ['manifest', 'decoder0', 'decoder1', 'decoder2'] as const;
export type PilotInputName = typeof PILOT_INPUT_NAMES[number];
export const MAX_PILOT_BYTES = 256 * 1024;
export interface PilotQuality {
  schema: 'OF1_PILOT_QUALITY_VIEW_1';
  inputs: Record<PilotInputName, string>;
  manifest: ObjectValue & { counts: ObjectValue; range: ObjectValue; provenance: ObjectValue };
  decoders: ObjectValue[];
  operations?: ObjectValue;
}

export function parsePilotQuality(value: unknown, bindings: Pick<InputHashes, 'collection' | 'plan'>): PilotQuality {
  exactTree(value);
  const v = object(value), inputs = object(v.inputs), m = object(v.manifest);
  check(v.schema === 'OF1_PILOT_QUALITY_VIEW_1');
  check(Object.keys(inputs).sort().join(',') === [...PILOT_INPUT_NAMES].sort().join(','));
  PILOT_INPUT_NAMES.forEach(n => hash(inputs[n]));
  check(m.schema === 'OF1_RAW_BRONZE_SILVER_WALKING_SKELETON_1' && m.research_ready === false && m.slice_class === 'RESEARCH_SAMPLING');
  const p = object(m.provenance), range = object(m.range), sample = object(m.sample_identity), c = object(m.counts);
  check(p.collection_sha256 === bindings.collection && p.plan_sha256 === bindings.plan && p.research_ready === false);
  check(range.start_slot === '422669516' && range.end_slot_exclusive === '422669519');
  const slots = list(range.selected_slots);
  check(slots.join(',') === '422669516,422669517,422669518');
  check(sample.sample_class === 'RESEARCH_SAMPLING' && sample.start_slot === range.start_slot && sample.end_slot_exclusive === range.end_slot_exclusive);
  for (const key of ['selected_blocks', 'transactions', 'atomic_packages', 'decoded', 'failed_transactions', 'successful_transactions', 'silver_facts', 'silver_facts_on_failed_transactions']) uint(c[key]);
  check(c.selected_blocks === '3' && c.atomic_packages === c.transactions);
  check(BigInt(c.failed_transactions as string) + BigInt(c.successful_transactions as string) === BigInt(c.transactions as string));
  check(c.silver_facts_on_failed_transactions === '0' && c.failed_transactions_have_no_state_transition === true);
  for (const key of ['missing', 'unsupported', 'quarantined', 'top_level_instructions', 'recorded_cpi_instructions']) {
    if (c[key] !== undefined && c[key] !== null) uint(c[key]);
  }
  // Optional coverage stays unknown when absent. A recorded zero is Bronze-only.
  for (const gaps of [range.gap_slots, c.gap_slots]) if (gaps !== undefined && gaps !== null) {
    const g = list(gaps); g.forEach(uint); check(new Set(g).size === g.length);
    check(g.every(s => BigInt(s as string) >= 422669516n && BigInt(s as string) < 422669519n && !slots.includes(s)));
  }
  const logical = object(m.logical_identities);
  for (const [layer, count] of [['bronze', c.transactions], ['silver', c.silver_facts]]) {
    const id = object(logical[layer as string]); hash(id.ordered_logical_sha256); check(id.rows === count);
  }
  const source = object(p.selected_source), sourceBindings = object(source.bindings);
  check(source.source_id === 'pilot' && object(sourceBindings.sample_identity).sample_class === 'RESEARCH_SAMPLING');
  for (const key of ['manifest_sha256', 'aggregate_sha256', 'payload_manifest_sha256']) hash(sourceBindings[key]);
  const batches = list(p.selected_batches).map(object), receipts = list(p.selected_receipts).map(object), decoders = list(v.decoders).map(object);
  check(batches.length === 3 && receipts.length === 3 && decoders.length === 3);
  check(new Set(batches.map(b => b.batch_id)).size === 3 && new Set(receipts.map(r => r.sequence)).size === 3);
  const originals = list(sourceBindings.receipts).map(object);
  for (const r of receipts) {
    check(r.source_id === source.source_id); uint(r.sequence); uint(r.raw_bytes); hash(r.sha256); hash(r.raw_sha256);
    const matches = originals.filter(o => o.sequence === r.sequence);
    check(matches.length === 1 && ['path', 'raw_bytes', 'raw_sha256', 'sha256'].every(k => matches[0][k] === r[k]));
  }
  batches.forEach((b, index) => {
    check(b.source_id === source.source_id && list(b.selected_slots).join(',') === slots[index]);
    const seqs = list(b.receipt_sequences); check(seqs.length === 1 && seqs[0] === receipts[index].sequence);
    hash(b.manifest_sha256); hash(b.decoder_execution_sha256);
    const d = decoders[index];
    check(d.batch_id === b.batch_id && d.source_id === source.source_id && d.execution_sha256 === b.decoder_execution_sha256
      && d.execution_sha256 === inputs[PILOT_INPUT_NAMES[index + 1]]);
    for (const key of ['processed_at_unix_ms', 'finished_at_unix_ms']) if (d[key] !== undefined && d[key] !== null) uint(d[key]);
    for (const key of ['decoder_source_sha256', 'executable_sha256', 'lock_sha256']) hash(d[key]);
    const writer = object(b.writer);
    for (const key of ['source_sha256', 'executable_sha256', 'cargo_lock_sha256']) hash(writer[key]);
    check(list(p.writer_identities).some(w => {
      const candidate = object(w); return ['source_sha256', 'executable_sha256', 'cargo_lock_sha256'].every(k => candidate[k] === writer[k]);
    }));
  });
  if (v.operations !== undefined) {
    const o = object(v.operations), pins = object(o.inputs), a = list(o.acquisitions).map(object), report = object(o.report);
    check(Object.keys(pins).sort().join(',') === 'acquisition0,acquisition1,acquisition2,reportExecution,reportSource');
    Object.values(pins).forEach(hash); check(a.length === 3);
    a.forEach((r, i) => {
      check(r.receipt_sha256 === receipts[i].sha256 && r.receipt_sha256 === pins[`acquisition${i}`]
        && r.raw_sha256 === receipts[i].raw_sha256 && r.raw_bytes === receipts[i].raw_bytes && r.sequence === receipts[i].sequence && r.slot === slots[i]);
      for (const k of ['acquired_at_unix_ms', 'range_start', 'range_end_exclusive']) uint(r[k]);
      check(BigInt(r.range_end_exclusive as string) - BigInt(r.range_start as string) === BigInt(r.raw_bytes as string));
    });
    check(report.skeleton_sha256 === pins.reportSource);
    check(typeof report.elapsed_seconds === 'string' && /^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:e[+-]?[0-9]+)?$/.test(report.elapsed_seconds));
    for (const k of ['started_at_utc', 'completed_at_utc']) check(typeof report[k] === 'string' && Number.isFinite(Date.parse(report[k] as string)));
  }
  return value as PilotQuality;
}
