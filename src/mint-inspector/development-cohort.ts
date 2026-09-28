/** Display-only contract. Native exact-snapshot admission owns record eligibility. */
import { check, exactTree, hash, list, object, uint, type ObjectValue } from './contract.js';
import { COHORT_PINS, COHORT_PINS_SHA256 } from './cohort-pins.js';
export const MAX_COHORT_BYTES = 16 * 1024 * 1024;
export type CohortFact = ObjectValue & { record_sha256: string; canonical_record_json: string; record: ObjectValue;
  mint: string; side: 'buy' | 'sell'; half: 'FIRST_8' | 'LAST_8'; slot: string; transaction_index: string; package_id: string; part_id: string };
export interface CohortMint { mint: string; facts: CohortFact[]; observed_pair: null | { buy_fact_sha256: string; sell_fact_sha256: string; buy_package_id: string; sell_package_id: string }; pair_state: string; negative_conclusion: string }
export interface CohortWindow {
  ordinal: string; collection_sha256: string; collection_path: string; sample_identity: ObjectValue;
  counts: Record<string, string>; layers: ObjectValue; coverage: ObjectValue; slot_outcomes: ObjectValue[];
  halves: Record<'FIRST_8' | 'LAST_8', { buy_facts: string; sell_facts: string; unique_mints: string }>;
  mints: CohortMint[]; mint_count: string; observed_pair_mints: string; midpoint_slot: string;
  parts: ObjectValue[]; pump_layout_outcomes: ObjectValue | null; diagnosis_denominator: string | null;
  semantic_coverage: ObjectValue; missing_information: string[];
}
export interface DevelopmentCohort {
  schema: 'OF1_B7_DEVELOPMENT_COHORT_1'; state: 'READY'; research_ready: false;
  selection_sha256: string; pins_sha256: string; native_admission_sha256: string; producer: ObjectValue;
  totals: Record<string, string>; windows: CohortWindow[];
}
/** Key-order independent equality of exact JSON; never interprets protocol fields. */
export function same(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (a === null || b === null || typeof a !== 'object' || typeof b !== 'object') return false;
  if (Array.isArray(a) || Array.isArray(b)) return Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((v, i) => same(v, b[i]));
  const x = object(a), y = object(b), keys = Object.keys(x);
  return keys.length === Object.keys(y).length && keys.every(k => Object.hasOwn(y, k) && same(x[k], y[k]));
}
export function parseDevelopmentCohort(value: unknown): DevelopmentCohort {
  exactTree(value, 0, { left: 500000 });
  const r = object(value);
  check(r.schema === 'OF1_B7_DEVELOPMENT_COHORT_1' && r.state === 'READY' && r.research_ready === false
    && r.selection_sha256 === COHORT_PINS.selection_sha256 && r.pins_sha256 === COHORT_PINS_SHA256
    && r.evaluation_access === 'DENIED' && r.window_order === 'PREREGISTERED_ORDINAL_NOT_HISTORICAL_TIME');
  hash(r.native_admission_sha256);
  const producer = object(r.producer);
  check(producer.version === 'DEVELOPMENT_DESCRIPTIVE_PAIRS_1');
  for (const k of ['python_sha256', 'native_source_sha256', 'native_binary_sha256']) hash(producer[k]);
  const windows = list(r.windows).map(object), seen = new Set<string>(); check(windows.length === 4);
  for (const [i, w] of windows.entries()) {
    const pin = COHORT_PINS.windows[i], s = object(w.sample_identity), b = object(s.b7);
    check(w.ordinal === pin.ordinal && w.collection_sha256 === pin.sha256 && same(w.counts, pin.counts) && same(w.layers, pin.layers));
    check(s.schema === 'OF1_B7_WINDOW_SAMPLE_1' && s.sample_class === 'RESEARCH_SAMPLING'
      && b.cohort_role === 'DEVELOPMENT' && b.window_ordinal === pin.ordinal && b.phase === '1'
      && b.selection_sha256 === COHORT_PINS.selection_sha256 && s.start_slot === pin.range[0] && s.end_slot_exclusive === pin.range[1]);
    check(w.midpoint_slot === String(BigInt(pin.range[0]) + 8n));
    const coverage = object(w.coverage), semantic = object(w.semantic_coverage);
    check(coverage.all_selected_slots_accounted === true && coverage.missing_selected_raw_slots === '0'
      && semantic.state === 'UNPROVEN' && semantic.negative_conclusion_allowed === false);
    check(list(w.slot_outcomes).length === 16);
    list(w.slot_outcomes).forEach((entry, n) => { const slot = object(entry); check(slot.slot === String(BigInt(pin.range[0]) + BigInt(n)) && slot.state === 'ACCOUNTED'); });
    const parts = list(w.parts).map(object), mints = list(w.mints).map(object), windowFacts: CohortFact[] = [];
    check(parts.length > 0 && parts.length <= 2048 && w.mint_count === String(mints.length));
    parts.forEach((part, n) => { check(part.part_id === String(n)); hash(part.parquet_manifest_sha256); });
    let previousMint = '';
    for (const m of mints) {
      check(typeof m.mint === 'string' && /^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(m.mint) && m.mint > previousMint); previousMint = m.mint;
      check(m.negative_conclusion === 'UNAVAILABLE_SEMANTIC_COVERAGE_NOT_ESTABLISHED');
      const facts = list(m.facts).map(object); check(facts.length > 0);
      let previous: [bigint, bigint] | undefined;
      for (const f of facts) {
        hash(f.record_sha256); check(!seen.has(f.record_sha256)); seen.add(f.record_sha256);
        uint(f.slot); uint(f.transaction_index); uint(f.part_id);
        const slot = BigInt(f.slot), tx = BigInt(f.transaction_index);
        check(slot >= BigInt(pin.range[0]) && slot < BigInt(pin.range[1]) && BigInt(f.part_id) < BigInt(parts.length));
        check(!previous || slot > previous[0] || slot === previous[0] && tx >= previous[1]); previous = [slot, tx];
        check(f.half === (slot < BigInt(w.midpoint_slot as string) ? 'FIRST_8' : 'LAST_8') && f.mint === m.mint);
        const record = object(f.record), event = object(record.event_reported), source = object(record.source);
        check(same(record.sample_identity, s) && same(object(source.bindings).sample_identity, s)
          && record.transaction_status === 'OK' && record.atomic_observation_package === true);
        hash(record.bronze_record_sha256);
        check(f.package_id === `${source.run_id}:${record.bronze_record_sha256}` && event.mint_address === m.mint
          && typeof event.is_buy === 'boolean' && f.side === (event.is_buy ? 'buy' : 'sell'));
        check(object(record.effective_at).slot === f.slot && object(record.effective_at).transaction_index_in_slot === f.transaction_index);
        uint(event.token_amount_raw_u64); uint(event.quote_amount_raw_u64);
        check(BigInt(event.token_amount_raw_u64) < 2n ** 64n && BigInt(event.quote_amount_raw_u64) < 2n ** 64n && typeof f.canonical_record_json === 'string');
        windowFacts.push(f as CohortFact);
      }
      if (m.observed_pair === null) check(m.pair_state === 'NO_PAIR_IN_ADMITTED_FACTS');
      else {
        const pair = object(m.observed_pair), buy = facts.find(f => f.record_sha256 === pair.buy_fact_sha256), sell = facts.find(f => f.record_sha256 === pair.sell_fact_sha256);
        check(m.pair_state === 'OBSERVED_ADMITTED_PAIR' && buy && sell && buy.side === 'buy' && buy.half === 'FIRST_8' && sell.side === 'sell' && sell.half === 'LAST_8'
          && buy.package_id !== sell.package_id && buy.package_id === pair.buy_package_id && sell.package_id === pair.sell_package_id);
      }
    }
    check(String(windowFacts.length) === pin.counts.silver_facts);
    check(w.observed_pair_mints === String(mints.filter(m => m.observed_pair !== null).length));
    for (const half of ['FIRST_8', 'LAST_8']) {
      const counts = object(object(w.halves)[half]); for (const k of ['buy_facts', 'sell_facts', 'unique_mints']) uint(counts[k]);
    }
    check(list(w.missing_information).length > 0);
  }
  for (const k of ['blocks', 'packages', 'failures', 'silver_facts'] as const)
    check(object(r.totals)[k] === COHORT_PINS.windows.reduce((sum, p) => sum + BigInt(p.counts[k]), 0n).toString());
  return value as DevelopmentCohort;
}
