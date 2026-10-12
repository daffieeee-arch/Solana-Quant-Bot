#!/usr/bin/env node
import { pathToFileURL } from 'node:url';

/**
 * Missing/cancelled/failed jobs are failures, never successful partial evidence.
 * The scope job must succeed. The frozen V2 jobs (quality, columnar) must both succeed,
 * unless scope reported v2=false (every changed path under lab/): then both must be skipped.
 */
export function assertRequiredCi(raw) {
  const results = JSON.parse(raw);
  const required = ['columnar', 'quality', 'scope'];
  if (!results || typeof results !== 'object' || Array.isArray(results)
      || Object.keys(results).sort().join(',') !== required.join(',')
      || results.scope?.result !== 'success') {
    throw Error('Every required offline job must finish successfully');
  }
  const v2 = results.scope.outputs?.v2;
  const expected = v2 === 'true' ? 'success' : v2 === 'false' ? 'skipped' : null;
  if (!expected || ['quality', 'columnar'].some((job) => results[job]?.result !== expected)) {
    throw Error(`V2 jobs must all be ${expected ?? 'decided by the scope job'} (scope v2=${JSON.stringify(v2)})`);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  assertRequiredCi(process.env.CI_REQUIRED_RESULTS);
  console.log('All required offline jobs passed');
}
