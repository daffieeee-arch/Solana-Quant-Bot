import { describe, expect, it } from 'vitest';
import { object } from '../src/mint-inspector/contract.js';
import { parsePilotQuality } from '../src/mint-inspector/pilot-quality.js';
import { fixturePilotQuality } from './fixtures/pilot-quality.js';
const bindings = { collection: 'a'.repeat(64), plan: 'a'.repeat(64) };
describe('existing pilot quality presentation contract', () => {
  it('preserves counts, string u64 values and unknown coverage without deriving facts', () => {
    const v = fixturePilotQuality(); v.manifest.counts.quarantined = null; delete v.manifest.counts.missing;
    const before = JSON.stringify(v);
    expect(parsePilotQuality(v, bindings)).toBe(v);
    expect(JSON.stringify(v)).toBe(before); expect(before).toContain('18446744073709551615');
    expect(v.manifest.counts.quarantined).toBeNull(); expect(v.manifest.counts.missing).toBeUndefined();
  });
  it.each(['collection', 'context', 'range', 'count', 'failed-fact', 'number', 'receipt', 'decoder', 'logical'])('rejects inconsistent %s evidence', variant => {
    const v = fixturePilotQuality(), p = v.manifest.provenance;
    if (variant === 'collection') p.collection_sha256 = 'f'.repeat(64);
    if (variant === 'context') v.manifest.slice_class = 'ENGINEERING_VALIDATION_ONLY';
    if (variant === 'range') v.manifest.range.end_slot_exclusive = '422669535';
    if (variant === 'count') v.manifest.counts.successful_transactions = '3002';
    if (variant === 'failed-fact') v.manifest.counts.silver_facts_on_failed_transactions = '1';
    if (variant === 'number') (v.manifest.counts as Record<string, unknown>).transactions = 3224;
    if (variant === 'receipt') object((p.selected_receipts as unknown[])[0]).source_id = 'context';
    if (variant === 'decoder') v.decoders[1].execution_sha256 = v.decoders[0].execution_sha256;
    if (variant === 'logical') object(object(v.manifest.logical_identities).bronze).rows = '3223';
    expect(() => parsePilotQuality(v, bindings)).toThrow('INSPECTOR_CONTRACT_INVALID');
  });
});
