/** Synthetic display projection only. No authentic transaction data. */
import { parseInspection, type Inspection } from '../../src/mint-inspector/contract.js';
export const mint = '11111111111111111111111111111111';
export function fixtureInspection(): Inspection {
  const packages = ['a', 'b', 'c'].map((letter, i) => {
    const parent = letter.repeat(64), sourceId = i === 0 ? 'pilot' : 'context';
    return {
      package_id: `${sourceId}:${parent}`, bronze_record_sha256: parent, collection_source_id: sourceId,
      collection_batch_id: `batch-${i}`, collection_role: i === 0 ? 'ORIGINAL_SELECTION' : 'POSTHOC_DESCRIPTIVE_CONTEXT',
      slice_class: i === 0 ? 'RESEARCH_SAMPLING' : 'ENGINEERING_VALIDATION_ONLY',
      sample_identity: i === 0 ? { sample_class: 'RESEARCH_SAMPLING' } : null,
      slot: '10', transaction_index: String(i), transaction_status: i === 2 ? 'ERROR' : 'OK',
      transaction_error: i === 2 ? { InstructionError: ['0', 'FIXTURE_ERROR'] } : null,
      atomic_observation_package: true, effective_at: { slot: '10', transaction_index_in_slot: String(i) },
      signatures: ['synthetic-signature'], inclusion: { BALANCE_MINT: true, SILVER_FACT: i === 0 },
      bronze_disposition: 'DECODED', bronze_reason: null, decoder_source_sha256: 'd'.repeat(64),
      source: { raw_sha256: 'e'.repeat(64), receipt_sequence: '0', raw_section_offset: '10', raw_section_length: '20', transaction_node_cid_hex: '0171' },
      receipt: { sha256: 'f'.repeat(64), raw_sha256: 'e'.repeat(64), raw_bytes: '100', sequence: '0', path: 'published/0000000000/receipt.json' },
      balance_observations: [{ observation_index: '0', observation: { mint, side: 'PRE', amount_u64: '18446744073709551615', decimals: null, account_index: '2', exact_decimal_amount: null } }],
      silver_facts: i === 0 ? [{ record_sha256: '9'.repeat(64), record: { schema: 'SYNTHETIC_SILVER', bronze_record_sha256: parent,
        transaction_status: 'OK', atomic_observation_package: true, research_ready: false,
        event_reported: { mint_address: mint, is_buy: false, token_amount_raw_u64: '18446744073709551615', quote_amount_raw_u64: '9' },
        token_balance_context: null, quote_mint_identity: 'UNKNOWN' } }] : [],
      diagnostics: [{ kind: 'PROFILE_DIAGNOSIS', mint_attribution: 'UNKNOWN', json_pointer: '/fixture/0', outer_index: '0', inner_order: null, disposition: 'NOT_ADMITTED' }],
      instructions: [{ kind: 'DECLARED_TOP_LEVEL', outer_index: '0', inner_order: null }, { kind: 'RECORDED_CPI', outer_index: '0', inner_order: '0' }],
      diagnostic_inventory: { fixture: 'UNAVAILABLE' }, instruction_inventory: { instructions: 'RECORDED' },
    };
  });
  const counts = { transactions: '3', silver_facts: '1', balance_observations: '3', diagnoses: '3', instruction_references: '6', buys: '0', sells: '1',
    status: { OK: '2', ERROR: '1' }, by_role: {
      ORIGINAL_SELECTION: { transactions: '1', silver_facts: '1', balance_observations: '1', balance_packages: '1' },
      POSTHOC_DESCRIPTIVE_CONTEXT: { transactions: '2', silver_facts: '0', balance_observations: '2', balance_packages: '2' },
    } };
  return parseInspection({ schema: 'OF1_MINT_INSPECTOR_1', state: 'READY', inputs: {
    timeline: '1'.repeat(64), lifecycle: '2'.repeat(64), collection: '3'.repeat(64), plan: '4'.repeat(64),
  }, timeline: { schema: 'OF1_MINT_TIMELINE_1', mint, research_ready: false, display_complete: true,
    selection_class: 'POSTHOC_DESCRIPTIVE_LIFECYCLE_FRAGMENT', counts, transactions: packages, limits: ['SYNTHETIC_DISPLAY_FIXTURE'],
    bindings: { collection_sha256: '3'.repeat(64), plan_sha256: '4'.repeat(64), batches: packages.map(p => ({
      batch_id: p.collection_batch_id, source_id: p.collection_source_id, selected_slots: ['10'], parquet_manifest_sha256: '5'.repeat(64),
    })) }, collection: { plan_sha256: '4'.repeat(64), research_ready: false },
  }, lifecycle: { schema: 'B5_LIFECYCLE_ACCEPTANCE_PROPOSAL_1', mint, research_ready: false, counts,
    collection: { sha256: '3'.repeat(64), plan_sha256: '4'.repeat(64) },
    facts: [{ silver_record_sha256: '9'.repeat(64) }], phases: [{ phase: 'Creatie', status: 'NIET_AANTOONBAAR', evidence: 'Fixture unknown', limit: 'No lifecycle inference' }],
  } });
}
