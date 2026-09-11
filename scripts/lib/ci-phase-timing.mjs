import { appendFileSync } from 'node:fs';
import { performance } from 'node:perf_hooks';

/** Synchronous operational CI timings, never deterministic fixture evidence. */
export function createCiPhaseTimer({
  clock = () => performance.now(),
  log = line => process.stderr.write(line),
  appendSummary = (path, markdown) => appendFileSync(path, markdown),
} = {}) {
  const started = clock();
  const phases = [];
  const safelyLog = line => {
    try { log(line); } catch { /* Telemetry must not change a gate result. */ }
  };
  const millisecondsSince = time => Math.max(0, Math.round((clock() - time) * 1_000) / 1_000);
  return {
    measure(label, operation) {
      if (!/^[a-z0-9][a-z0-9_.-]{0,95}$/u.test(label)) throw new Error('invalid CI phase label');
      const phaseStarted = clock();
      let status = 'FAIL';
      try {
        const result = operation();
        status = 'PASS';
        return result;
      } finally {
        const phase = { label, status, duration_ms: millisecondsSince(phaseStarted) };
        phases.push(phase);
        safelyLog(`OF1_CI_PHASE ${JSON.stringify(phase)}\n`);
      }
    },
    finish({ passed, summaryPath } = {}) {
      const result = {
        status: passed === true && phases.every(phase => phase.status === 'PASS') ? 'PASS' : 'FAIL',
        duration_ms: millisecondsSince(started),
        phases: phases.map(phase => ({ ...phase })),
      };
      safelyLog(`OF1_CI_TIMING ${JSON.stringify(result)}\n`);
      if (summaryPath) {
        const rows = phases.map(phase => `| ${phase.label} | ${phase.status} | ${phase.duration_ms.toFixed(3)} |`);
        const markdown = [
          '## OF1 offline CI timings', '',
          `Gate: **${result.status}**. Total elapsed: **${result.duration_ms.toFixed(3)} ms**.`, '',
          'Operational wall-clock timings, not acquisition or research evidence. Every gate still runs on a cache hit.', '',
          '| Phase | Result | Elapsed ms |', '|---|---|---:|', ...rows, '',
          'Subprocess phases exclude surrounding JavaScript validation and scratch cleanup; total elapsed includes that work.', '',
        ].join('\n');
        try { appendSummary(summaryPath, markdown); } catch {
          // Do not reveal an arbitrary filesystem error/path or mask the real gate failure.
          safelyLog('OF1_CI_SUMMARY_UNAVAILABLE\n');
        }
      }
      return result;
    },
  };
}
