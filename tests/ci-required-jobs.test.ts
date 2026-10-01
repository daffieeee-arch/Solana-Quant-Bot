import { describe, expect, it } from 'vitest';
import { assertRequiredCi } from '../scripts/assert-required-ci.mjs';

describe('required CI aggregate', () => {
  it('accepts only complete successful work', () => {
    expect(() => assertRequiredCi(JSON.stringify({ quality: { result: 'success' }, columnar: { result: 'success' } }))).not.toThrow();
  });
  it('refuses every unsuccessful result in either required job', () => {
    for (const job of ['quality', 'columnar']) {
      for (const result of ['failure', 'cancelled', 'skipped', 'pending', '', null]) {
        const jobs = { quality: { result: 'success' }, columnar: { result: 'success' }, [job]: { result } };
        expect(() => assertRequiredCi(JSON.stringify(jobs))).toThrow();
      }
    }
  });
  it('refuses absent, malformed or unexpected dependency sets', () => {
    for (const results of [null, [], {}, { quality: { result: 'success' } }, { columnar: { result: 'success' } },
      { quality: {}, columnar: { result: 'success' } },
      { quality: { result: 'success' }, columnar: { result: 'success' }, bypass: { result: 'success' } }]) {
      expect(() => assertRequiredCi(JSON.stringify(results))).toThrow();
    }
    expect(() => assertRequiredCi('not json')).toThrow();
  });
});
