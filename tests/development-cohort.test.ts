import { describe, expect, it } from 'vitest';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { fixtureDevelopmentCohort } from './fixtures/development-cohort.js';
import { parseDevelopmentCohort } from '../src/mint-inspector/development-cohort.js';
import { bindDevelopmentCohort, exactNativeJson } from '../src/mint-inspector/cohort-binding.js';
import { COHORT_PINS, COHORT_PINS_SHA256 } from '../src/mint-inspector/cohort-pins.js';

describe('four DEVELOPMENT snapshot display admission', () => {
  it('shares the exact native pins and preserves original integer lexemes', () => {
    const bytes = readFileSync('rust/of1-bronze-decoder/sources/b7-development-cohort.json');
    expect(createHash('sha256').update(bytes).digest('hex')).toBe(COHORT_PINS_SHA256);
    expect(exactNativeJson(bytes.toString())).toEqual(COHORT_PINS);
    expect(exactNativeJson('{"a":18446744073709551615,"b":null}')).toEqual({ a: '18446744073709551615', b: null });
    expect(() => exactNativeJson('{"a":1.5}')).toThrow();
  });
  it('binds all facts, atomic parents, original strings and source files without rounding', () => {
    const { report, admissionBytes } = fixtureDevelopmentCohort();
    expect(bindDevelopmentCohort(Buffer.from(JSON.stringify(report)), admissionBytes)).toEqual(report);
    expect(report.windows[0].mints[0].facts[0].record.event_reported).toHaveProperty('token_amount_raw_u64', '18446744073709551615');
  });
  it.each(['role', 'mixed-window', 'changed-snapshot', 'duplicate-fact', 'failed', 'half-boundary', 'same-package-pair', 'missing-binding', 'unknown-erased', 'numeric', 'count'])('denies %s rather than showing a partial cohort', variant => {
    const { report } = fixtureDevelopmentCohort(), w = report.windows[0], m = w.mints[0], f = m.facts[0];
    if (variant === 'role') (w.sample_identity.b7 as any).cohort_role = 'RESERVED_EVALUATION';
    if (variant === 'mixed-window') report.windows[0] = report.windows[1];
    if (variant === 'changed-snapshot') w.collection_sha256 = '0'.repeat(64);
    if (variant === 'duplicate-fact') m.facts[1] = f;
    if (variant === 'failed') f.record.transaction_status = 'ERROR';
    if (variant === 'half-boundary') m.facts[1].half = 'FIRST_8';
    if (variant === 'same-package-pair') m.observed_pair!.sell_package_id = m.observed_pair!.buy_package_id;
    if (variant === 'missing-binding') delete f.record.source;
    if (variant === 'unknown-erased') w.semantic_coverage.negative_conclusion_allowed = true;
    if (variant === 'numeric') (f.record.event_reported as any).token_amount_raw_u64 = 18446744073709551615;
    if (variant === 'count') w.counts = { ...w.counts, silver_facts: '0' };
    expect(() => parseDevelopmentCohort(report)).toThrow();
  });
  it('denies a different native snapshot, changed original fact or altered display projection', () => {
    for (const mutation of ['admission', 'record', 'raw', 'parts']) {
      const { report, admissionBytes } = fixtureDevelopmentCohort();
      if (mutation === 'admission') report.native_admission_sha256 = '0'.repeat(64);
      if (mutation === 'record') (report.windows[0].mints[0].facts[0].record.event_reported as any).quote_amount_raw_u64 = '1';
      if (mutation === 'raw') report.windows[0].mints[0].facts[0].canonical_record_json = '{}';
      if (mutation === 'parts') report.windows[0].parts[0].writer = 'changed';
      expect(() => bindDevelopmentCohort(Buffer.from(JSON.stringify(report)), admissionBytes)).toThrow();
    }
  });
});
