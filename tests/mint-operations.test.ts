import { describe, expect, it } from 'vitest';
import { decoderClocks, operationalProjection, type OperationInputName } from '../src/mint-inspector/operations.js';
import { object } from '../src/mint-inspector/contract.js';
import { fixturePilotQuality } from './fixtures/pilot-quality.js';

// Synthetic operational binding fixtures, never authentic acquisition evidence.
function fixture() {
  const q = fixturePilotQuality(), p = q.manifest.provenance, source = object(p.selected_source), receipts = p.selected_receipts as Record<string, string>[];
  source.run_id = 'fixture-run';
  const values = {} as Record<OperationInputName, unknown>, pins = {} as Record<OperationInputName, string>;
  receipts.forEach((r, i) => {
    r.raw_bytes = String(100 + i); r.sha256 = String(i + 1).repeat(64);
    const name = `acquisition${i}` as OperationInputName; pins[name] = r.sha256;
    values[name] = { schema: 'OF1_ACQUISITION_RECEIPT_1', run_id: source.run_id, aggregate_sha256: object(source.bindings).aggregate_sha256,
      request: { sequence: i + 4, kind: { kind: 'CAR_RANGE', slot: 422669516 + i, start: i * 200, end_exclusive: i * 200 + 100 + i } },
      sha256: r.raw_sha256, response_entity_bytes: 100 + i, source_host: 'fixture.invalid', source_path: '/fixture.car', acquired_at: { wall_ms: 1000 + i } };
  });
  pins.reportSource = 'f'.repeat(64); pins.reportExecution = 'e'.repeat(64);
  values.reportSource = structuredClone(q.manifest); delete object(values.reportSource).research_ready;
  values.reportExecution = { schema: 'OF1_RAW_BRONZE_SILVER_WALKING_SKELETON_EXECUTION_1', provider_calls: false, skeleton_sha256: pins.reportSource,
    started_at_utc: '2026-01-01T00:00:00+00:00', completed_at_utc: '2026-01-01T00:00:02+00:00', elapsed_seconds: 2.125 };
  return { q, pins, values };
}
describe('operational receipt projection', () => {
  it('copies clocks and measured report duration without manufacturing download duration', () => {
    const { q, pins, values } = fixture(), before = JSON.stringify(values);
    const result = operationalProjection(q.manifest, pins, values);
    expect((result.acquisitions as Record<string, string>[]).map(r => r.raw_bytes)).toEqual(['100', '101', '102']);
    expect(object(result.report).elapsed_seconds).toBe('2.125');
    expect(JSON.stringify(result)).not.toContain('download_duration'); expect(JSON.stringify(values)).toBe(before);
    expect(decoderClocks({ processed_at_unix_ms: '18446744073709551615', finished_at_unix_ms: '0', operational_timestamps_are_not_features: true }))
      .toEqual({ processed_at_unix_ms: '18446744073709551615', finished_at_unix_ms: '0' }); // wall clocks may step backwards
    expect(decoderClocks({})).toEqual({ processed_at_unix_ms: null, finished_at_unix_ms: null });
  });
  it.each(['unsafe-integer', 'wrong-source', 'wrong-receipt', 'wrong-bytes', 'overlap', 'wrong-report', 'wrong-manifest', 'promoted-report', 'invalid-elapsed'])('rejects %s before publication', variant => {
    const { q, pins, values } = fixture(), a = object(values.acquisition0);
    if (variant === 'unsafe-integer') object(a.acquired_at).wall_ms = Number.MAX_SAFE_INTEGER + 1;
    if (variant === 'wrong-source') a.run_id = 'another-run';
    if (variant === 'wrong-receipt') pins.acquisition0 = pins.acquisition1;
    if (variant === 'wrong-bytes') a.response_entity_bytes = 99;
    if (variant === 'overlap') { const k = object(object(values.acquisition1).request).kind; object(k).start = 0; object(k).end_exclusive = 101; }
    if (variant === 'wrong-report') object(values.reportExecution).skeleton_sha256 = '0'.repeat(64);
    if (variant === 'wrong-manifest') object(object(values.reportSource).counts).transactions = '999';
    if (variant === 'promoted-report') object(values.reportSource).research_ready = true;
    if (variant === 'invalid-elapsed') object(values.reportExecution).elapsed_seconds = -1;
    expect(() => operationalProjection(q.manifest, pins, values)).toThrow('INSPECTOR_CONTRACT_INVALID');
  });
  it.each([{ processed_at_unix_ms: 42 }, { processed_at_unix_ms: '42' }, { processed_at_unix_ms: 'invalid', operational_timestamps_are_not_features: true }])('rejects ambiguous decoder clocks', value => {
    expect(() => decoderClocks(value)).toThrow();
  });
});
