import { describe, expect, it } from 'vitest';
import { assertRequiredCi } from '../scripts/assert-required-ci.mjs';

const run = (v2: unknown, quality: unknown, columnar: unknown, scope: unknown = 'success') => () => assertRequiredCi(JSON.stringify({
  scope: { result: scope, outputs: v2 === undefined ? {} : { v2 } },
  quality: { result: quality },
  columnar: { result: columnar },
}));

describe('required CI aggregate', () => {
  it('accepts complete successful V2 work, or both V2 jobs skipped for a lab-only change', () => {
    expect(run('true', 'success', 'success')).not.toThrow();
    expect(run('false', 'skipped', 'skipped')).not.toThrow();
  });
  it('refuses every unsuccessful V2 result when the change is not lab-only', () => {
    for (const job of ['quality', 'columnar']) {
      for (const result of ['failure', 'cancelled', 'skipped', 'pending', '', null]) {
        expect(run('true', job === 'quality' ? result : 'success', job === 'columnar' ? result : 'success')).toThrow();
      }
    }
  });
  it('refuses a lab-only change whose V2 jobs did not both skip', () => {
    for (const [quality, columnar] of [['success', 'skipped'], ['skipped', 'success'], ['failure', 'skipped'], ['skipped', 'cancelled']]) {
      expect(run('false', quality, columnar)).toThrow();
    }
  });
  it('refuses an unsuccessful or undecided scope job', () => {
    for (const scope of ['failure', 'cancelled', 'skipped', '', null]) {
      expect(run('false', 'skipped', 'skipped', scope)).toThrow();
      expect(run('true', 'success', 'success', scope)).toThrow();
    }
    for (const v2 of [undefined, '', 'yes', true, false, null]) expect(run(v2, 'skipped', 'skipped')).toThrow();
  });
  it('refuses absent, malformed or unexpected dependency sets', () => {
    const scope = { result: 'success', outputs: { v2: 'true' } };
    for (const results of [null, [], {}, { quality: { result: 'success' }, columnar: { result: 'success' } },
      { scope, quality: { result: 'success' } },
      { scope, quality: {}, columnar: { result: 'success' } },
      { scope, quality: { result: 'success' }, columnar: { result: 'success' }, bypass: { result: 'success' } }]) {
      expect(() => assertRequiredCi(JSON.stringify(results))).toThrow();
    }
    expect(() => assertRequiredCi('not json')).toThrow();
  });
});
