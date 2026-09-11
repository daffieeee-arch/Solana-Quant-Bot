import { describe, expect, it } from 'vitest';
import { createCiPhaseTimer } from '../scripts/lib/ci-phase-timing.mjs';

describe('operational CI phase timing', () => {
  it('measures monotonic durations and preserves operation return values', () => {
    let now = 10;
    const lines: string[] = [];
    const timer = createCiPhaseTimer({ clock: () => now, log: (line: string) => lines.push(line) });
    const value = { stdout: 'canonical fixture output\n', stderr: '' };
    expect(timer.measure('clippy.default', () => { now = 12.125; return value; })).toBe(value);
    now = 14;
    expect(timer.finish({ passed: true })).toEqual({
      status: 'PASS', duration_ms: 4,
      phases: [{ label: 'clippy.default', status: 'PASS', duration_ms: 2.125 }],
    });
    expect(lines).toHaveLength(2);
    expect(lines.join('')).not.toContain('canonical fixture output');
  });

  it('records failure without replacing the original exception or invoking later work', () => {
    let now = 0;
    const failure = new Error('original gate failure');
    const timer = createCiPhaseTimer({ clock: () => now, log: () => {} });
    expect(() => timer.measure('test.default', () => { now = 3; throw failure; })).toThrow(failure);
    expect(timer.finish({ passed: false })).toEqual({
      status: 'FAIL', duration_ms: 3,
      phases: [{ label: 'test.default', status: 'FAIL', duration_ms: 3 }],
    });
  });

  it('reports a surrounding validation failure even when subprocess phases passed', () => {
    const timer = createCiPhaseTimer({ clock: () => 1, log: () => {} });
    timer.measure('fixture.json', () => 'output');
    expect(timer.finish({ passed: false })).toMatchObject({ status: 'FAIL', phases: [{ status: 'PASS' }] });
  });

  it('never reports a captured failed phase as a successful gate', () => {
    const timer = createCiPhaseTimer({ clock: () => 1, log: () => {} });
    expect(() => timer.measure('test.failed', () => { throw new Error('test failure'); })).toThrow();
    expect(timer.finish({ passed: true }).status).toBe('FAIL');
  });

  it('writes only the requested summary with fixed labels and no commands or environment', () => {
    const summaries: Array<[string, string]> = [];
    const timer = createCiPhaseTimer({
      clock: () => 1, log: () => {},
      appendSummary: (path: string, markdown: string) => summaries.push([path, markdown]),
    });
    timer.measure('compile.acquisition_https', () => undefined);
    timer.finish({ passed: true, summaryPath: '/runner/step-summary' });
    expect(summaries).toHaveLength(1);
    expect(summaries[0][0]).toBe('/runner/step-summary');
    expect(summaries[0][1]).toContain('| compile.acquisition_https | PASS | 0.000 |');
    expect(summaries[0][1]).toContain('not acquisition or research evidence');
    expect(summaries[0][1]).not.toContain('/runner/step-summary');
    for (const label of ['bad\n::error::injection', 'token=not-a-label', 'raw|table', 'A', 'a'.repeat(97)]) {
      expect(() => timer.measure(label, () => undefined)).toThrow('invalid CI phase label');
    }
  });

  it('does not write a summary when no Actions summary path is supplied', () => {
    const timer = createCiPhaseTimer({
      log: () => {}, appendSummary: () => { throw new Error('must not be called'); },
    });
    expect(timer.finish({ passed: true }).status).toBe('PASS');
  });

  it('keeps telemetry failures from masking success or the original gate failure', () => {
    const timer = createCiPhaseTimer({
      log: () => { throw new Error('broken stderr'); },
      appendSummary: () => { throw new Error('private filesystem detail'); },
    });
    expect(timer.measure('test.pass', () => 42)).toBe(42);
    const failure = new Error('actual test failure');
    expect(() => timer.measure('test.fail', () => { throw failure; })).toThrow(failure);
    expect(timer.finish({ passed: false, summaryPath: '/runner/summary' }).status).toBe('FAIL');
  });

  it('does not include summary filesystem details in logs', () => {
    const lines: string[] = [];
    const timer = createCiPhaseTimer({
      log: (line: string) => lines.push(line),
      appendSummary: () => { throw new Error('private filesystem detail'); },
    });
    timer.finish({ passed: true, summaryPath: '/runner/summary' });
    expect(lines).toContain('OF1_CI_SUMMARY_UNAVAILABLE\n');
    expect(lines.join('')).not.toContain('private filesystem detail');
  });
});
