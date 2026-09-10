#!/usr/bin/env node
/**
 * List or delete remote branches whose exact name matches a merged PR head.
 *
 * Default is dry-run. Never deletes `main` or any open PR head.
 *
 * Usage:
 *   node scripts/cleanup-merged-pr-branches.mjs
 *   node scripts/cleanup-merged-pr-branches.mjs --execute
 *   node scripts/cleanup-merged-pr-branches.mjs --execute --yes
 */
import { spawnSync } from 'node:child_process';

function ghJson(args) {
  const result = spawnSync('gh', args, { encoding: 'utf8' });
  if (result.status !== 0) {
    throw new Error(`gh ${args.join(' ')} failed: ${result.stderr || result.stdout}`);
  }
  return JSON.parse(result.stdout);
}

function ghText(args) {
  const result = spawnSync('gh', args, { encoding: 'utf8' });
  if (result.status !== 0) {
    throw new Error(`gh ${args.join(' ')} failed: ${result.stderr || result.stdout}`);
  }
  return result.stdout;
}

function remoteHeads() {
  const result = spawnSync('git', ['ls-remote', '--heads', 'origin'], { encoding: 'utf8' });
  if (result.status !== 0) {
    throw new Error(`git ls-remote failed: ${result.stderr || result.stdout}`);
  }
  const names = new Set();
  for (const line of result.stdout.split('\n')) {
    if (!line.trim()) continue;
    const ref = line.split('\t')[1];
    if (!ref?.startsWith('refs/heads/')) continue;
    names.add(ref.slice('refs/heads/'.length));
  }
  return names;
}

const execute = process.argv.includes('--execute');
const yes = process.argv.includes('--yes');
const repo = process.env.GITHUB_REPOSITORY || 'daffieeee-arch/Solana-Quant-Bot';

const merged = ghJson([
  'pr',
  'list',
  '--repo',
  repo,
  '--state',
  'merged',
  '--limit',
  '200',
  '--json',
  'number,headRefName,mergedAt,title',
]);
const openPrs = ghJson([
  'pr',
  'list',
  '--repo',
  repo,
  '--state',
  'open',
  '--limit',
  '100',
  '--json',
  'number,headRefName,title',
]);

const protectedNames = new Set(['main', ...openPrs.map((pr) => pr.headRefName)]);
const remote = remoteHeads();
const candidates = [];
for (const pr of merged) {
  const name = pr.headRefName;
  if (!name || protectedNames.has(name)) continue;
  if (!remote.has(name)) continue;
  candidates.push(pr);
}

console.log(
  JSON.stringify(
    {
      event: 'merged_pr_branch_cleanup_plan',
      repository: repo,
      mode: execute ? 'execute' : 'dry-run',
      remoteBranchCount: remote.size,
      protected: [...protectedNames].sort(),
      candidateCount: candidates.length,
      candidates: candidates.map((pr) => ({
        number: pr.number,
        headRefName: pr.headRefName,
        mergedAt: pr.mergedAt,
        title: pr.title,
      })),
    },
    null,
    2,
  ),
);

if (!execute) {
  console.error('Dry-run only. Re-run with --execute --yes to delete remote branches.');
  process.exit(0);
}

if (!yes) {
  console.error('Refusing to delete without --yes.');
  process.exit(2);
}

let deleted = 0;
let failed = 0;
for (const pr of candidates) {
  const name = pr.headRefName;
  const result = spawnSync(
    'gh',
    ['api', '-X', 'DELETE', `repos/${repo}/git/refs/heads/${encodeURIComponent(name)}`],
    { encoding: 'utf8' },
  );
  if (result.status === 0) {
    deleted += 1;
    console.log(`deleted ${name} (PR #${pr.number})`);
  } else {
    failed += 1;
    console.error(`FAILED ${name}: ${result.stderr || result.stdout}`);
  }
}

console.log(JSON.stringify({ event: 'merged_pr_branch_cleanup_result', deleted, failed }, null, 2));
process.exit(failed === 0 ? 0 : 1);
