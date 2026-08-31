import { describe, expect, it } from 'vitest';
import {
  deriveMetadata,
  deriveStatus,
  linkedIssueNumbers,
  parseRoadmapMeta,
  validateProjectConfig,
} from '../scripts/github-projects/sync.mjs';

const marker = (value: unknown) => `<!-- roadmap-meta\n${JSON.stringify(value)}\n-->`;

const baseMeta = {
  schemaVersion: 1,
  type: 'Feature',
  area: 'Frontend',
  priority: 'P1',
  phase: '3 Core Workspaces',
  risk: 'Medium',
  evidence: 'Unproven',
  workflow: 'Ready',
};

describe('GitHub Projects roadmap metadata', () => {
  it('parses one bounded typed metadata block and preserves optional planning values', () => {
    expect(parseRoadmapMeta(marker({
      ...baseMeta,
      effort: 8,
      startDate: '2026-09-01',
      targetDate: '2026-09-30',
    }))).toEqual({
      ...baseMeta,
      effort: 8,
      startDate: '2026-09-01',
      targetDate: '2026-09-30',
    });
    expect(parseRoadmapMeta('ordinary issue body')).toBeUndefined();
  });

  it('fails closed on duplicate, malformed, unknown or invalid metadata', () => {
    expect(() => parseRoadmapMeta(`${marker(baseMeta)}\n${marker(baseMeta)}`)).toThrow(/at most once/i);
    expect(() => parseRoadmapMeta('<!-- roadmap-meta {broken} -->')).toThrow(/invalid JSON/i);
    expect(() => parseRoadmapMeta(marker({ ...baseMeta, surprise: true }))).toThrow(/unknown key/i);
    expect(() => parseRoadmapMeta(marker({ ...baseMeta, schemaVersion: 2 }))).toThrow(/schemaVersion/i);
    expect(() => parseRoadmapMeta(marker({ ...baseMeta, effort: 1.5 }))).toThrow(/effort/i);
    expect(() => parseRoadmapMeta(marker({ ...baseMeta, targetDate: '2026-02-31' }))).toThrow(/valid date/i);
  });

  it('extracts only issue references from delivery/linking lines', () => {
    expect(linkedIssueNumbers(`
Random mention #999
Closes #27, #31
Roadmap: #58
Implements https://github.com/daffieeee-arch/solana-paper-scanner/issues/35
`)).toEqual([27, 31, 35, 58]);
  });

  it('derives workflow status deterministically from issue and PR state', () => {
    expect(deriveStatus({ kind: 'Issue', state: 'OPEN' }, { workflow: 'Blocked' })).toBe('Blocked');
    expect(deriveStatus({ kind: 'Issue', state: 'CLOSED', stateReason: 'COMPLETED' })).toBe('Done');
    expect(deriveStatus({ kind: 'Issue', state: 'CLOSED', stateReason: 'NOT_PLANNED' })).toBe('Cancelled');
    expect(deriveStatus({ kind: 'PullRequest', state: 'OPEN', isDraft: true })).toBe('In Progress');
    expect(deriveStatus({ kind: 'PullRequest', state: 'OPEN', isDraft: false })).toBe('In Review');
    expect(deriveStatus({ kind: 'PullRequest', state: 'MERGED', merged: true })).toBe('Done');
    expect(deriveStatus({ kind: 'PullRequest', state: 'CLOSED', merged: false })).toBe('Cancelled');
  });

  it('inherits roadmap dimensions from the linked issue while PR state owns Status and Type', () => {
    const issue = {
      kind: 'Issue',
      number: 35,
      title: '[P0][Frontend] Define cockpit architecture',
      body: marker({ ...baseMeta, priority: 'P0', workflow: 'Ready' }),
      state: 'OPEN',
    };
    const issues = new Map([[35, issue]]);
    const pr = {
      kind: 'PullRequest',
      number: 64,
      title: 'feat(frontend): professional cockpit architecture',
      body: 'Closes #35',
      state: 'OPEN',
      isDraft: false,
      merged: false,
    };
    expect(deriveMetadata(pr, issues)).toMatchObject({
      type: 'Pull Request',
      area: 'Frontend',
      priority: 'P0',
      phase: '3 Core Workspaces',
      risk: 'Medium',
      evidence: 'Unproven',
      workflow: 'In Review',
    });
  });
});

describe('GitHub Projects roadmap config', () => {
  const config = {
    schemaVersion: 1,
    owner: 'daffieeee-arch',
    repository: 'daffieeee-arch/solana-paper-scanner',
    project: {
      title: 'Project',
      shortDescription: 'Description',
      readme: 'Readme',
    },
    fields: [
      {
        name: 'Status',
        dataType: 'SINGLE_SELECT',
        builtin: true,
        options: [{ name: 'Backlog', color: 'GRAY', description: 'Backlog' }],
      },
      { name: 'Effort', dataType: 'NUMBER' },
      { name: 'Target date', dataType: 'DATE' },
    ],
    views: [{ name: 'Board', layout: 'BOARD', filter: 'is:open', visibleFields: ['Title', 'Status'] }],
  };

  it('accepts a bounded unique config', () => {
    expect(validateProjectConfig(config)).toBe(config);
  });

  it('rejects owner drift, duplicate fields and unsupported layouts', () => {
    expect(() => validateProjectConfig({ ...config, repository: 'someone/else' })).toThrow(/owner/i);
    expect(() => validateProjectConfig({ ...config, fields: [...config.fields, config.fields[0]] })).toThrow(/duplicate/i);
    expect(() => validateProjectConfig({ ...config, views: [{ name: 'Bad', layout: 'GRID' }] })).toThrow(/layout/i);
  });
});
