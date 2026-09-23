// Synthetic display-contract fixture. Authentic acceptance uses the registered private manifest.
import type { PilotQuality } from '../../src/mint-inspector/pilot-quality.js';
export function fixturePilotQuality(): PilotQuality {
  const h = 'a'.repeat(64), hashes = ['b', 'c', 'd'].map(x => x.repeat(64));
  const sample = { sample_class: 'RESEARCH_SAMPLING', start_slot: '422669516', end_slot_exclusive: '422669519' };
  const writer = { source_sha256: h, executable_sha256: h, cargo_lock_sha256: h };
  const receipts = ['4', '5', '6'].map(sequence => ({ source_id: 'pilot', sequence, path: `published/${sequence}/receipt.json`, sha256: h, raw_sha256: h, raw_bytes: '18446744073709551615' }));
  return { schema: 'OF1_PILOT_QUALITY_VIEW_1', inputs: { manifest: h, decoder0: hashes[0], decoder1: hashes[1], decoder2: hashes[2] },
    manifest: { schema: 'OF1_RAW_BRONZE_SILVER_WALKING_SKELETON_1', research_ready: false, slice_class: 'RESEARCH_SAMPLING', sample_identity: sample,
      range: { start_slot: '422669516', end_slot_exclusive: '422669519', selected_slots: ['422669516', '422669517', '422669518'], gap_slots: [] },
      counts: { selected_blocks: '3', transactions: '3224', atomic_packages: '3224', decoded: '3224', failed_transactions: '223', successful_transactions: '3001',
        silver_facts: '7', silver_facts_on_failed_transactions: '0', failed_transactions_have_no_state_transition: true, gap_slots: [],
        quarantined: '0', unsupported: '0', missing: '0', top_level_instructions: '7279', recorded_cpi_instructions: '3708', unavailable_state: 'UNAVAILABLE' },
      logical_identities: { bronze: { rows: '3224', ordered_logical_sha256: h }, silver: { rows: '7', ordered_logical_sha256: h } }, files: [],
      provenance: { collection_sha256: h, plan_sha256: h, research_ready: false, selected_receipts: receipts,
        selected_source: { source_id: 'pilot', bindings: { sample_identity: sample, manifest_sha256: h, aggregate_sha256: h, payload_manifest_sha256: h, receipts } },
        selected_batches: hashes.map((hash, i) => ({ batch_id: `batch-00${i}`, source_id: 'pilot', selected_slots: [String(422669516 + i)], receipt_sequences: [String(4 + i)], decoder_execution_sha256: hash, manifest_sha256: h, writer })), writer_identities: [writer] } },
    decoders: hashes.map((hash, i) => ({ batch_id: `batch-00${i}`, source_id: 'pilot', execution_sha256: hash, decoder_source_sha256: h, executable_sha256: h, lock_sha256: h })) };
}
