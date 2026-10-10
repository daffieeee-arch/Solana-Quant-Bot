#!/usr/bin/env node
import { execFileSync } from 'node:child_process';
import { appendFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

// Owner decision 2026-10-10: a change that only touches lab/ skips the frozen V2 jobs
// (core-offline, columnar-offline). Any doubt runs them: no diff base, an empty diff, a
// git error, or any path outside lab/.

/** True only for a non-empty list in which every path lies under lab/. */
export function isLabOnly(paths) {
  return Array.isArray(paths) && paths.length > 0 && paths.every((path) => typeof path === 'string' && path.startsWith('lab/'));
}

const SHA = /^[0-9a-f]{40}$/;

/** The commit range of this run, or null when the full V2 jobs must run. */
export function diffRange(event, before, git) {
  if (event === 'pull_request') {
    // actions/checkout checks out the merge commit: first parent = base branch tip.
    const parents = git(['rev-list', '--parents', '-n', '1', 'HEAD']).trim().split(/\s+/);
    return parents.length === 3 ? [parents[1], parents[0]] : null;
  }
  if (event === 'push' && SHA.test(before ?? '') && !/^0+$/.test(before)) {
    git(['cat-file', '-e', `${before}^{commit}`]);
    return [before, 'HEAD'];
  }
  return null;
}

/** Decide whether the V2 jobs must run ("true") or may be skipped ("false"). */
export function decideScope(event, before, git) {
  try {
    const range = diffRange(event, before, git);
    if (!range) return { v2: 'true', paths: [], reason: `no lab-only diff range for event ${event}` };
    const paths = git(['diff', '--name-only', '--no-renames', '-z', range[0], range[1]])
      .split('\0').filter(Boolean);
    return isLabOnly(paths)
      ? { v2: 'false', paths, reason: 'every changed path is under lab/' }
      : { v2: 'true', paths, reason: paths.length ? 'a changed path lies outside lab/' : 'empty diff' };
  } catch (error) {
    return { v2: 'true', paths: [], reason: `git failed: ${error instanceof Error ? error.message : String(error)}` };
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const git = (args) => execFileSync('git', args, { encoding: 'utf8' });
  const scope = decideScope(process.env.CI_EVENT, process.env.CI_BEFORE, git);
  console.log(`v2=${scope.v2} (${scope.reason}; ${scope.paths.length} changed paths)`);
  for (const path of scope.paths.slice(0, 50)) console.log(`  ${path}`);
  if (!process.env.GITHUB_OUTPUT) throw Error('GITHUB_OUTPUT is not set');
  appendFileSync(process.env.GITHUB_OUTPUT, `v2=${scope.v2}\n`);
}
