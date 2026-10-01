#!/usr/bin/env node
import { pathToFileURL } from 'node:url';

/** Missing/skipped/cancelled jobs are failures, never successful partial evidence. */
export function assertRequiredCi(raw) {
  const results = JSON.parse(raw);
  const required = ['quality', 'columnar'];
  if (!results || typeof results !== 'object' || Array.isArray(results)
      || Object.keys(results).sort().join(',') !== [...required].sort().join(',')
      || required.some(job => results[job]?.result !== 'success')) {
    throw Error('Every required offline job must finish successfully');
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  assertRequiredCi(process.env.CI_REQUIRED_RESULTS);
  console.log('All required offline jobs passed');
}
