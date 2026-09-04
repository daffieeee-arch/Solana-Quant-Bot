import { describe, expect, it } from 'vitest';
import {
  deriveMetadata,
  resolvePullRequestInheritanceRoute,
} from '../scripts/github-projects/sync.mjs';

const REPOSITORY = 'daffieeee-arch/Solana-Quant-Bot';
const REPOSITORY_ALIASES = ['daffieeee-arch/solana-paper-scanner'];

const marker = (value: unknown) => `<!-- roadmap-meta\n${JSON.stringify(value)}\n-->`;

const metadata = (overrides: Record<string, unknown> = {}) => ({
  schemaVersion: 1,
  type: 'Feature',
  area: 'Platform',
  priority: 'P1',
  phase: '0 Foundations',
  risk: 'High',
  evidence: 'Unproven',
  workflow: 'Ready',
  ...overrides,
});

const issue = (number: number, overrides: Record<string, unknown> = {}) => ({
  kind: 'Issue',
  number,
  title: `Issue ${number}`,
  body: marker(metadata()),
  state: 'OPEN',
  ...overrides,
});

const issueNumbers = [35, 56, 58, 62, 63, 70, 72, 87];
const issues = new Map(issueNumbers.map((number) => [number, issue(number)]));

const resolve = (body: string, issueMap = issues) => resolvePullRequestInheritanceRoute({
  body,
  issuesByNumber: issueMap,
  repository: REPOSITORY,
  repositoryAliases: REPOSITORY_ALIASES,
});

