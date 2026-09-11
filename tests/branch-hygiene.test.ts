import { describe, expect, it } from 'vitest';
import { parseRemoteHeads, planBranchCleanup, REPOSITORY, runCleanup } from '../scripts/cleanup-merged-pr-branches.mjs';

const A = 'a'.repeat(40);
const B = 'b'.repeat(40);
const branch = 'v2/finished';
const merged = () => ({ number: 11, state: 'closed', merged_at: '2026-09-01T00:00:00Z',
  head: { ref: branch, sha: A, repo: { full_name: REPOSITORY } },
  base: { ref: 'main', repo: { full_name: REPOSITORY } } });
const plan = (pulls = [merged()], oid = A) => planBranchCleanup(pulls, new Map([[branch, oid]]));

function harness({ pages = [[merged()]], changedPages = pages, changedOid = A,
  origin = `https://github.com/${REPOSITORY}.git`, pushOrigin = origin, pushStatus = 0 } = {}) {
  const calls: { binary: string; args: string[] }[] = [];
  let inventories = 0;
  let pushed = false;
  const command = (binary: string, args: string[]) => {
    calls.push({ binary, args });
    let stdout = '';
    let status = 0;
    if (binary === 'gh') { inventories += 1; stdout = JSON.stringify(inventories === 1 ? pages : changedPages); }
    else if (args[0] === 'remote') stdout = args.includes('--push') ? pushOrigin : origin;
    else if (args[0] === 'ls-remote') stdout = pushed ? '' : `${inventories === 1 ? A : changedOid}\trefs/heads/${branch}\n`;
    else if (args[0] === 'push') { status = pushStatus; pushed = status === 0; }
    else throw new Error('Unexpected command');
    return { status, stdout, stderr: '' };
  };
  const run = (argv: string[] = []) => runCleanup({ argv, env: {}, command, log() {} });
  return { run, calls, command, pushes: () => calls.filter((call) => call.args[0] === 'push') };
}

describe('merged-branch inventory and exact-head deletion contract', () => {
  it('selects only exact merged same-repository heads and deduplicates names', () => {
    expect(plan([merged(), { ...merged(), number: 12 }]).candidates).toEqual([
      { number: 12, headRefName: branch, headOid: A, mergedAt: '2026-09-01T00:00:00Z' },
    ]);
    expect(plan([merged()], B).candidates).toEqual([]);
    const fork = merged(); fork.head.repo.full_name = 'other/fork';
    expect(plan([fork]).candidates).toEqual([]);
    const otherBase = merged(); otherBase.base.ref = 'feature/not-main';
    expect(plan([otherBase]).candidates).toEqual([]);
  });
  it('protects main and every open same-repository head', () => {
    const open = { ...merged(), number: 12, state: 'open', merged_at: '' };
    expect(plan([merged(), open]).candidates).toEqual([]);
    const main = merged(); main.head.ref = 'main';
    expect(planBranchCleanup([main], new Map([['main', A]])).candidates).toEqual([]);
  });
  it('defaults to read-only and requests all PR pages, including late open PRs', () => {
    const h = harness();
    expect(h.run()).toEqual({ deleted: 0, planned: 1 });
    expect(h.pushes()).toEqual([]);
    expect(h.calls.find((call) => call.binary === 'gh')?.args).toContain('--paginate');
    const lateOpen = { ...merged(), number: 201, state: 'open', merged_at: '' };
    const paginated = harness({ pages: [[merged()], [lateOpen]] });
    expect(paginated.run()).toEqual({ deleted: 0, planned: 0 });
  });
  it('refuses mismatched repository identity, origin or push destination', () => {
    expect(() => harness({ origin: 'https://github.com/other/repo.git' }).run()).toThrow(/Origin/);
    expect(() => harness({ pushOrigin: 'https://github.com/other/repo.git' }).run()).toThrow(/push URL/);
    const allowed = `https://github.com/${REPOSITORY}.git`;
    expect(() => harness({ pushOrigin: `${allowed}\nhttps://github.com/other/repo.git` }).run()).toThrow(/push URL/);
    expect(() => harness({ origin: `${allowed}\n${allowed}` }).run()).toThrow(/Origin/);
    expect(() => runCleanup({ env: { GITHUB_REPOSITORY: 'other/repo' }, log() {} })).toThrow(/identity/);
  });
  it('requires both explicit deletion flags and rejects unknown flags', () => {
    for (const args of [['--execute'], ['--yes'], ['--force'], ['--execute', '--yes', '--yes']]) {
      const h = harness(); expect(() => h.run(args)).toThrow(); expect(h.calls).toEqual([]);
    }
  });
  it('rechecks PR eligibility and current OID before deleting and never retries', () => {
    for (const options of [
      { changedOid: B },
      { changedPages: [[merged(), { ...merged(), number: 12, state: 'open', merged_at: '' }]] },
    ]) {
      const h = harness(options);
      expect(() => h.run(['--execute', '--yes'])).toThrow(/eligibility changed/);
      expect(h.pushes()).toEqual([]);
    }
  });
  it('executes one atomic exact-OID compare-and-delete and verifies absence', () => {
    const h = harness();
    expect(h.run(['--execute', '--yes'])).toEqual({ deleted: 1, planned: 1 });
    expect(h.pushes()).toEqual([{ binary: 'git', args: ['push', '--porcelain',
      `--force-with-lease=refs/heads/${branch}:${A}`, 'origin', `:refs/heads/${branch}`] }]);
  });
  it('fails closed once on an ambiguous or raced delete without mutation replay', () => {
    const h = harness({ pushStatus: 1 });
    expect(() => h.run(['--execute', '--yes'])).toThrow(/stopped without retry/);
    expect(h.pushes()).toHaveLength(1);
  });
  it('rejects duplicate or malformed remote references', () => {
    expect(() => parseRemoteHeads(`${A}\trefs/heads/${branch}\n${A}\trefs/heads/${branch}`)).toThrow(/Duplicate/);
    expect(() => parseRemoteHeads(`unknown\trefs/heads/${branch}`)).toThrow(/Malformed/);
  });
});
