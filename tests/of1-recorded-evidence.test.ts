import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { frameSources, historicalSourceReceipt, retainedSources } from './retained-of1-source-helper.js';

const root = process.cwd();
const base = resolve(root, 'schemas/acquisition/of1');
const hash = (bytes: Buffer | string) => createHash('sha256').update(bytes).digest('hex');
const reportBytes = readFileSync(resolve(base, 'recorded-car-verification.json'));
const report = JSON.parse(reportBytes.toString());
const execution = JSON.parse(readFileSync(resolve(base, 'recorded-car-verification-execution.json'), 'utf8'));
const origin = JSON.parse(readFileSync(resolve(base, 'epoch-978-slot-422496000.observed.provenance.json'), 'utf8'));

describe('retained authentic read-only verifier evidence (no acquisition)', () => {
  it('binds unchanged observed bytes and the exact receipt, separately from synthetic boundary cases', () => {
    const raw = Buffer.from(readFileSync(resolve(base, 'epoch-978-slot-422496000.observed.car.hex'), 'utf8').replace(/\s/g, ''), 'hex');
    expect(raw.length).toBe(45051);
    expect(hash(raw)).toBe('3d93337542751eaacecf039a2fb5384f700f226879616b3fb80117fb9d4a8ae6');
    expect(origin.raw_sha256).toBe(hash(raw));
    expect(origin.run_id).toBe(report.run_id);
    expect(report.bindings.receipts.find((r: any) => r.sequence === 4)).toMatchObject({
      raw_sha256: hash(raw), raw_bytes: raw.length, sha256: origin.original_receipt_sha256,
    });
  });

  it('binds the actual historical execution to preserved source bytes, without claiming Git build attestation', () => {
    expect(execution.report_sha256).toBe(hash(reportBytes));
    expect(execution.binary_sha256).toBe(report.verifier.binary_sha256);
    const historical = retainedSources();
    const indexes = historicalSourceReceipt.verifier.file_indexes;
    expect(indexes).toEqual(Array.from({ length: 13 }, (_, i) => i));
    const sourcePath = 'rust/of1-range-recorder/src/recorded_verification.rs';
    const paths = [...historical[0].toString().matchAll(/include_bytes!\("([^"]+)"\)/g)].map(m => resolve(root, dirname(sourcePath), m[1]));
    expect(paths).toEqual(indexes.map((i: number) => resolve(root, historicalSourceReceipt.files[i].path)));
    expect(hash(frameSources(indexes.map((i: number) => historical[i])))).toBe(report.verifier.source_sha256);
    expect(historicalSourceReceipt.verifier.framed_source_sha256).toBe(report.verifier.source_sha256);
    expect(execution.source_sha256).toBe(report.verifier.source_sha256);
    expect(execution.binary_sha256).not.toBe(origin.acquisition_executable_sha256);
  });

  it('binds new sample-aware sources separately instead of relabelling the historical verification', () => {
    const sourcePath = resolve(root, 'rust/of1-range-recorder/src/recorded_verification.rs');
    const source = readFileSync(sourcePath, 'utf8');
    const list = source.match(/let sources: &\[&\[u8\]\] = &\[([\s\S]*?)\n    \];/)?.[1];
    expect(list).toBeDefined();
    const tokens = [...list!.matchAll(/include_bytes!\("([^"]+)"\)|crate::sample::SELECTION_PLAN/g)];
    expect(tokens).toHaveLength(16);
    expect(tokens.filter(m => m[1] === 'sample.rs')).toHaveLength(1);
    expect(tokens.filter(m => m[1] === 'rate.rs')).toHaveLength(1);
    expect(tokens.filter(m => !m[1])).toHaveLength(1);
    const current = tokens.map(m => readFileSync(m[1] ? resolve(dirname(sourcePath), m[1]) : resolve(root, 'research/columnar-query/pilot-proposal.json')));
    expect(hash(frameSources(current))).not.toBe(report.verifier.source_sha256);
    expect(hash(current[tokens.findIndex(m => !m[1])])).toBe('df930707d0ece9915744aec7cf771c60e92f35298f2a6b4251aeb30d5a6d85a1');
    expect(() => retainedSources({ ...historicalSourceReceipt, framed_sha256: '0'.repeat(64) })).toThrow('hash mismatch');
    const changed = structuredClone(historicalSourceReceipt); changed.files[0].sha256 = '0'.repeat(64);
    expect(() => retainedSources(changed)).toThrow('identity mismatch');
  });

  it('keeps the original failure and all four result stages explicit', () => {
    expect(report.stages).toEqual({ capture: 'COMPLETE', raw_receipts: 'VERIFIED', car_slot: 'VERIFIED', domain_decoding: 'NOT_PERFORMED' });
    expect(report.prior_failure).toMatchObject({ error: 'CAR_CBOR_OR_ARCHIVAL_SCHEMA_INVALID',
      status: 'HISTORICAL_FAILURE_PRESERVED', binary_sha256: origin.acquisition_executable_sha256, raw_sha256: origin.raw_sha256 });
    expect(report.integrity.root_to_slot_membership).toBe('UNAVAILABLE');
    expect(report.integrity.whole_car_sha256_verified).toBe(false);
    expect(report.research_ready).toBe(false);
  });

  it('records the full bounded graph result without manufacturing decoded transactions or Pump observations', () => {
    expect(report.integrity.slots).toHaveLength(1);
    expect(report.integrity.slots[0]).toMatchObject({ slot: 422496000,
      archival_node_counts: { entry: 64, block: 1, rewards: 1, transaction: 0, dataframe: 0 },
      report: { verified_nodes: 66, verified_links: 65, captured_section_bytes: 45051,
        source_commit: origin.format_source.commit, domain_counts: 'UNAVAILABLE_NOT_DECODED_IN_B4',
        dataframe_payload_and_checksum: 'NOT_EVALUATED' } });
  });

  it('records repeatability and unchanged old run/history without a writer or network', () => {
    expect(execution.exit_codes).toEqual([0, 0]);
    expect(execution.deterministic_repeat).toBe(true);
    expect(execution.network_syscalls_denied).toBe(true);
    expect(execution.writer_resumed).toBe(false);
    expect(execution.original_run_files_verified).toBe(200);
    expect(execution.original_history_files_unchanged).toBe(25);
    expect(execution.before_run_inventory_sha256).toBe(execution.after_run_inventory_sha256);
  });

  it('binds the executed browser screenshot to this report and keeps its original failure visible', () => {
    const browser = JSON.parse(readFileSync(resolve(base, 'recorded-car-verification-browser.json'), 'utf8'));
    expect(hash(readFileSync(resolve(root, browser.screenshot)))).toBe(browser.screenshot_sha256);
    expect(browser.report_sha256).toBe(hash(reportBytes));
    expect(browser.run_id).toBe(report.run_id);
    expect(browser.stages).toEqual(report.stages);
    expect(browser.node_counts).toEqual(report.integrity.slots[0].archival_node_counts);
    expect(browser.visible_history).toBe(true);
    expect(browser.captured_deadline_label).toBe(true);
    expect(browser.request_origins).toEqual(['http://localhost:4173']);
    expect(browser.request_methods).toEqual(['GET']);
    expect(browser.acquisition_performed).toBe(false);
  });
});