describe('pull-request roadmap inheritance routing', () => {
  it('reproduces PR #69 and inherits from its explicit first Roadmap issue, never incidental #56 prose', () => {
    const issueMap = new Map([
      [56, issue(56, { body: marker(metadata({ area: 'Governance', priority: 'P0', evidence: 'Operationally Verified' })) })],
      [62, issue(62, { body: marker(metadata({ area: 'Platform', priority: 'P1', evidence: 'Unproven' })) })],
      [63, issue(63)],
    ]);
    const body = `Roadmap: #62 #63

issue #56 remains the continuing control;
the roadmap migration discusses #56;
implementation evidence is recorded in #56;
this tracks historical issue #56;`;

    expect(resolve(body, issueMap)).toEqual({
      source: 'DIRECT_ROADMAP',
      primaryIssueNumber: 62,
      secondaryIssueNumbers: [63],
      orderedIssueNumbers: [62, 63],
    });
    expect(deriveMetadata({
      kind: 'PullRequest', number: 69, title: 'docs: V2 cutover', body,
      state: 'MERGED', merged: true,
    }, issueMap, REPOSITORY)).toMatchObject({
      type: 'Pull Request',
      area: 'Platform',
      priority: 'P1',
      evidence: 'Unproven',
      workflow: 'Done',
    });
  });

  it('preserves source order without numeric sorting', () => {
    expect(resolve('Roadmap: #62 #63').orderedIssueNumbers).toEqual([62, 63]);
    expect(resolve('Roadmap: #87 #72 #70')).toEqual({
      source: 'DIRECT_ROADMAP',
      primaryIssueNumber: 87,
      secondaryIssueNumbers: [72, 70],
      orderedIssueNumbers: [87, 72, 70],
    });
  });

  it('gives Roadmap precedence over closing directives regardless of line order', () => {
    expect(resolve('Closes #56\nRoadmap: #58 #63').primaryIssueNumber).toBe(58);
    expect(resolve('Roadmap: #58 #63\nCloses #56').primaryIssueNumber).toBe(58);
    expect(() => resolve('Roadmap: #999\nCloses #56')).toThrow(/#999/i);
  });

  it.each(['Close', 'Closes', 'Fix', 'Fixes', 'Resolve', 'Resolves'])(
    'uses %s only as the closing fallback when Roadmap is absent',
    (verb) => {
      expect(resolve(`${verb} #35`)).toEqual({
        source: 'CLOSING_DIRECTIVE',
        primaryIssueNumber: 35,
        secondaryIssueNumbers: [],
        orderedIssueNumbers: [35],
      });
    },
  );

  it('preserves closing-directive line and token order', () => {
    expect(resolve('Fixes #56, #35\nResolves: #62 #56')).toEqual({
      source: 'CLOSING_DIRECTIVE',
      primaryIssueNumber: 56,
      secondaryIssueNumbers: [35, 62],
      orderedIssueNumbers: [56, 35, 62],
    });
  });

  it('uses exact Implements:/Tracks: directives only as the last fallback', () => {
    expect(resolve('Implements: #35\nTracks: #56')).toEqual({
      source: 'IMPLEMENTATION_DIRECTIVE',
      primaryIssueNumber: 35,
      secondaryIssueNumbers: [56],
      orderedIssueNumbers: [35, 56],
    });
    expect(resolve('Implements #35')).toMatchObject({ source: 'NONE', primaryIssueNumber: undefined });
    expect(resolve('Tracks #56')).toMatchObject({ source: 'NONE', primaryIssueNumber: undefined });
    expect(resolve('Implements: #35\nCloses #56')).toMatchObject({
      source: 'CLOSING_DIRECTIVE', primaryIssueNumber: 56,
    });
  });

  it('does not infer a route from headings, prose, the PR title, comments, or fenced examples', () => {
    const body = `## Roadmap

issue #56 remains the continuing control;
the roadmap migration discusses #56;
implementation evidence is recorded in #56;
this tracks historical issue #56;
Fix the documentation that mentions #56.
Fix #56 is discussed as historical context.

<!-- Roadmap: #56 -->

\`\`\`
Roadmap: #56
\`\`\``;
    expect(resolve(body)).toEqual({
      source: 'NONE',
      primaryIssueNumber: undefined,
      secondaryIssueNumbers: [],
      orderedIssueNumbers: [],
    });
    expect(deriveMetadata({
      kind: 'PullRequest', number: 90, title: '[P2][Docs] Roadmap issue #56', body,
      state: 'OPEN', isDraft: false, merged: false,
    }, issues, REPOSITORY)).toMatchObject({
      type: 'Pull Request',
      area: 'Docs',
      priority: 'P2',
      workflow: 'In Review',
    });
  });

  it('keeps route-like text hidden behind exact CommonMark fence boundaries', () => {
    const bodies = [
      ['```md', 'example', '```not-a-close', 'Roadmap: #62', '```'].join('\n'),
      ['```md', '    ```', 'Roadmap: #62', '```'].join('\n'),
      ['````md', '```', 'Roadmap: #62', '````'].join('\n'),
      ['~~~md', 'Roadmap: #62', '~~~'].join('\n'),
    ];
    for (const body of bodies) expect(resolve(body)).toMatchObject({ source: 'NONE' });

    expect(resolve(['    ```md', 'Roadmap: #62'].join('\n'))).toMatchObject({
      source: 'DIRECT_ROADMAP', primaryIssueNumber: 62,
    });
  });

  it('does not let fence-like text inside an HTML comment hide a later route', () => {
    expect(resolve(['<!--', '```', '-->', 'Roadmap: #62'].join('\n'))).toMatchObject({
      source: 'DIRECT_ROADMAP', primaryIssueNumber: 62,
    });
  });

  it('applies direct PR metadata last without changing or excusing its route', () => {
    const direct = marker(metadata({ area: 'Governance', priority: 'P0', evidence: 'Fixture' }));
    const body = `Roadmap: #62 #63\n${direct}`;
    expect(resolve(body).primaryIssueNumber).toBe(62);
    expect(deriveMetadata({
      kind: 'PullRequest', number: 90, title: 'fix: routing', body,
      state: 'OPEN', isDraft: false, merged: false,
    }, issues, REPOSITORY)).toMatchObject({
      type: 'Pull Request',
      area: 'Governance',
      priority: 'P0',
      evidence: 'Fixture',
      workflow: 'In Review',
    });
    expect(() => deriveMetadata({
      kind: 'PullRequest', number: 90, title: 'fix: routing',
      body: `Roadmap: #999\n${direct}`,
      state: 'OPEN', isDraft: false, merged: false,
    }, issues, REPOSITORY)).toThrow(/unavailable same-repository issue #999/i);
  });

  it('deduplicates explicit routes without changing their first-seen order', () => {
    expect(resolve('Roadmap: #62 #63 #62')).toEqual({
      source: 'DIRECT_ROADMAP',
      primaryIssueNumber: 62,
      secondaryIssueNumbers: [63],
      orderedIssueNumbers: [62, 63],
    });
  });

  it('accepts the current repository issue URL and the former-name alias', () => {
    expect(resolve('Roadmap: https://github.com/daffieeee-arch/Solana-Quant-Bot/issues/62 #63'))
      .toMatchObject({ primaryIssueNumber: 62, secondaryIssueNumbers: [63] });
    expect(resolve('Roadmap: https://github.com/daffieeee-arch/solana-paper-scanner/issues/62 #63'))
      .toMatchObject({ primaryIssueNumber: 62, secondaryIssueNumbers: [63] });
  });

  it('rejects a former-name URL when no alias is configured', () => {
    expect(() => resolvePullRequestInheritanceRoute({
      body: 'Roadmap: https://github.com/daffieeee-arch/solana-paper-scanner/issues/62',
      issuesByNumber: issues,
      repository: REPOSITORY,
    })).toThrow(/foreign repository/i);
  });

  it.each([
    ['nonexistent issue', 'Roadmap: #999'],
    ['PR-only number', 'Roadmap: #69'],
    ['foreign repository URL', 'Roadmap: https://github.com/other/repository/issues/62'],
    ['pull-request URL', 'Roadmap: https://github.com/daffieeee-arch/Solana-Quant-Bot/pull/69'],
    ['no numeric reference', 'Roadmap: not-an-issue'],
    ['empty directive', 'Roadmap:'],
    ['mixed valid and invalid references', 'Roadmap: #62 not-an-issue'],
    ['multiple directives', 'Roadmap: #62\nRoadmap: #63'],
  ])('fails closed for an explicit unresolved %s route', (_case, body) => {
    expect(() => resolve(body)).toThrow();
  });

  it('keeps the reviewed historical compatibility routes stable and explicit', () => {
    const fixtures = [
      [64, 'Closes #56\nRoadmap: #58 #63', 58, [63]],
      [65, 'Roadmap: #56 #63', 56, [63]],
      [67, 'Roadmap: #56 #63\nFix the second live Roadmap Sync compatibility failure.', 56, [63]],
      [68, 'Roadmap: #56 #63\nFix the third live GitHub Projects compatibility mismatch.', 56, [63]],
      [69, 'Roadmap: #62 #63\nissue #56 remains the continuing control.', 62, [63]],
      [71, 'Roadmap: #70', 70, []],
      [73, 'Roadmap: #70', 70, []],
    ] as const;

    for (const [prNumber, body, primaryIssueNumber, secondaryIssueNumbers] of fixtures) {
      expect(resolve(body), `PR #${prNumber}`).toEqual({
        source: 'DIRECT_ROADMAP',
        primaryIssueNumber,
        secondaryIssueNumbers,
        orderedIssueNumbers: [primaryIssueNumber, ...secondaryIssueNumbers],
      });
    }
  });
});
