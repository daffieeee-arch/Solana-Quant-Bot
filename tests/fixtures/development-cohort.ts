/** Synthetic UI/transport fixtures. Never authentic protocol or cohort evidence. */
import { createHash } from 'node:crypto';
import { COHORT_PINS, COHORT_PINS_SHA256 } from '../../src/mint-inspector/cohort-pins.js';
import type { DevelopmentCohort } from '../../src/mint-inspector/development-cohort.js';
const sha = (s: string) => createHash('sha256').update(s).digest('hex');
export function fixtureDevelopmentCohort() {
  const nativeWindows = COHORT_PINS.windows.map(pin => {
    const sample = { schema: 'OF1_B7_WINDOW_SAMPLE_1', sample_class: 'RESEARCH_SAMPLING', start_slot: pin.range[0], end_slot_exclusive: pin.range[1],
      b7: { cohort_role: 'DEVELOPMENT', phase: '1', window_ordinal: pin.ordinal, selection_sha256: COHORT_PINS.selection_sha256 } };
    const facts = Array.from({ length: Number(pin.counts.silver_facts) }, (_, n) => {
      const record = { effective_at: { slot: String(BigInt(pin.range[0]) + (n === 0 ? 7n : 8n)), transaction_index_in_slot: String(n) },
        schema: 'SYNTHETIC_UI_FIXTURE', transaction_status: 'OK', atomic_observation_package: true, sample_identity: sample,
        source: { run_id: `fixture-${pin.ordinal}`, bindings: { sample_identity: sample, receipt_sha256: 'f'.repeat(64) } },
        event_reported: { mint_address: 'A'.repeat(32), is_buy: n === 0, token_amount_raw_u64: '18446744073709551615', quote_amount_raw_u64: '0' },
        quote_mint_identity: 'UNKNOWN', quote_decimals: null, instruction_sha256: '1'.repeat(64), event_context: { event_cpi_sha256: '2'.repeat(64), selected_invocation: { outer_index: '1', inner_order: '0' } },
        bronze_record_sha256: sha(`${pin.ordinal}-${n}`) };
      const canonical_record_json = JSON.stringify(record);
      return { record, canonical_record_json, record_sha256: sha(canonical_record_json), part_id: '0' };
    });
    return { ordinal: pin.ordinal, collection_sha256: pin.sha256, collection_path: 'fixture-only/collection.json', sample_identity: sample,
      counts: pin.counts, layers: pin.layers, coverage: { all_selected_slots_accounted: true, missing_selected_raw_slots: '0' },
      slot_outcomes: Array.from({ length: 16 }, (_, n) => ({ slot: String(BigInt(pin.range[0]) + BigInt(n)), state: 'ACCOUNTED' })),
      parts: [{ part_id: '0', parquet_manifest_sha256: 'e'.repeat(64), writer: 'SYNTHETIC' }],
      pump_layout_outcomes: { UNSUPPORTED_FIXTURE: '2' }, diagnosis_denominator: 'Outcomes, not unique instructions', facts };
  });
  const admission = { schema: 'OF1_B7_DEVELOPMENT_ADMISSION_1', pins_sha256: COHORT_PINS_SHA256, selection_sha256: COHORT_PINS.selection_sha256,
    reader_source_sha256: 'a'.repeat(64), reader_binary_sha256: 'b'.repeat(64), research_ready: false, windows: nativeWindows };
  const admissionBytes = Buffer.from(JSON.stringify(admission));
  const windows = nativeWindows.map(w => {
    const facts = w.facts.map(f => ({ ...f, mint: f.record.event_reported.mint_address, side: f.record.event_reported.is_buy ? 'buy' : 'sell',
      half: f.record.event_reported.is_buy ? 'FIRST_8' : 'LAST_8', slot: f.record.effective_at.slot, transaction_index: f.record.effective_at.transaction_index_in_slot,
      package_id: `${f.record.source.run_id}:${f.record.bronze_record_sha256}` }));
    const { facts: _nativeFacts, ...rest } = w;
    return { ...rest, halves: { FIRST_8: { buy_facts: '1', sell_facts: '0', unique_mints: '1' }, LAST_8: { buy_facts: '0', sell_facts: String(facts.length - 1), unique_mints: '1' } },
      mint_count: '1', observed_pair_mints: '1', midpoint_slot: String(BigInt(w.sample_identity.start_slot) + 8n),
      mints: [{ mint: facts[0].mint, facts, observed_pair: { buy_fact_sha256: facts[0].record_sha256, sell_fact_sha256: facts[1].record_sha256,
        buy_package_id: facts[0].package_id, sell_package_id: facts[1].package_id }, pair_state: 'OBSERVED_ADMITTED_PAIR', negative_conclusion: 'UNAVAILABLE_SEMANTIC_COVERAGE_NOT_ESTABLISHED' }],
      semantic_coverage: { state: 'UNPROVEN', negative_conclusion_allowed: false }, missing_information: ['Quote units and execution opportunity unavailable.'] };
  });
  const report = { schema: 'OF1_B7_DEVELOPMENT_COHORT_1', state: 'READY', research_ready: false,
    selection_sha256: COHORT_PINS.selection_sha256, pins_sha256: COHORT_PINS_SHA256, native_admission_sha256: sha(admissionBytes.toString()),
    producer: { version: 'DEVELOPMENT_DESCRIPTIVE_PAIRS_1', python_sha256: 'c'.repeat(64), native_source_sha256: admission.reader_source_sha256, native_binary_sha256: admission.reader_binary_sha256 },
    totals: { blocks: '64', packages: '80541', failures: '9505', silver_facts: '122' }, windows, evaluation_access: 'DENIED', window_order: 'PREREGISTERED_ORDINAL_NOT_HISTORICAL_TIME' } as unknown as DevelopmentCohort;
  return { report, admissionBytes };
}
