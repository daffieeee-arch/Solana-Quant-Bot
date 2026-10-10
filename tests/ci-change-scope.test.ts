import { describe, expect, it } from 'vitest';
import { decideScope, isLabOnly } from '../scripts/ci-change-scope.mjs';

const MERGE = 'a'.repeat(40);
const BASE = 'b'.repeat(40);
const HEAD = 'c'.repeat(40);
const fakeGit = (paths: string[], parents = `${MERGE} ${BASE} ${HEAD}`) => (args: string[]) => {
  if (args[0] === 'rev-list') return `${parents}\n`;
  if (args[0] === 'cat-file') return '';
  if (args[0] === 'diff') return paths.map((path) => `${path}\0`).join('');
  throw Error(`unexpected git ${args.join(' ')}`);
};

describe('CI change scope', () => {
  it('treats only a non-empty all-lab/ change as lab-only', () => {
    expect(isLabOnly(['lab/backtest/replay.py', 'lab/ui/web/src/App.tsx'])).toBe(true);
    for (const paths of [[], ['lab/x.py', 'src/main.ts'], ['labs/x'], ['docs/lab/x.md'], ['.github/workflows/ci.yml'], ['lab'], [null]]) {
      expect(isLabOnly(paths as string[])).toBe(false);
    }
  });
  it('skips V2 jobs for a lab-only pull request and diffs the merge commit against its base parent', () => {
    const calls: string[][] = [];
    const git = (args: string[]) => { calls.push(args); return fakeGit(['lab/a.py'])(args); };
    expect(decideScope('pull_request', '', git)).toMatchObject({ v2: 'false' });
    expect(calls.at(-1)).toEqual(['diff', '--name-only', '--no-renames', '-z', BASE, MERGE]);
  });
  it('runs V2 jobs for mixed changes, empty diffs, non-merge checkouts and other events', () => {
    expect(decideScope('pull_request', '', fakeGit(['lab/a.py', 'package.json'])).v2).toBe('true');
    expect(decideScope('pull_request', '', fakeGit([])).v2).toBe('true');
    expect(decideScope('pull_request', '', fakeGit(['lab/a.py'], `${MERGE} ${BASE}`)).v2).toBe('true');
    for (const event of ['workflow_dispatch', 'workflow_call', 'schedule', undefined]) {
      expect(decideScope(event as string, BASE, fakeGit(['lab/a.py'])).v2).toBe('true');
    }
  });
  it('uses the pushed range on main and falls back to V2 jobs for a missing or zero base', () => {
    expect(decideScope('push', BASE, fakeGit(['lab/a.py'])).v2).toBe('false');
    for (const before of ['', '0'.repeat(40), 'not-a-sha', undefined]) {
      expect(decideScope('push', before as string, fakeGit(['lab/a.py'])).v2).toBe('true');
    }
  });
  it('runs V2 jobs when git fails', () => {
    const broken = () => { throw Error('fatal: bad revision'); };
    expect(decideScope('push', BASE, broken).v2).toBe('true');
    expect(decideScope('pull_request', '', broken).v2).toBe('true');
  });
});
