#!/usr/bin/env node
/** Bounded merged-head inventory; deletion is explicit and exact-ref leased. */
import { spawnSync } from 'node:child_process';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const REPOSITORY = 'daffieeee-arch/Solana-Quant-Bot';
const OID = /^[0-9a-f]{40}$/;
const safeBranch = (name) => typeof name === 'string'
  && name !== 'main' && !/[\x00-\x20\x7f~^:?*\[\\]/.test(name)
  && !name.includes('..') && !name.includes('@{') && !name.endsWith('.')
  && name.split('/').every((part) => part && !part.startsWith('.') && !part.endsWith('.lock'));

export function parseRemoteHeads(text) {
  const heads = new Map();
  for (const line of text.trim().split('\n').filter(Boolean)) {
    const [oid, ref, extra] = line.split('\t');
    if (!OID.test(oid) || !ref?.startsWith('refs/heads/') || extra !== undefined) {
      throw new Error('Malformed remote-head inventory');
    }
    const name = ref.slice('refs/heads/'.length);
    if (heads.has(name)) throw new Error('Duplicate remote-head inventory');
    heads.set(name, oid);
  }
  return heads;
}

/** An old PR branch name alone is never proof that the current ref is merged. */
export function planBranchCleanup(pulls, heads) {
  const protectedNames = new Set(['main', ...pulls
    .filter((pr) => pr.state === 'open' && pr.head?.repo?.full_name === REPOSITORY)
    .map((pr) => pr.head.ref)]);
  const candidates = new Map();
  for (const pr of pulls) {
    const name = pr.head?.ref;
    if (pr.state !== 'closed' || !pr.merged_at || !safeBranch(name)
      || protectedNames.has(name) || pr.head?.repo?.full_name !== REPOSITORY
      || pr.base?.repo?.full_name !== REPOSITORY || pr.base?.ref !== 'main'
      || !OID.test(pr.head.sha) || heads.get(name) !== pr.head.sha) continue;
    const candidate = { number: pr.number, headRefName: name, headOid: pr.head.sha, mergedAt: pr.merged_at };
    // Reused names may have multiple historical PRs; a ref is still one operation.
    if (!candidates.has(name) || pr.number > candidates.get(name).number) candidates.set(name, candidate);
  }
  return {
    protected: [...protectedNames].sort(),
    candidates: [...candidates.values()].sort((a, b) => a.headRefName.localeCompare(b.headRefName, 'en')),
  };
}

export function runCleanup({ argv = process.argv.slice(2), env = process.env,
  command = spawnSync, log = console.log } = {}) {
  if (argv.some((arg) => !['--execute', '--yes'].includes(arg)) || new Set(argv).size !== argv.length) {
    throw new Error('Only --execute --yes are accepted');
  }
  const execute = argv.includes('--execute');
  if (execute !== argv.includes('--yes')) throw new Error('Deletion requires both --execute and --yes');
  if (env.GITHUB_REPOSITORY && env.GITHUB_REPOSITORY !== REPOSITORY) {
    throw new Error('Repository identity mismatch');
  }
  const call = (binary, args) => {
    const result = command(binary, args, { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024, timeout: 60_000 });
    if (result.status !== 0) throw new Error(`${binary} command failed; stopped without retry`);
    return result.stdout;
  };
  const verifyOrigin = () => {
    const origin = call('git', ['remote', 'get-url', '--all', 'origin']).trim();
    if (![`https://github.com/${REPOSITORY}.git`, `https://github.com/${REPOSITORY}`,
      `git@github.com:${REPOSITORY}.git`, `ssh://git@github.com/${REPOSITORY}.git`].includes(origin)) {
      throw new Error('Origin is not the approved repository');
    }
    const pushOrigin = call('git', ['remote', 'get-url', '--push', '--all', 'origin']).trim();
    if (pushOrigin !== origin) throw new Error('Origin push URL differs from verified fetch URL');
  };
  const inventory = () => {
    verifyOrigin();
    // No --limit truncation: every open PR must be considered before any deletion.
    const pages = JSON.parse(call('gh', ['api', '--hostname', 'github.com', '--paginate', '--slurp',
      `repos/${REPOSITORY}/pulls?state=all&per_page=100`]));
    if (!Array.isArray(pages) || pages.some((page) => !Array.isArray(page))) {
      throw new Error('Malformed paginated pull-request inventory');
    }
    const pulls = pages.flat();
    if (pulls.some((pr) => !Number.isSafeInteger(pr.number) || !['open', 'closed'].includes(pr.state)
      || typeof pr.head?.ref !== 'string')) throw new Error('Incomplete pull-request inventory');
    const heads = parseRemoteHeads(call('git', ['ls-remote', '--heads', 'origin']));
    return { heads, ...planBranchCleanup(pulls, heads) };
  };
  const initial = inventory();
  log(JSON.stringify({ event: 'merged_pr_branch_cleanup_plan', repository: REPOSITORY,
    mode: execute ? 'execute' : 'dry-run', remoteBranchCount: initial.heads.size,
    protected: initial.protected, candidateCount: initial.candidates.length, candidates: initial.candidates }, null, 2));
  if (!execute) return { deleted: 0, planned: initial.candidates.length };
  let deleted = 0;
  for (const candidate of initial.candidates) {
    // A newly opened PR, moved ref or changed origin invalidates the reviewed plan.
    const current = inventory();
    if (!current.candidates.some((next) => next.headRefName === candidate.headRefName
      && next.headOid === candidate.headOid && next.number === candidate.number)) {
      throw new Error(`Branch eligibility changed: ${candidate.headRefName}; stopped before deletion`);
    }
    const ref = `refs/heads/${candidate.headRefName}`;
    // Atomic compare-and-delete. A push after the read cannot lose newer work.
    call('git', ['push', '--porcelain', `--force-with-lease=${ref}:${candidate.headOid}`, 'origin', `:${ref}`]);
    if (parseRemoteHeads(call('git', ['ls-remote', '--heads', 'origin'])).has(candidate.headRefName)) {
      throw new Error(`Deleted ref still visible: ${candidate.headRefName}; stopped without retry`);
    }
    deleted += 1;
    log(JSON.stringify({ event: 'merged_pr_branch_deleted', ...candidate }));
  }
  log(JSON.stringify({ event: 'merged_pr_branch_cleanup_result', deleted, failed: 0 }));
  return { deleted, planned: initial.candidates.length };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { runCleanup(); } catch (error) { console.error(error.message); process.exitCode = 1; }
}
