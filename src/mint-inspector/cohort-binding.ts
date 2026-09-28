import { createHash } from 'node:crypto';
import { check, list, object } from './contract.js';
import { parseDevelopmentCohort, same } from './development-cohort.js';
/** Node 22 JSON source text preserves integer lexemes before IEEE rounding.
 * This is JSON presentation, never Solana/Pump wire interpretation. */
export function exactNativeJson(text: string): unknown {
  return JSON.parse(text, (_key: string, value: unknown, context?: { source?: string }) => {
    if (typeof value !== 'number') return value;
    check(context?.source && /^-?(0|[1-9][0-9]*)$/.test(context.source));
    return context.source;
  });
}
export function bindDevelopmentCohort(reportBytes: Buffer, admissionBytes: Buffer) {
  const r = parseDevelopmentCohort(JSON.parse(reportBytes.toString('utf8'))), a = object(exactNativeJson(admissionBytes.toString('utf8')));
  check(createHash('sha256').update(admissionBytes).digest('hex') === r.native_admission_sha256
    && a.schema === 'OF1_B7_DEVELOPMENT_ADMISSION_1' && a.pins_sha256 === r.pins_sha256
    && a.selection_sha256 === r.selection_sha256 && a.research_ready === false
    && a.reader_source_sha256 === r.producer.native_source_sha256 && a.reader_binary_sha256 === r.producer.native_binary_sha256);
  const windows = list(a.windows).map(object); check(windows.length === 4);
  r.windows.forEach((w, i) => {
    const original = windows[i];
    for (const key of ['ordinal', 'collection_sha256', 'collection_path', 'sample_identity', 'counts', 'layers', 'coverage', 'slot_outcomes', 'parts', 'pump_layout_outcomes', 'diagnosis_denominator'] as const)
      check(same(w[key], original[key]));
    const nativeFacts = list(original.facts).map(object), facts = w.mints.flatMap(m => m.facts);
    check(nativeFacts.length === facts.length);
    const matched = new Set<string>();
    for (const f of facts) {
      const originalFact = nativeFacts.find(n => n.record_sha256 === f.record_sha256);
      check(originalFact && !matched.has(f.record_sha256)); matched.add(f.record_sha256);
      check(originalFact.canonical_record_json === f.canonical_record_json && originalFact.part_id === f.part_id
        && createHash('sha256').update(f.canonical_record_json).digest('hex') === f.record_sha256
        && same(f.record, exactNativeJson(f.canonical_record_json)) && same(f.record, originalFact.record));
    }
  });
  return r;
}
