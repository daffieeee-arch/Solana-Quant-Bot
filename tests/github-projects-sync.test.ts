import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import {
  deriveMetadata,
  deriveStatus,
  linkedIssueNumbers,
  parseRoadmapMeta,
  projectViewLayoutInput,
  validateProjectConfig,
  validateViewPreconditions,
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
      v2Phase: '0 Cutover & Cleanup',
      v2Disposition: 'ACTIVE NOW',
      effort: 8,
      startDate: '2026-09-01',
      targetDate: '2026-09-30',
    }))).toEqual({
      ...baseMeta,
      v2Phase: '0 Cutover & Cleanup',
      v2Disposition: 'ACTIVE NOW',
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
Implements https://github.com/daffieeee-arch/Solana-Quant-Bot/issues/35
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

  it('inherits roadmap dimensions from the linked issue while PR state owns Status and work-type metadata', () => {
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
    repository: 'daffieeee-arch/Solana-Quant-Bot',
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
    itemRetention: {
      closed_item_retention_days: 30,
      pre_v2_merged_pr_max_number: 68,
      pre_v2_merged_pr_numbers: [68],
      pre_v2_snapshot_sha256: 'a'.repeat(64),
      pre_v2_merged_pr_set_sha256: 'b'.repeat(64),
      pinned_items: [],
    },
    views: [{ name: 'Board', layout: 'BOARD', filter: 'is:open', visibleFields: ['Title', 'Status'] }],
  };

  it('accepts a bounded unique config', () => {
    expect(validateProjectConfig(config)).toBe(config);
  });

  it('maps readable config layouts to the current GitHub Projects GraphQL enum values', () => {
    expect(projectViewLayoutInput('TABLE')).toBe('TABLE_LAYOUT');
    expect(projectViewLayoutInput('BOARD')).toBe('BOARD_LAYOUT');
    expect(projectViewLayoutInput('ROADMAP')).toBe('ROADMAP_LAYOUT');
    expect(() => projectViewLayoutInput('GRID')).toThrow(/unsupported project view layout/i);
  });

  it('uses Work Type rather than GitHub Projects reserved Type in the production config', () => {
    const production = JSON.parse(readFileSync('roadmap/project-config.json', 'utf8')) as {
      fields: Array<{ name: string }>;
    };
    const fieldNames = production.fields.map((field) => field.name);
    expect(fieldNames).toContain('Work Type');
    expect(fieldNames).not.toContain('Type');
  });

  it('pins the current GitHub repository identity and former-name routing alias', () => {
    const production = JSON.parse(readFileSync('roadmap/project-config.json', 'utf8')) as {
      repository: string;
      repositoryAliases: string[];
    };
    expect(production.repository).toBe('daffieeee-arch/Solana-Quant-Bot');
    expect(production.repositoryAliases).toEqual(['daffieeee-arch/solana-paper-scanner']);
    expect(validateProjectConfig(production).repository).toBe(production.repository);
  });

  it('pins the reviewed V2 fields, Project copy, and eight migration-safe views', () => {
    const production = JSON.parse(readFileSync('roadmap/project-config.json', 'utf8')) as {
      project: { shortDescription: string; readme: string };
      fields: Array<{ name: string; options?: Array<{ name: string }> }>;
      views: Array<{
        name: string;
        layout: string;
        filter: string;
        visibleFields: string[];
        aliases?: string[];
      }>;
    };
    const optionNames = (fieldName: string) => production.fields
      .find((field) => field.name === fieldName)?.options?.map(({ name }) => name);

    expect(optionNames('V2 Phase')).toEqual([
      '0 Cutover & Cleanup',
      '1 Pump Protocol Truth',
      '2 Authentic Acquisition',
      '3 Bronze & Silver',
      '4 Research Observatory',
      '5 Scale & Data Sufficiency',
      '6 Gold & Edge Validation',
      '7 Prospective Shadow',
      '8 New Rust Paper Engine',
      '9 Professional Workstation',
      '10 VPS & Gated Live',
    ]);
    expect(optionNames('V2 Disposition')).toEqual([
      'ACTIVE NOW', 'NEXT', 'LATER', 'SPLIT', 'SUPERSEDED', 'RETIRED',
    ]);
    expect(production.project.shortDescription).toBe('Data-first, Triton-only Solana/Pump quant program (E0): authentic evidence, Research Observatory before Professional Workstation, and edge discovery or falsification. Profitability is not assumed.');
    expect(production.project.readme).toBe('# Solana Quant Platform V2\n\nProject #4 is the active delivery cockpit for program E0: build authentic, point-in-time Solana/Pump evidence and discover a defensible edge or falsify the hypothesis. Profitability is not assumed.\n\n- Authoritative handoff: [docs/HANDOFF_V2.md](https://github.com/daffieeee-arch/Solana-Quant-Bot/blob/main/docs/HANDOFF_V2.md)\n- Network boundary: Triton One only.\n- Product order: authentic data and Research Observatory before the Professional Trading Workstation, prospective shadow and new paper engine.\n- Project status is delivery metadata, never research evidence by itself.\n\nManual edits to synchronized fields can be overwritten by the next trusted-default-branch reconciliation.');

    const visibleFields = [
      'Title', 'Status', 'V2 Disposition', 'V2 Phase', 'Priority', 'Area',
      'Work Type', 'Evidence', 'Risk', 'Assignees',
    ];
    expect(production.views).toEqual([
      {
        name: 'Now', layout: 'BOARD',
        filter: 'v2-disposition:"ACTIVE NOW" -status:Done,Cancelled -work-type:Program,Epic',
        visibleFields, aliases: ['Delivery Board'],
      },
      {
        name: 'Next', layout: 'TABLE',
        filter: 'v2-disposition:NEXT -status:Done,Cancelled -work-type:Program,Epic',
        visibleFields, aliases: ['P0 Blockers'],
      },
      {
        name: 'Data', layout: 'TABLE',
        filter: 'v2-phase:"1 Pump Protocol Truth","2 Authentic Acquisition","3 Bronze & Silver","5 Scale & Data Sufficiency" v2-disposition:"ACTIVE NOW",NEXT,LATER',
        visibleFields, aliases: ['Data & Research'],
      },
      {
        name: 'Observatory', layout: 'TABLE',
        filter: 'v2-phase:"4 Research Observatory" v2-disposition:"ACTIVE NOW",NEXT,LATER',
        visibleFields, aliases: ['Frontend Cockpit'],
      },
      {
        name: 'Research', layout: 'TABLE',
        filter: 'v2-phase:"5 Scale & Data Sufficiency","6 Gold & Edge Validation" v2-disposition:"ACTIVE NOW",NEXT,LATER',
        visibleFields, aliases: ['Recently Updated'],
      },
      {
        name: 'Later', layout: 'TABLE', filter: 'v2-disposition:LATER',
        visibleFields, aliases: ['Executive Roadmap'],
      },
      {
        name: 'Retired', layout: 'TABLE', filter: 'v2-disposition:SUPERSEDED,RETIRED',
        visibleFields, aliases: ['Done & Cancelled'],
      },
      {
        name: 'Migration Ledger', layout: 'TABLE', filter: 'v2-disposition:SPLIT,SUPERSEDED,RETIRED',
        visibleFields, aliases: ['Live Shadow & Execution'],
      },
    ]);
  });

  it('rejects owner drift, duplicate fields and unsupported layouts', () => {
    expect(() => validateProjectConfig({ ...config, repository: 'someone/else' })).toThrow(/owner/i);
    expect(() => validateProjectConfig({ ...config, fields: [...config.fields, config.fields[0]] })).toThrow(/duplicate/i);
    expect(() => validateProjectConfig({ ...config, views: [{ name: 'Bad', layout: 'GRID' }] })).toThrow(/layout/i);
  });

  it('rejects alias owner drift, canonical duplicates and duplicate aliases', () => {
    expect(() => validateProjectConfig({
      ...config,
      repositoryAliases: ['other-owner/former-name'],
    })).toThrow(/owner/i);
    expect(() => validateProjectConfig({
      ...config,
      repositoryAliases: ['daffieeee-arch/Solana-Quant-Bot'],
    })).toThrow(/duplicate repository identity/i);
    expect(() => validateProjectConfig({
      ...config,
      repositoryAliases: ['daffieeee-arch/former-name', 'daffieeee-arch/former-name'],
    })).toThrow(/duplicate repository identity/i);
  });

  it('requires an explicit bounded retention policy and snapshot binding', () => {
    const { itemRetention: _removed, ...withoutRetention } = config;
    expect(() => validateProjectConfig(withoutRetention)).toThrow(/itemRetention must be an object/i);
    expect(() => validateProjectConfig({
      ...config,
      itemRetention: { ...config.itemRetention, closed_item_retention_days: undefined },
    })).toThrow(/explicit integer/i);
    expect(() => validateProjectConfig({
      ...config,
      itemRetention: { ...config.itemRetention, pre_v2_snapshot_sha256: 'unbound' },
    })).toThrow(/reviewed pre-migration export/i);
  });

  it('fails closed when the audited legacy/current view set drifts', () => {
    const migrationConfig = {
      views: [{ name: 'Now', aliases: ['Delivery Board'] }],
    };
    expect(validateViewPreconditions(migrationConfig, [
      { id: 'VIEW_1', name: 'Delivery Board' },
    ])).toEqual({ matched: 1, creates: 0 });
    expect(validateViewPreconditions(migrationConfig, [
      { id: 'VIEW_1', name: 'Now' },
    ])).toEqual({ matched: 1, creates: 0 });
    expect(() => validateViewPreconditions(migrationConfig, [])).toThrow(/view for Now is missing/i);
    expect(() => validateViewPreconditions(migrationConfig, [
      { id: 'VIEW_1', name: 'Delivery Board' },
      { id: 'VIEW_2', name: 'Unreviewed' },
    ])).toThrow(/unexpected Project views.*Unreviewed/i);
  });
});
