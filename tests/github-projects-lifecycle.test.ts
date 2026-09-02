import { readFileSync } from 'node:fs';
import YAML from 'yaml';
import { describe, expect, it } from 'vitest';
import {
  buildItemReconciliationPlan,
  planItemLifecycle,
  selectInputOptions,
  validateMigrationBaseline,
} from '../scripts/github-projects/sync.mjs';

const RECONCILED_AT = '2026-09-01T12:00:00.000Z';
const SNAPSHOT_SHA256 = 'a'.repeat(64);
const retention = {
  closed_item_retention_days: 30,
  pre_v2_merged_pr_max_number: 68,
  pre_v2_merged_pr_numbers: [68],
  pre_v2_snapshot_sha256: SNAPSHOT_SHA256,
  pre_v2_merged_pr_set_sha256: 'b'.repeat(64),
  pinned_items: [{ kind: 'Issue', number: 56 }],
};

const issue = (number: number, overrides: Record<string, unknown> = {}) => ({
  id: `ISSUE_${number}`,
  kind: 'Issue',
  number,
  state: 'OPEN',
  closedAt: null,
  latestReopenedAt: null,
  ...overrides,
});

const pullRequest = (number: number, overrides: Record<string, unknown> = {}) => ({
  id: `PR_${number}`,
  kind: 'PullRequest',
  number,
  state: 'OPEN',
  merged: false,
  closedAt: null,
  mergedAt: null,
  latestReopenedAt: null,
  ...overrides,
});

const item = (
  content: { id: string },
  overrides: Record<string, unknown> = {},
) => ({
  id: `ITEM_${content.id}`,
  isArchived: false,
  updatedAt: '2026-08-01T00:00:00.000Z',
  content: { id: content.id },
  ...overrides,
});

const actionFor = (
  content: ReturnType<typeof issue> | ReturnType<typeof pullRequest>,
  existingItem?: ReturnType<typeof item>,
) => planItemLifecycle({
  content,
  item: existingItem,
  retention,
  reconciledAt: RECONCILED_AT,
});

describe('GitHub Project item lifecycle', () => {
  it('requires the production policy to retain pinned #56 after the reviewed #63 pin removal', () => {
    const production = JSON.parse(readFileSync('roadmap/project-config.json', 'utf8')) as {
      itemRetention?: {
        closed_item_retention_days?: number;
        pre_v2_merged_pr_max_number?: number;
        pre_v2_merged_pr_numbers?: number[];
        pre_v2_snapshot_sha256?: string;
        pre_v2_merged_pr_set_sha256?: string;
        pinned_items?: Array<{ kind: string; number: number }>;
      };
    };

    expect(production.itemRetention).toMatchObject({
      closed_item_retention_days: 30,
      pre_v2_merged_pr_max_number: 68,
    });
    expect(production.itemRetention?.pinned_items).toContainEqual({ kind: 'Issue', number: 56 });
    expect(production.itemRetention?.pinned_items).not.toContainEqual({ kind: 'Issue', number: 63 });
    expect(production.itemRetention?.pinned_items).toHaveLength(1);
    expect(production.itemRetention?.pre_v2_merged_pr_numbers).toEqual([
      1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13,
      14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26,
      64, 65, 67, 68,
    ]);
    expect(production.itemRetention?.pre_v2_snapshot_sha256)
      .toBe('98a97b7bfb74d194898d0aa1d65c0a9caf8c4015f09ae6a93c76fd8289ee3585');
    expect(production.itemRetention?.pre_v2_merged_pr_set_sha256)
      .toBe('0211a16af1a18006bfa1005f12e4b57c9373508d655884f59a2a3f18656d9e67');
  });

  it('uses the explicit retention interval with an inclusive 30-day archive boundary', () => {
    const exactBoundary = issue(80, {
      state: 'CLOSED',
      closedAt: '2026-08-02T12:00:00.000Z',
    });
    const stillRetained = issue(81, {
      state: 'CLOSED',
      closedAt: '2026-08-02T12:00:00.001Z',
    });

    expect(actionFor(exactBoundary, item(exactBoundary))).toMatchObject({
      action: 'ARCHIVE',
      eligible: false,
    });
    expect(actionFor(stillRetained, item(stillRetained))).toMatchObject({
      action: 'KEEP_ACTIVE',
      eligible: true,
    });
  });

  it('archives exactly merged pre-V2 PRs through #68, not a closed-unmerged #66 or PR #69', () => {
    const merged68 = pullRequest(68, {
      state: 'MERGED',
      merged: true,
      mergedAt: '2026-09-01T10:00:00.000Z',
    });
    const closedUnmerged66 = pullRequest(66, {
      state: 'CLOSED',
      merged: false,
      closedAt: '2026-09-01T10:00:00.000Z',
    });
    const merged69 = pullRequest(69, {
      state: 'MERGED',
      merged: true,
      mergedAt: '2026-09-01T10:00:00.000Z',
    });

    expect(actionFor(merged68, item(merged68))).toMatchObject({ action: 'ARCHIVE', eligible: false });
    expect(actionFor(closedUnmerged66, item(closedUnmerged66))).toMatchObject({
      action: 'KEEP_ACTIVE',
      eligible: true,
    });
    expect(actionFor(merged69, item(merged69))).toMatchObject({ action: 'KEEP_ACTIVE', eligible: true });
  });

  it('keeps pinned continuing control #56 active despite its age', () => {
    const control = issue(56, {
      state: 'CLOSED',
      closedAt: '2025-01-01T00:00:00.000Z',
    });

    expect(actionFor(control, item(control))).toMatchObject({
      action: 'KEEP_ACTIVE',
      eligible: true,
    });
  });

  it('never reactivates archived closed content', () => {
    const closed = issue(82, {
      state: 'CLOSED',
      closedAt: '2026-08-31T00:00:00.000Z',
      latestReopenedAt: '2026-08-30T00:00:00.000Z',
    });

    expect(actionFor(closed, item(closed, {
      isArchived: true,
      updatedAt: '2026-08-01T00:00:00.000Z',
    }))).toMatchObject({ action: 'KEEP_ARCHIVED', eligible: false });
  });

  it('unarchives open content only after a strictly newer genuine reopen', () => {
    const afterArchive = issue(83, {
      latestReopenedAt: '2026-08-01T00:00:00.001Z',
    });
    const equalToArchive = issue(84, {
      latestReopenedAt: '2026-08-01T00:00:00.000Z',
    });
    const beforeArchive = issue(85, {
      latestReopenedAt: '2026-07-31T23:59:59.999Z',
    });

    expect(actionFor(afterArchive, item(afterArchive, {
      isArchived: true,
      updatedAt: '2026-08-01T00:00:00.000Z',
    }))).toMatchObject({ action: 'UNARCHIVE', eligible: true });
    expect(actionFor(equalToArchive, item(equalToArchive, {
      isArchived: true,
      updatedAt: '2026-08-01T00:00:00.000Z',
    }))).toMatchObject({ action: 'KEEP_ARCHIVED', eligible: false });
    expect(actionFor(beforeArchive, item(beforeArchive, {
      isArchived: true,
      updatedAt: '2026-08-01T00:00:00.000Z',
    }))).toMatchObject({ action: 'KEEP_ARCHIVED', eligible: false });
  });

  it('fails closed when lifecycle timestamps needed for a mutation are absent', () => {
    const archivedOpen = issue(86);
    const closedWithoutTimestamp = issue(87, { state: 'CLOSED' });

    expect(actionFor(archivedOpen, item(archivedOpen, {
      isArchived: true,
      updatedAt: null,
    }))).toMatchObject({ action: 'KEEP_ARCHIVED', eligible: false });
    expect(() => actionFor(closedWithoutTimestamp)).toThrow(/closedAt is required/i);
    expect(() => actionFor(closedWithoutTimestamp, item(closedWithoutTimestamp)))
      .toThrow(/closedAt is required/i);
  });

  it('produces deterministic sorted operations and counts for permuted inputs', () => {
    const add = issue(10);
    const reopen = issue(12, { latestReopenedAt: '2026-08-02T00:00:00.000Z' });
    const archivedClosed = issue(13, {
      state: 'CLOSED',
      closedAt: '2026-08-31T00:00:00.000Z',
    });
    const pinned = issue(56, {
      state: 'CLOSED',
      closedAt: '2025-01-01T00:00:00.000Z',
    });
    const closedUnmerged = pullRequest(66, {
      state: 'CLOSED',
      closedAt: '2026-08-31T00:00:00.000Z',
    });
    const preV2 = pullRequest(68, {
      state: 'MERGED',
      merged: true,
      mergedAt: '2026-08-31T00:00:00.000Z',
    });
    const current = pullRequest(69, {
      state: 'MERGED',
      merged: true,
      mergedAt: '2026-08-31T00:00:00.000Z',
    });
    const expiredAbsent = pullRequest(70, {
      state: 'CLOSED',
      closedAt: '2026-01-01T00:00:00.000Z',
    });
    const contents = [current, archivedClosed, preV2, add, expiredAbsent, pinned, reopen, closedUnmerged];
    const existingItems = [
      item(current),
      item(archivedClosed, { isArchived: true }),
      item(preV2),
      item(pinned),
      item(reopen, { isArchived: true }),
      item(closedUnmerged),
    ];

    const planned = buildItemReconciliationPlan({
      contents,
      existingItems,
      retention,
      reconciledAt: RECONCILED_AT,
    });
    const permuted = buildItemReconciliationPlan({
      contents: [...contents].reverse(),
      existingItems: [...existingItems].reverse(),
      retention,
      reconciledAt: RECONCILED_AT,
    });

    expect(permuted).toEqual(planned);
    expect(planned.operations.map(({ content, action }) => `${content.kind}:${content.number}:${action}`))
      .toEqual([
        'Issue:10:ADD',
        'Issue:12:UNARCHIVE',
        'Issue:13:KEEP_ARCHIVED',
        'Issue:56:KEEP_ACTIVE',
        'PullRequest:66:KEEP_ACTIVE',
        'PullRequest:68:ARCHIVE',
        'PullRequest:69:KEEP_ACTIVE',
        'PullRequest:70:SKIP',
      ]);
    expect(planned.counts).toEqual({
      ADD: 1,
      KEEP_ACTIVE: 3,
      ARCHIVE: 1,
      UNARCHIVE: 1,
      KEEP_ARCHIVED: 1,
      SKIP: 1,
    });
    expect(Object.values(planned.reasonCounts).reduce((total, count) => total + count, 0)).toBe(8);
    expect(planned.baselines).toEqual({
      expectedPreV2MergedPullRequests: 1,
      observedPreV2MergedPullRequests: 1,
    });
  });

  it('fails before mutations when the live pre-V2 merged-PR set drifts from the audited set', () => {
    const unexpectedMerged = pullRequest(66, {
      state: 'MERGED',
      merged: true,
      mergedAt: '2026-08-31T00:00:00.000Z',
    });

    expect(() => buildItemReconciliationPlan({
      contents: [pullRequest(68, {
        state: 'MERGED',
        merged: true,
        mergedAt: '2026-08-31T00:00:00.000Z',
      }), unexpectedMerged],
      existingItems: [],
      retention,
      reconciledAt: RECONCILED_AT,
    })).toThrow(/candidate drift.*expected 68.*observed 66,68/i);
  });

  it('requires every audited archive candidate to be active on the initial V2 migration', () => {
    const candidate = pullRequest(68, {
      state: 'MERGED',
      merged: true,
      mergedAt: '2026-08-31T00:00:00.000Z',
    });
    const activePlan = buildItemReconciliationPlan({
      contents: [candidate],
      existingItems: [item(candidate)],
      retention,
      reconciledAt: RECONCILED_AT,
    });
    const archivedPlan = buildItemReconciliationPlan({
      contents: [candidate],
      existingItems: [item(candidate, { isArchived: true })],
      retention,
      reconciledAt: RECONCILED_AT,
    });
    const missingPlan = buildItemReconciliationPlan({
      contents: [candidate],
      existingItems: [],
      retention,
      reconciledAt: RECONCILED_AT,
    });

    expect(validateMigrationBaseline({ existingFields: [], plan: activePlan, retention })).toEqual({
      mode: 'INITIAL',
      expectedInitialArchives: 1,
    });
    expect(() => validateMigrationBaseline({ existingFields: [], plan: archivedPlan, retention }))
      .toThrow(/initial Project archive baseline drift.*#68:KEEP_ARCHIVED/i);
    expect(validateMigrationBaseline({
      existingFields: [{ name: 'V2 Phase' }, { name: 'V2 Disposition' }],
      plan: archivedPlan,
      retention,
    })).toEqual({ mode: 'CONTINUING', expectedInitialArchives: 1 });
    expect(() => validateMigrationBaseline({
      existingFields: [{ name: 'V2 Phase' }, { name: 'V2 Disposition' }],
      plan: missingPlan,
      retention,
    })).toThrow(/continuing Project archive baseline drift.*#68:SKIP/i);
    expect(() => validateMigrationBaseline({
      existingFields: [{ name: 'V2 Phase' }],
      plan: activePlan,
      retention,
    })).toThrow(/partial V2 field migration/i);
  });
});

describe('GitHub Project field identity and mutation safety', () => {
  it('preserves the six semantically unchanged Evidence option IDs', () => {
    const production = JSON.parse(readFileSync('roadmap/project-config.json', 'utf8')) as {
      fields: Array<{
        name: string;
        options?: Array<{
          name: string;
          color: string;
          description: string;
          aliases?: string[];
          preserveId?: string;
        }>;
      }>;
    };
    const evidence = production.fields.find((field) => field.name === 'Evidence');
    expect(evidence?.options?.map(({ name }) => name)).toEqual([
      'Not Applicable',
      'Unproven',
      'Fixture',
      'Operationally Verified',
      'Engineering Validation',
      'Research Candidate',
      'Research Ready',
      'Shadow',
      'Paper Proven',
      'Live Proven',
    ]);

    const existingOptions = [
      { id: '2dec7514', name: 'Unproven' },
      { id: '0aab3ca8', name: 'Fixture' },
      { id: 'fd58ad10', name: 'Research Ready' },
      { id: '37941e3f', name: 'Shadow' },
      { id: '1325597e', name: 'Paper Proven' },
      { id: '83718c1f', name: 'Live Proven' },
    ];
    const selected = selectInputOptions(evidence, existingOptions);

    expect(Object.fromEntries(selected.filter(({ id }) => id).map(({ name, id }) => [name, id])))
      .toEqual(Object.fromEntries(existingOptions.map(({ name, id }) => [name, id])));
  });

  it('fails closed when a required option ID is missing or names a different semantic option', () => {
    const field = {
      name: 'Evidence',
      options: [{
        name: 'Unproven',
        color: 'GRAY',
        description: 'No accepted evidence yet',
        preserveId: '2dec7514',
      }],
    };

    expect(() => selectInputOptions(field, [])).toThrow(/preserve missing option ID/i);
    expect(() => selectInputOptions(field, [{ id: '2dec7514', name: 'Fixture' }]))
      .toThrow(/incompatible name/i);
  });

  it('contains no item or repository-history delete mutation', () => {
    const source = readFileSync('scripts/github-projects/sync.mjs', 'utf8');
    expect(source).not.toMatch(/deleteProjectV2Item/i);
    expect(source).not.toMatch(/\bdelete(?:Issue|PullRequest)\b/i);
  });
});

describe('Roadmap Sync trusted-default-branch security boundary', () => {
  it('keeps PROJECT_TOKEN exclusively in the trusted reconciliation step', () => {
    const raw = readFileSync('.github/workflows/roadmap-sync.yml', 'utf8');
    const workflow = YAML.parse(raw) as {
      permissions?: Record<string, string>;
      jobs: Record<string, {
        permissions?: Record<string, string>;
        env?: Record<string, string>;
        steps: Array<{
          name?: string;
          uses?: string;
          with?: Record<string, unknown>;
          env?: Record<string, string>;
          run?: string;
        }>;
      }>;
    };
    const job = workflow.jobs.reconcile;
    const checkoutSteps = job.steps.filter((step) => step.uses?.startsWith('actions/checkout@'));
    const tokenSteps = job.steps.filter((step) => Object.hasOwn(step.env ?? {}, 'PROJECT_TOKEN'));

    expect(workflow.permissions).toEqual({ contents: 'read' });
    expect(job.permissions).toBeUndefined();
    expect(job.env).toBeUndefined();
    expect(checkoutSteps).toHaveLength(1);
    expect(checkoutSteps[0].with).toMatchObject({
      ref: '${{ github.event.repository.default_branch }}',
      'persist-credentials': false,
    });
    expect(checkoutSteps[0].with).not.toHaveProperty('token');
    expect(raw).not.toMatch(/github\.(?:head_ref|event\.pull_request\.head(?:\.|\b))/);
    expect(raw.match(/\$\{\{\s*secrets\.PROJECT_TOKEN\s*\}\}/g)).toHaveLength(1);
    expect(tokenSteps).toHaveLength(1);
    expect(tokenSteps[0]).toMatchObject({
      name: 'Reconcile user-owned GitHub Project',
      env: { PROJECT_TOKEN: '${{ secrets.PROJECT_TOKEN }}' },
      run: 'node scripts/github-projects/sync.mjs',
    });
    expect(job.steps.find((step) => step.run?.includes('--dry-run'))?.env).toBeUndefined();
  });
});
