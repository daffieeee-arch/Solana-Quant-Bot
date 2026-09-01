import { describe, expect, it, vi } from 'vitest';
import {
  ensureFields,
  ensureProject,
  ensureViews,
  evaluateItemFieldProjection,
  evaluateItemLifecycleProjection,
  PROJECT_VERIFICATION_DELAYS_MS,
  PROJECT_VERIFICATION_OUTCOMES,
  planRepositoryItems,
  reconcileItems,
  validateFieldPreconditions,
  verifyProjectProjection,
} from '../scripts/github-projects/sync.mjs';

const REPOSITORY = 'daffieeee-arch/solana-paper-scanner';

const issue = (number: number) => ({
  id: `ISSUE_${number}`,
  kind: 'Issue',
  number,
  title: `Issue ${number}`,
  body: '',
  state: 'OPEN',
});

const fieldValue = (fieldName: string, optionId: string, name: string) => ({
  __typename: 'ProjectV2ItemFieldSingleSelectValue',
  optionId,
  name,
  field: { id: `FIELD_${fieldName}`, name: fieldName },
});

const projectItem = (
  content: ReturnType<typeof issue>,
  overrides: Record<string, unknown> = {},
) => ({
  id: `ITEM_${content.number}`,
  isArchived: false,
  updatedAt: '2026-09-01T00:00:00.000Z',
  content: {
    id: content.id,
    __typename: content.kind,
    number: content.number,
    url: `https://github.test/issues/${content.number}`,
    repository: { nameWithOwner: REPOSITORY },
  },
  fieldValues: { nodes: [] },
  ...overrides,
});

const projectItemsResponse = (nodes: unknown[]) => ({
  node: {
    items: {
      nodes,
      pageInfo: { hasNextPage: false, endCursor: null },
    },
  },
});

const projectFieldsResponse = (nodes: unknown[]) => ({
  node: {
    fields: {
      nodes,
      pageInfo: { hasNextPage: false, endCursor: null },
    },
  },
});

const projectViewsResponse = (nodes: unknown[]) => ({
  node: {
    views: {
      nodes,
      pageInfo: { hasNextPage: false, endCursor: null },
    },
  },
});

const verificationOptions = () => {
  const sleeps: number[] = [];
  const logs: unknown[] = [];
  return {
    sleeps,
    logs,
    options: {
      sleep: async (milliseconds: number) => { sleeps.push(milliseconds); },
      log: (entry: unknown) => { logs.push(entry); },
    },
  };
};

describe('Roadmap Sync bounded Project projection verification', () => {
  it('uses five deterministic reads and no more than 7.5 seconds of bounded wait', () => {
    expect(PROJECT_VERIFICATION_DELAYS_MS).toEqual([0, 500, 1_000, 2_000, 4_000]);
    expect(PROJECT_VERIFICATION_DELAYS_MS.reduce((total, delay) => total + delay, 0)).toBe(7_500);
  });

  it('rejects a hard-drift reason mislabeled as retryable', async () => {
    const verifier = verificationOptions();
    await expect(verifyProjectProjection({
      operationType: 'ADD_ITEM',
      identity: 'Issue #72',
      read: async () => [],
      classify: () => ({
        outcome: PROJECT_VERIFICATION_OUTCOMES.NOT_YET_CONVERGED,
        reason: 'DUPLICATE_CONTENT',
        expected: 'one item',
        observed: 'duplicate items',
      }),
      ...verifier.options,
    })).rejects.toThrow(/incompatible outcome\/reason/i);
    expect(verifier.sleeps).toEqual([]);
  });

  it('rejects a foreign Project item during planning before any mutation can run', async () => {
    const repositoryIssue = issue(72);
    const foreignItem = projectItem(repositoryIssue, {
      content: {
        id: repositoryIssue.id,
        __typename: 'Issue',
        number: repositoryIssue.number,
        repository: { nameWithOwner: 'foreign/repository' },
      },
    });
    const labels: string[] = [];
    const api = {
      request: vi.fn(async (_query: string, _variables: unknown, label: string) => {
        labels.push(label);
        if (label === 'list repository issues') {
          return {
            repository: {
              issues: {
                nodes: [{
                  ...repositoryIssue,
                  url: 'https://github.test/issues/72',
                  stateReason: null,
                  createdAt: '2026-09-01T00:00:00.000Z',
                  updatedAt: '2026-09-01T00:00:00.000Z',
                  closedAt: null,
                  reopened: { nodes: [] },
                }],
                pageInfo: { hasNextPage: false, endCursor: null },
              },
            },
          };
        }
        if (label === 'list repository pull requests') {
          return {
            repository: {
              pullRequests: {
                nodes: [],
                pageInfo: { hasNextPage: false, endCursor: null },
              },
            },
          };
        }
        if (label === 'list project items') return projectItemsResponse([foreignItem]);
        throw new Error(`unexpected GraphQL operation ${label}`);
      }),
    };

    await expect(planRepositoryItems(api, { repository: REPOSITORY }, { id: 'PROJECT_4' }, '2026-09-01T00:00:00.000Z'))
      .rejects.toThrow(/unexpected repository foreign\/repository/i);

    expect(labels).toEqual(expect.arrayContaining([
      'list repository issues', 'list repository pull requests', 'list project items',
    ]));
    expect(labels.some((label) => /^(?:add|archive|unarchive|set|update|create|link)\b/i.test(label))).toBe(false);
  });

  it('represents run 33560315744: ADD #72 is missing once, then converges without mutation replay', async () => {
    const issue72 = issue(72);
    const existingActive = Array.from({ length: 41 }, (_, index) => projectItem(issue(index + 1)));
    const existingArchived = Array.from({ length: 30 }, (_, index) => projectItem(issue(index + 42), {
      isArchived: true,
    }));
    const existing = [...existingActive, ...existingArchived];
    const addedWithoutFields = projectItem(issue72);
    const fieldDefinitions = [
      ['Status', 'STATUS_READY', 'Ready'],
      ['Priority', 'PRIORITY_P0', 'P0'],
      ['Area', 'AREA_PROGRAM', 'Program'],
      ['Work Type', 'TYPE_PROGRAM', 'Program'],
      ['V2 Phase', 'PHASE_0', '0 Cutover & Cleanup'],
      ['V2 Disposition', 'DISPOSITION_ACTIVE', 'ACTIVE NOW'],
      ['Risk', 'RISK_CRITICAL', 'Critical'],
      ['Evidence', 'EVIDENCE_NA', 'Not Applicable'],
    ] as const;
    const withProjectedFieldCount = (count: number) => [...existing, projectItem(issue72, {
      fieldValues: {
        nodes: fieldDefinitions.slice(0, count).map(([name, optionId, optionName]) => (
          fieldValue(name, optionId, optionName)
        )),
      },
    })];
    const reads = [
      existing,
      [...existing, addedWithoutFields],
      [...existing, addedWithoutFields],
      ...Array.from({ length: fieldDefinitions.length }, (_, index) => withProjectedFieldCount(index + 1)),
    ];
    const mutationLabels: string[] = [];
    const api = {
      request: vi.fn(async (_query: string, _variables: unknown, label: string) => {
        if (label === 'add Issue #72') {
          mutationLabels.push(label);
          return { addProjectV2ItemById: { item: { id: 'ITEM_72' } } };
        }
        if (label.startsWith('set ')) {
          mutationLabels.push(label);
          return { updateProjectV2ItemFieldValue: { projectV2Item: { id: 'ITEM_72' } } };
        }
        if (label === 'list project items') {
          const snapshot = reads.shift();
          if (!snapshot) throw new Error('unexpected extra Project items read');
          return projectItemsResponse(snapshot);
        }
        throw new Error(`unexpected GraphQL operation ${label}`);
      }),
    };
    const allIssues = [...Array.from({ length: 71 }, (_, index) => issue(index + 1)), issue72];
    const operations = [
      ...existingActive.map((itemNode, index) => ({
        content: allIssues[index], item: itemNode, action: 'KEEP_ACTIVE', reason: 'open_item_active', eligible: true,
      })),
      ...existingArchived.map((itemNode, index) => ({
        content: allIssues[index + 41], item: itemNode, action: 'KEEP_ARCHIVED', reason: 'archived_closed_item', eligible: false,
      })),
      { content: issue72, item: undefined, action: 'ADD', reason: 'eligible_missing_item', eligible: true },
    ];
    const metadataByContentId = new Map(allIssues.map((content) => [content.id, {}]));
    metadataByContentId.set(issue72.id, {
      workflow: 'Ready',
      priority: 'P0',
      area: 'Program',
      type: 'Program',
      v2Phase: '0 Cutover & Cleanup',
      v2Disposition: 'ACTIVE NOW',
      risk: 'Critical',
      evidence: 'Not Applicable',
    });
    const fieldsByName = new Map(fieldDefinitions.map(([name, optionId, optionName]) => [name, {
      id: `FIELD_${name}`,
      name,
      dataType: 'SINGLE_SELECT',
      options: [{ id: optionId, name: optionName }],
    }]));
    const verifier = verificationOptions();

    const summary = await reconcileItems(
      api,
      { repository: REPOSITORY },
      { id: 'PROJECT_4', number: 4 },
      fieldsByName,
      {
        issues: allIssues,
        pullRequests: [],
        repositoryItems: existing,
        metadataByContentId,
        plan: {
          operations,
          counts: {
            ADD: 1,
            KEEP_ACTIVE: 41,
            ARCHIVE: 0,
            UNARCHIVE: 0,
            KEEP_ARCHIVED: 30,
            SKIP: 0,
          },
          reasonCounts: {
            eligible_missing_item: 1,
            open_item_active: 41,
            archived_closed_item: 30,
          },
          baselines: { expectedPreV2MergedPullRequests: 30, observedPreV2MergedPullRequests: 30 },
        },
      },
      verifier.options,
    );

    expect(mutationLabels).toEqual([
      'add Issue #72',
      ...fieldDefinitions.map(([name]) => `set ${name}`),
    ]);
    expect(api.request.mock.calls.filter((call) => call[2] === 'list project items')).toHaveLength(11);
    expect(verifier.sleeps).toEqual([500, 500]);
    expect(summary.planned).toEqual({
      ADD: 1,
      KEEP_ACTIVE: 41,
      ARCHIVE: 0,
      UNARCHIVE: 0,
      KEEP_ARCHIVED: 30,
      SKIP: 0,
    });
    expect(summary.executed).toEqual({ added: 1, archived: 0, unarchived: 0, fieldUpdates: 8 });
    expect(summary.verified).toEqual({ active: 42, archived: 30, absent: 0 });
    expect(verifier.logs).toEqual(expect.arrayContaining([
      expect.objectContaining({
        event: 'project_verification_delayed',
        operationType: 'ADD_ITEM',
        identity: 'Issue #72',
        verificationAttempt: 0,
        reason: 'ITEM_NOT_VISIBLE',
      }),
      expect.objectContaining({
        event: 'project_verification_converged',
        operationType: 'ADD_ITEM',
        identity: 'Issue #72',
        verificationAttempt: 1,
      }),
    ]));
  });

  it('re-verifies a compatibly stale aggregate snapshot read-only and preserves exact counts', async () => {
    const first = issue(90);
    const second = issue(91);
    const firstBacklog = projectItem(first, {
      fieldValues: { nodes: [fieldValue('Status', 'BACKLOG', 'Backlog')] },
    });
    const firstReady = projectItem(first, {
      fieldValues: { nodes: [fieldValue('Status', 'READY', 'Ready')] },
    });
    const secondActive = projectItem(second);
    const snapshots = [
      [firstReady],
      [firstBacklog, secondActive],
      [firstReady, secondActive],
    ];
    const mutations: string[] = [];
    const api = {
      request: vi.fn(async (_query: string, _variables: unknown, label: string) => {
        if (label === 'set Status') {
          mutations.push(label);
          return { updateProjectV2ItemFieldValue: { projectV2Item: { id: 'ITEM_90' } } };
        }
        if (label === 'add Issue #91') {
          mutations.push(label);
          return { addProjectV2ItemById: { item: { id: 'ITEM_91' } } };
        }
        if (label === 'list project items') return projectItemsResponse(snapshots.shift() ?? []);
        throw new Error(`unexpected GraphQL operation ${label}`);
      }),
    };
    const statusField = {
      id: 'FIELD_Status', name: 'Status', dataType: 'SINGLE_SELECT',
      options: [{ id: 'BACKLOG', name: 'Backlog' }, { id: 'READY', name: 'Ready' }],
    };
    const verifier = verificationOptions();

    const summary = await reconcileItems(api, { repository: REPOSITORY }, { id: 'PROJECT_4', number: 4 }, new Map([
      ['Status', statusField],
    ]), {
      issues: [first, second],
      pullRequests: [],
      projectItems: [firstBacklog],
      repositoryItems: [firstBacklog],
      metadataByContentId: new Map([[first.id, { workflow: 'Ready' }], [second.id, {}]]),
      plan: {
        operations: [
          { content: first, item: firstBacklog, action: 'KEEP_ACTIVE', reason: 'open', eligible: true },
          { content: second, item: undefined, action: 'ADD', reason: 'add', eligible: true },
        ],
        counts: { ADD: 1, KEEP_ACTIVE: 1, ARCHIVE: 0, UNARCHIVE: 0, KEEP_ARCHIVED: 0, SKIP: 0 },
        reasonCounts: { open: 1, add: 1 },
        baselines: { expectedPreV2MergedPullRequests: 0, observedPreV2MergedPullRequests: 0 },
      },
    }, verifier.options);

    expect(mutations).toEqual(['set Status', 'add Issue #91']);
    expect(summary.executed).toEqual({ added: 1, archived: 0, unarchived: 0, fieldUpdates: 1 });
    expect(summary.verified).toEqual({ active: 2, archived: 0, absent: 0 });
    expect(verifier.sleeps).toEqual([500]);
    expect(verifier.logs).toEqual(expect.arrayContaining([
      expect.objectContaining({
        event: 'project_verification_delayed',
        operationType: 'FINAL_ITEM_AUDIT',
        reason: 'FIELD_NOT_VISIBLE',
      }),
      expect.objectContaining({
        event: 'project_verification_converged',
        operationType: 'FINAL_ITEM_AUDIT',
        verificationAttempt: 1,
      }),
    ]));
  });

  it('waits for partially hydrated item fields and accepts only the exact final option IDs', async () => {
    const target = issue(72);
    const beforeValues = new Map([
      ['Status', { kind: 'SINGLE_SELECT', optionId: 'BACKLOG', name: 'Backlog' }],
      ['Evidence', { kind: 'SINGLE_SELECT', optionId: 'UNPROVEN', name: 'Unproven' }],
    ]);
    const expectedValues = new Map([
      ['Status', { kind: 'SINGLE_SELECT', optionId: 'READY', name: 'Ready' }],
      ['Evidence', { kind: 'SINGLE_SELECT', optionId: 'UNPROVEN', name: 'Unproven' }],
    ]);
    const partial = projectItem(target, {
      fieldValues: { nodes: [fieldValue('Evidence', 'UNPROVEN', 'Unproven')] },
    });
    const complete = projectItem(target, {
      fieldValues: {
        nodes: [
          fieldValue('Status', 'READY', 'Ready'),
          fieldValue('Evidence', 'UNPROVEN', 'Unproven'),
        ],
      },
    });
    const reads = [[partial], [complete]];
    const verifier = verificationOptions();

    const result = await verifyProjectProjection({
      operationType: 'UPDATE_ITEM_FIELDS',
      identity: 'Issue #72',
      read: async () => reads.shift(),
      classify: (items) => evaluateItemFieldProjection({
        items,
        targetItemId: 'ITEM_72',
        content: target,
        repository: REPOSITORY,
        beforeValues,
        expectedValues,
      }),
      ...verifier.options,
    });

    expect(result.attempts).toBe(2);
    expect(verifier.sleeps).toEqual([500]);
    expect(verifier.logs[0]).toMatchObject({ reason: 'FIELD_NOT_VISIBLE' });
  });

  it('allows a newly added item to disappear once during field projection without replaying mutations', async () => {
    const target = issue(73);
    const visibleWithoutFields = projectItem(target);
    const visibleWithField = projectItem(target, {
      fieldValues: { nodes: [fieldValue('Status', 'READY', 'Ready')] },
    });
    const snapshots = [[visibleWithoutFields], [], [visibleWithField]];
    const mutations: string[] = [];
    const api = {
      request: vi.fn(async (_query: string, _variables: unknown, label: string) => {
        if (label === 'add Issue #73') {
          mutations.push(label);
          return { addProjectV2ItemById: { item: { id: 'ITEM_73' } } };
        }
        if (label === 'set Status') {
          mutations.push(label);
          return { updateProjectV2ItemFieldValue: { projectV2Item: { id: 'ITEM_73' } } };
        }
        if (label === 'list project items') return projectItemsResponse(snapshots.shift() ?? []);
        throw new Error(`unexpected GraphQL operation ${label}`);
      }),
    };
    const verifier = verificationOptions();

    const summary = await reconcileItems(api, { repository: REPOSITORY }, { id: 'PROJECT_4', number: 4 }, new Map([
      ['Status', {
        id: 'FIELD_STATUS', name: 'Status', dataType: 'SINGLE_SELECT',
        options: [{ id: 'READY', name: 'Ready' }],
      }],
    ]), {
      issues: [target],
      pullRequests: [],
      projectItems: [],
      repositoryItems: [],
      metadataByContentId: new Map([[target.id, { workflow: 'Ready' }]]),
      plan: {
        operations: [{ content: target, item: undefined, action: 'ADD', reason: 'add', eligible: true }],
        counts: { ADD: 1, KEEP_ACTIVE: 0, ARCHIVE: 0, UNARCHIVE: 0, KEEP_ARCHIVED: 0, SKIP: 0 },
        reasonCounts: { add: 1 },
        baselines: { expectedPreV2MergedPullRequests: 0, observedPreV2MergedPullRequests: 0 },
      },
    }, verifier.options);

    expect(mutations).toEqual(['add Issue #73', 'set Status']);
    expect(summary.executed).toEqual({ added: 1, archived: 0, unarchived: 0, fieldUpdates: 1 });
    expect(verifier.sleeps).toEqual([500]);
    expect(verifier.logs[0]).toMatchObject({
      operationType: 'UPDATE_ITEM_FIELD',
      reason: 'ITEM_NOT_VISIBLE',
    });
  });

  it('allows an earlier mutation-created item to disappear during a later ADD verification', async () => {
    const first = issue(73);
    const second = issue(74);
    const firstItem = projectItem(first);
    const secondItem = projectItem(second);
    const snapshots = [
      [firstItem],
      [secondItem],
      [firstItem, secondItem],
    ];
    const mutations: string[] = [];
    const api = {
      request: vi.fn(async (_query: string, _variables: unknown, label: string) => {
        if (label === 'add Issue #73') {
          mutations.push(label);
          return { addProjectV2ItemById: { item: { id: 'ITEM_73' } } };
        }
        if (label === 'add Issue #74') {
          mutations.push(label);
          return { addProjectV2ItemById: { item: { id: 'ITEM_74' } } };
        }
        if (label === 'list project items') return projectItemsResponse(snapshots.shift() ?? []);
        throw new Error(`unexpected GraphQL operation ${label}`);
      }),
    };
    const verifier = verificationOptions();

    const summary = await reconcileItems(api, { repository: REPOSITORY }, { id: 'PROJECT_4', number: 4 }, new Map(), {
      issues: [first, second],
      pullRequests: [],
      projectItems: [],
      repositoryItems: [],
      metadataByContentId: new Map([[first.id, {}], [second.id, {}]]),
      plan: {
        operations: [
          { content: first, item: undefined, action: 'ADD', reason: 'add', eligible: true },
          { content: second, item: undefined, action: 'ADD', reason: 'add', eligible: true },
        ],
        counts: { ADD: 2, KEEP_ACTIVE: 0, ARCHIVE: 0, UNARCHIVE: 0, KEEP_ARCHIVED: 0, SKIP: 0 },
        reasonCounts: { add: 2 },
        baselines: { expectedPreV2MergedPullRequests: 0, observedPreV2MergedPullRequests: 0 },
      },
    }, verifier.options);

    expect(mutations).toEqual(['add Issue #73', 'add Issue #74']);
    expect(summary.executed).toEqual({ added: 2, archived: 0, unarchived: 0, fieldUpdates: 0 });
    expect(summary.verified).toEqual({ active: 2, archived: 0, absent: 0 });
    expect(verifier.sleeps).toEqual([500]);
    expect(verifier.logs).toEqual(expect.arrayContaining([
      expect.objectContaining({
        operationType: 'ADD_ITEM',
        identity: 'Issue #74',
        reason: 'ITEM_NOT_VISIBLE',
      }),
    ]));
  });

  it('waits read-only for a preserved option name while performing no item-field mutation', async () => {
    const target = issue(74);
    const stale = projectItem(target, {
      fieldValues: { nodes: [fieldValue('Status', 'READY', 'Legacy Ready')] },
    });
    const exact = projectItem(target, {
      fieldValues: { nodes: [fieldValue('Status', 'READY', 'Ready')] },
    });
    const snapshots = [[stale], [exact]];
    const mutations: string[] = [];
    const api = {
      request: vi.fn(async (_query: string, _variables: unknown, label: string) => {
        if (label.startsWith('set ')) mutations.push(label);
        if (label === 'list project items') return projectItemsResponse(snapshots.shift() ?? []);
        throw new Error(`unexpected GraphQL operation ${label}`);
      }),
    };
    const verifier = verificationOptions();

    const summary = await reconcileItems(api, { repository: REPOSITORY }, { id: 'PROJECT_4', number: 4 }, new Map([
      ['Status', {
        id: 'FIELD_STATUS', name: 'Status', dataType: 'SINGLE_SELECT',
        options: [{ id: 'READY', name: 'Ready' }],
      }],
    ]), {
      issues: [target],
      pullRequests: [],
      projectItems: [stale],
      repositoryItems: [stale],
      metadataByContentId: new Map([[target.id, { workflow: 'Ready' }]]),
      plan: {
        operations: [{ content: target, item: stale, action: 'KEEP_ACTIVE', reason: 'keep', eligible: true }],
        counts: { ADD: 0, KEEP_ACTIVE: 1, ARCHIVE: 0, UNARCHIVE: 0, KEEP_ARCHIVED: 0, SKIP: 0 },
        reasonCounts: { keep: 1 },
        baselines: { expectedPreV2MergedPullRequests: 0, observedPreV2MergedPullRequests: 0 },
      },
    }, verifier.options);

    expect(mutations).toEqual([]);
    expect(summary.executed.fieldUpdates).toBe(0);
    expect(verifier.sleeps).toEqual([500]);
    expect(verifier.logs[0]).toMatchObject({ operationType: 'VERIFY_ITEM_FIELD', reason: 'FIELD_NOT_VISIBLE' });
  });

  it.each([
    { operationType: 'ARCHIVE_ITEM', before: false, expected: true },
    { operationType: 'UNARCHIVE_ITEM', before: true, expected: false },
  ])('waits for stale $operationType state in reconciliation without repeating its mutation', async ({ operationType, before, expected }) => {
    const target = issue(80);
    const targetItem = projectItem(target, { isArchived: before });
    const snapshots = [
      [targetItem],
      [projectItem(target, { isArchived: expected })],
    ];
    const mutationLabel = operationType === 'ARCHIVE_ITEM' ? 'archive Issue #80' : 'unarchive Issue #80';
    const mutations: string[] = [];
    const api = {
      request: vi.fn(async (_query: string, _variables: unknown, label: string) => {
        if (label === mutationLabel) {
          mutations.push(label);
          return operationType === 'ARCHIVE_ITEM'
            ? { archiveProjectV2Item: { item: { id: 'ITEM_80' } } }
            : { unarchiveProjectV2Item: { item: { id: 'ITEM_80' } } };
        }
        if (label === 'list project items') return projectItemsResponse(snapshots.shift() ?? []);
        throw new Error(`unexpected GraphQL operation ${label}`);
      }),
    };
    const action = operationType === 'ARCHIVE_ITEM' ? 'ARCHIVE' : 'UNARCHIVE';
    const counts = {
      ADD: 0,
      KEEP_ACTIVE: 0,
      ARCHIVE: action === 'ARCHIVE' ? 1 : 0,
      UNARCHIVE: action === 'UNARCHIVE' ? 1 : 0,
      KEEP_ARCHIVED: 0,
      SKIP: 0,
    };
    const verifier = verificationOptions();

    const result = await reconcileItems(api, { repository: REPOSITORY }, { id: 'PROJECT_4', number: 4 }, new Map(), {
      issues: [target],
      pullRequests: [],
      projectItems: [targetItem],
      repositoryItems: [targetItem],
      metadataByContentId: new Map([[target.id, {}]]),
      plan: {
        operations: [{ content: target, item: targetItem, action, reason: 'test', eligible: expected === false }],
        counts,
        reasonCounts: { test: 1 },
        baselines: { expectedPreV2MergedPullRequests: 0, observedPreV2MergedPullRequests: 0 },
      },
    }, verifier.options);

    expect(mutations).toEqual([mutationLabel]);
    expect(result.executed).toMatchObject(action === 'ARCHIVE' ? { archived: 1 } : { unarchived: 1 });
    expect(verifier.sleeps).toEqual([500]);
    expect(verifier.logs[0]).toMatchObject({ reason: 'ARCHIVE_STATE_NOT_VISIBLE' });
  });

  it('fails closed after the fixed budget and never repeats or advances mutations', async () => {
    const target = issue(72);
    const mutate = vi.fn(async () => undefined);
    const laterMutation = vi.fn(async () => undefined);
    const verifier = verificationOptions();

    await mutate();
    await expect(verifyProjectProjection({
      operationType: 'ADD_ITEM',
      identity: 'Issue #72',
      read: async () => [],
      classify: (items) => evaluateItemLifecycleProjection({
        items,
        targetItemId: 'ITEM_72',
        content: target,
        repository: REPOSITORY,
        expectedArchived: false,
        beforeArchived: undefined,
        allowMissing: true,
      }),
      ...verifier.options,
    })).rejects.toThrow(/PROJECT_VERIFICATION_EXHAUSTED.*attempts=5.*expected=.*finalObserved=item=absent/i);

    expect(mutate).toHaveBeenCalledTimes(1);
    expect(laterMutation).not.toHaveBeenCalled();
    expect(verifier.sleeps).toEqual([500, 1_000, 2_000, 4_000]);
    expect(verifier.logs).toHaveLength(5);
  });

  it('stops reconciliation before a second field mutation when the first field never projects', async () => {
    const target = issue(81);
    const targetItem = projectItem(target);
    const mutations: string[] = [];
    const api = {
      request: vi.fn(async (_query: string, _variables: unknown, label: string) => {
        if (label.startsWith('set ')) {
          mutations.push(label);
          return { updateProjectV2ItemFieldValue: { projectV2Item: { id: 'ITEM_81' } } };
        }
        if (label === 'list project items') return projectItemsResponse([targetItem]);
        throw new Error(`unexpected GraphQL operation ${label}`);
      }),
    };
    const fieldsByName = new Map([
      ['Status', {
        id: 'FIELD_STATUS', name: 'Status', dataType: 'SINGLE_SELECT',
        options: [{ id: 'READY', name: 'Ready' }],
      }],
      ['Priority', {
        id: 'FIELD_PRIORITY', name: 'Priority', dataType: 'SINGLE_SELECT',
        options: [{ id: 'P0', name: 'P0' }],
      }],
    ]);
    const verifier = verificationOptions();

    await expect(reconcileItems(api, { repository: REPOSITORY }, { id: 'PROJECT_4', number: 4 }, fieldsByName, {
      issues: [target],
      pullRequests: [],
      projectItems: [targetItem],
      repositoryItems: [targetItem],
      metadataByContentId: new Map([[target.id, { workflow: 'Ready', priority: 'P0' }]]),
      plan: {
        operations: [{ content: target, item: targetItem, action: 'KEEP_ACTIVE', reason: 'test', eligible: true }],
        counts: { ADD: 0, KEEP_ACTIVE: 1, ARCHIVE: 0, UNARCHIVE: 0, KEEP_ARCHIVED: 0, SKIP: 0 },
        reasonCounts: { test: 1 },
        baselines: { expectedPreV2MergedPullRequests: 0, observedPreV2MergedPullRequests: 0 },
      },
    }, verifier.options)).rejects.toThrow(/PROJECT_VERIFICATION_EXHAUSTED.*UPDATE_ITEM_FIELD.*attempts=5/i);

    expect(mutations).toEqual(['set Status']);
    expect(verifier.sleeps).toEqual([500, 1_000, 2_000, 4_000]);
  });

  it('fails immediately on a third field value or unrelated managed-field drift', async () => {
    const target = issue(72);
    const beforeValues = new Map([
      ['Status', { kind: 'SINGLE_SELECT', optionId: 'BACKLOG', name: 'Backlog' }],
      ['Evidence', { kind: 'SINGLE_SELECT', optionId: 'UNPROVEN', name: 'Unproven' }],
    ]);
    const expectedValues = new Map([
      ['Status', { kind: 'SINGLE_SELECT', optionId: 'READY', name: 'Ready' }],
      ['Evidence', { kind: 'SINGLE_SELECT', optionId: 'UNPROVEN', name: 'Unproven' }],
    ]);
    const contradictory = projectItem(target, {
      fieldValues: {
        nodes: [
          fieldValue('Status', 'THIRD_OPTION', 'Third'),
          fieldValue('Evidence', 'FIXTURE', 'Fixture'),
        ],
      },
    });
    const verifier = verificationOptions();

    await expect(verifyProjectProjection({
      operationType: 'UPDATE_ITEM_FIELDS',
      identity: 'Issue #72',
      read: async () => [contradictory],
      classify: (items) => evaluateItemFieldProjection({
        items,
        targetItemId: 'ITEM_72',
        content: target,
        repository: REPOSITORY,
        beforeValues,
        expectedValues,
      }),
      ...verifier.options,
    })).rejects.toThrow(/PROJECT_HARD_DRIFT.*UNEXPECTED_FIELD_VALUE/i);

    expect(verifier.sleeps).toEqual([]);
    expect(verifier.logs).toEqual([]);
  });

  it('never accepts duplicate content or wrong repository identity as convergence', () => {
    const target = issue(72);
    const duplicate = [
      projectItem(target),
      projectItem(target, { id: 'ITEM_72_DUPLICATE' }),
    ];
    const wrongRepository = [projectItem(target, {
      content: {
        id: target.id,
        __typename: target.kind,
        number: target.number,
        repository: { nameWithOwner: 'unexpected/repository' },
      },
    })];
    const partialContradiction = [projectItem(target, {
      content: { id: 'WRONG_CONTENT', __typename: target.kind, number: target.number },
    })];

    expect(evaluateItemLifecycleProjection({
      items: duplicate,
      targetItemId: 'ITEM_72',
      content: target,
      repository: REPOSITORY,
      expectedArchived: false,
      allowMissing: true,
    }).outcome).toBe(PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT);
    expect(evaluateItemLifecycleProjection({
      items: wrongRepository,
      targetItemId: 'ITEM_72',
      content: target,
      repository: REPOSITORY,
      expectedArchived: false,
      allowMissing: true,
    }).outcome).toBe(PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT);
    expect(evaluateItemLifecycleProjection({
      items: partialContradiction,
      targetItemId: 'ITEM_72',
      content: target,
      repository: REPOSITORY,
      expectedArchived: false,
      allowMissing: true,
    }).outcome).toBe(PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT);
  });

  it('hard-fails a contradictory kind or number on an unrelated captured item', () => {
    const target = issue(72);
    const unrelated = issue(71);
    const targetItem = projectItem(target);
    const wrongUnrelated = projectItem(unrelated, {
      content: {
        id: unrelated.id,
        __typename: 'PullRequest',
        number: 999,
        repository: { nameWithOwner: REPOSITORY },
      },
    });
    const result = evaluateItemLifecycleProjection({
      items: [targetItem, wrongUnrelated],
      targetItemId: targetItem.id,
      content: target,
      repository: REPOSITORY,
      expectedArchived: false,
      knownItemsById: new Map([
        [targetItem.id, { contentId: target.id, kind: 'Issue', number: 72, repository: REPOSITORY }],
        [wrongUnrelated.id, { contentId: unrelated.id, kind: 'Issue', number: 71, repository: REPOSITORY }],
      ]),
    });

    expect(result.outcome).toBe(PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT);
    expect(result.reason).toBe('CONTENT_IDENTITY_DRIFT');
  });

  it('rejects duplicate Project field identities before a Project mutation can begin', () => {
    const duplicateFields = [
      { id: 'FIELD_STATUS', name: 'Status', dataType: 'SINGLE_SELECT', options: [] },
      { id: 'FIELD_STATUS', name: 'Evidence', dataType: 'SINGLE_SELECT', options: [] },
    ];
    expect(() => validateFieldPreconditions({ fields: [] }, duplicateFields))
      .toThrow(/Project field identity is duplicated/i);
  });

  it.each([
    {
      name: 'foreign extra item',
      projected: (target: ReturnType<typeof issue>, original: ReturnType<typeof projectItem>) => [
        original,
        projectItem(issue(999), {
          content: {
            id: 'FOREIGN_999', __typename: 'Issue', number: 999,
            repository: { nameWithOwner: 'foreign/repository' },
          },
        }),
      ],
    },
    {
      name: 'replacement item identity',
      projected: (target: ReturnType<typeof issue>) => [projectItem(target, { id: 'REPLACEMENT_ITEM' })],
    },
  ])('hard-fails the exact final snapshot on $name', async ({ projected }) => {
    const target = issue(82);
    const original = projectItem(target);
    const projectItems = projected(target, original);

    await expect(reconcileItems(
      { request: vi.fn() },
      { repository: REPOSITORY },
      { id: 'PROJECT_4', number: 4 },
      new Map(),
      {
        issues: [target],
        pullRequests: [],
        projectItems,
        repositoryItems: [original],
        metadataByContentId: new Map([[target.id, {}]]),
        plan: {
          operations: [{ content: target, item: original, action: 'KEEP_ACTIVE', reason: 'test', eligible: true }],
          counts: { ADD: 0, KEEP_ACTIVE: 1, ARCHIVE: 0, UNARCHIVE: 0, KEEP_ARCHIVED: 0, SKIP: 0 },
          reasonCounts: { test: 1 },
          baselines: { expectedPreV2MergedPullRequests: 0, observedPreV2MergedPullRequests: 0 },
        },
      },
      verificationOptions().options,
    )).rejects.toThrow(/PROJECT_HARD_DRIFT.*(?:UNEXPECTED_ITEM|CONTENT_IDENTITY_DRIFT)/i);
  });

  it('retries Project metadata reads while invoking updateProjectV2 exactly once', async () => {
    const before = {
      id: 'PROJECT_4', number: 4, title: 'Project', shortDescription: 'Old', readme: 'Old',
      closed: false, public: false,
      repositories: { nodes: [{ id: 'REPO', nameWithOwner: REPOSITORY }] },
    };
    const expected = { ...before, shortDescription: 'New', readme: 'New' };
    const reads = [before, expected];
    const mutations: string[] = [];
    const api = {
      request: vi.fn(async (_query: string, _variables: unknown, label: string) => {
        if (label === 'update project') {
          mutations.push(label);
          return { updateProjectV2: { projectV2: { id: 'PROJECT_4' } } };
        }
        if (label === 'verify project') return { node: reads.shift() };
        throw new Error(`unexpected GraphQL operation ${label}`);
      }),
    };
    const verifier = verificationOptions();

    const result = await ensureProject(api, {
      repository: REPOSITORY,
      project: { title: 'Project', shortDescription: 'New', readme: 'New' },
    }, {
      project: before,
      repository: { id: 'REPO', nameWithOwner: REPOSITORY },
    }, verifier.options);

    expect(mutations).toEqual(['update project']);
    expect(result.counts).toEqual({ updated: 1, repositoryLinks: 0 });
    expect(verifier.sleeps).toEqual([500]);
  });

  it('retries a repository-link projection while invoking linkProjectV2ToRepository exactly once', async () => {
    const before = {
      id: 'PROJECT_4', number: 4, title: 'Project', shortDescription: 'Current', readme: 'Current',
      closed: false, public: false,
      repositories: { nodes: [{ id: 'OTHER_REPO', nameWithOwner: 'trusted/other' }] },
    };
    const expected = {
      ...before,
      repositories: {
        nodes: [
          { id: 'OTHER_REPO', nameWithOwner: 'trusted/other' },
          { id: 'TARGET_REPO', nameWithOwner: REPOSITORY },
        ],
      },
    };
    const reads = [before, expected];
    const mutations: string[] = [];
    const api = {
      request: vi.fn(async (_query: string, _variables: unknown, label: string) => {
        if (label === 'link project to repository') {
          mutations.push(label);
          return { linkProjectV2ToRepository: { repository: { id: 'TARGET_REPO' } } };
        }
        if (label === 'verify project') return { node: reads.shift() };
        throw new Error(`unexpected GraphQL operation ${label}`);
      }),
    };
    const verifier = verificationOptions();

    const result = await ensureProject(api, {
      repository: REPOSITORY,
      project: { title: 'Project', shortDescription: 'Current', readme: 'Current' },
    }, {
      project: before,
      repository: { id: 'TARGET_REPO', nameWithOwner: REPOSITORY },
    }, verifier.options);

    expect(mutations).toEqual(['link project to repository']);
    expect(result.counts).toEqual({ updated: 0, repositoryLinks: 1 });
    expect(verifier.sleeps).toEqual([500]);
  });

  it('hard-fails when an unrelated repository link disappears during metadata projection', async () => {
    const before = {
      id: 'PROJECT_4', number: 4, title: 'Project', shortDescription: 'Old', readme: 'Old',
      closed: false, public: false,
      repositories: {
        nodes: [
          { id: 'TARGET_REPO', nameWithOwner: REPOSITORY },
          { id: 'OTHER_REPO', nameWithOwner: 'trusted/other' },
        ],
      },
    };
    const drifted = {
      ...before,
      shortDescription: 'New',
      readme: 'New',
      repositories: { nodes: [{ id: 'TARGET_REPO', nameWithOwner: REPOSITORY }] },
    };
    const api = {
      request: vi.fn(async (_query: string, _variables: unknown, label: string) => {
        if (label === 'update project') return { updateProjectV2: { projectV2: { id: 'PROJECT_4' } } };
        if (label === 'verify project') return { node: drifted };
        throw new Error(`unexpected GraphQL operation ${label}`);
      }),
    };
    const verifier = verificationOptions();

    await expect(ensureProject(api, {
      repository: REPOSITORY,
      project: { title: 'Project', shortDescription: 'New', readme: 'New' },
    }, {
      project: before,
      repository: { id: 'TARGET_REPO', nameWithOwner: REPOSITORY },
    }, verifier.options)).rejects.toThrow(/PROJECT_HARD_DRIFT.*PROJECT_SCHEMA_DRIFT/i);

    expect(verifier.sleeps).toEqual([]);
  });

  it('retries field option projection while invoking updateProjectV2Field exactly once', async () => {
    const beforeField = {
      id: 'FIELD_EVIDENCE', name: 'Evidence', dataType: 'SINGLE_SELECT',
      options: [{ id: 'UNPROVEN', name: 'Unproven', color: 'GRAY', description: 'Old' }],
    };
    const expectedField = {
      ...beforeField,
      options: [{ id: 'UNPROVEN', name: 'Unproven', color: 'GRAY', description: 'Current' }],
    };
    const desired = {
      name: 'Evidence', dataType: 'SINGLE_SELECT',
      options: [{
        name: 'Unproven', color: 'GRAY', description: 'Current', preserveId: 'UNPROVEN',
      }],
    };
    const reads = [[beforeField], [expectedField]];
    const mutations: string[] = [];
    const api = {
      request: vi.fn(async (_query: string, _variables: unknown, label: string) => {
        if (label === 'update field Evidence') {
          mutations.push(label);
          return { updateProjectV2Field: { projectV2Field: { id: 'FIELD_EVIDENCE' } } };
        }
        if (label === 'list project fields') return projectFieldsResponse(reads.shift() ?? []);
        throw new Error(`unexpected GraphQL operation ${label}`);
      }),
    };
    const verifier = verificationOptions();

    const result = await ensureFields(api, { fields: [desired] }, 'PROJECT_4', [beforeField], verifier.options);

    expect(mutations).toEqual(['update field Evidence']);
    expect(result.counts).toEqual({ created: 0, optionsUpdated: 1 });
    expect(verifier.sleeps).toEqual([500]);
  });

  it('retries a new field projection while invoking createProjectV2Field exactly once', async () => {
    const desired = {
      name: 'Evidence', dataType: 'SINGLE_SELECT',
      options: [{ name: 'Unproven', color: 'GRAY', description: 'No evidence' }],
    };
    const projected = {
      id: 'FIELD_EVIDENCE', name: 'Evidence', dataType: 'SINGLE_SELECT',
      options: [{ id: 'GENERATED_ID', name: 'Unproven', color: 'GRAY', description: 'No evidence' }],
    };
    const reads = [[], [projected]];
    const mutations: string[] = [];
    const api = {
      request: vi.fn(async (_query: string, _variables: unknown, label: string) => {
        if (label === 'create field Evidence') {
          mutations.push(label);
          return { createProjectV2Field: { projectV2Field: { id: 'FIELD_EVIDENCE' } } };
        }
        if (label === 'list project fields') return projectFieldsResponse(reads.shift() ?? []);
        throw new Error(`unexpected GraphQL operation ${label}`);
      }),
    };
    const verifier = verificationOptions();

    const result = await ensureFields(api, { fields: [desired] }, 'PROJECT_4', [], verifier.options);

    expect(mutations).toEqual(['create field Evidence']);
    expect(result.counts).toEqual({ created: 1, optionsUpdated: 0 });
    expect(verifier.sleeps).toEqual([500]);
  });

  it('binds a created field to the exact mutation-returned field identity', async () => {
    const desired = {
      name: 'Evidence', dataType: 'SINGLE_SELECT',
      options: [{ name: 'Unproven', color: 'GRAY', description: 'No evidence' }],
    };
    const wrongIdentity = {
      id: 'OTHER_FIELD', name: 'Evidence', dataType: 'SINGLE_SELECT',
      options: [{ id: 'GENERATED_ID', name: 'Unproven', color: 'GRAY', description: 'No evidence' }],
    };
    const mutations: string[] = [];
    const api = {
      request: vi.fn(async (_query: string, _variables: unknown, label: string) => {
        if (label === 'create field Evidence') {
          mutations.push(label);
          return { createProjectV2Field: { projectV2Field: { id: 'EXPECTED_FIELD' } } };
        }
        if (label === 'list project fields') return projectFieldsResponse([wrongIdentity]);
        throw new Error(`unexpected GraphQL operation ${label}`);
      }),
    };

    await expect(ensureFields(api, { fields: [desired] }, 'PROJECT_4', [], verificationOptions().options))
      .rejects.toThrow(/PROJECT_HARD_DRIFT.*FIELD_SCHEMA_DRIFT/i);
    expect(mutations).toEqual(['create field Evidence']);
  });

  it('hard-fails when an updated semantic option is projected under a substituted known ID', async () => {
    const beforeField = {
      id: 'FIELD_STATUS', name: 'Status', dataType: 'SINGLE_SELECT',
      options: [{ id: 'KNOWN_ID', name: 'Ready', color: 'GREEN', description: 'Old' }],
    };
    const substituted = {
      ...beforeField,
      options: [{ id: 'SUBSTITUTED_ID', name: 'Ready', color: 'GREEN', description: 'Current' }],
    };
    const desired = {
      name: 'Status', dataType: 'SINGLE_SELECT',
      options: [{ name: 'Ready', color: 'GREEN', description: 'Current' }],
    };
    const mutations: string[] = [];
    const api = {
      request: vi.fn(async (_query: string, _variables: unknown, label: string) => {
        if (label === 'update field Status') {
          mutations.push(label);
          return { updateProjectV2Field: { projectV2Field: { id: 'FIELD_STATUS' } } };
        }
        if (label === 'list project fields') return projectFieldsResponse([substituted]);
        throw new Error(`unexpected GraphQL operation ${label}`);
      }),
    };
    const verifier = verificationOptions();

    await expect(ensureFields(api, { fields: [desired] }, 'PROJECT_4', [beforeField], verifier.options))
      .rejects.toThrow(/PROJECT_HARD_DRIFT.*FIELD_SCHEMA_DRIFT/i);

    expect(mutations).toEqual(['update field Status']);
    expect(verifier.sleeps).toEqual([]);
  });

  it('hard-fails when an unrelated field definition changes during target-field projection', async () => {
    const targetBefore = {
      id: 'FIELD_EVIDENCE', name: 'Evidence', dataType: 'SINGLE_SELECT',
      options: [{ id: 'UNPROVEN', name: 'Unproven', color: 'GRAY', description: 'Old' }],
    };
    const unrelatedBefore = {
      id: 'FIELD_STATUS', name: 'Status', dataType: 'SINGLE_SELECT',
      options: [{ id: 'READY', name: 'Ready', color: 'GREEN', description: 'Ready' }],
    };
    const targetExpected = {
      ...targetBefore,
      options: [{ id: 'UNPROVEN', name: 'Unproven', color: 'GRAY', description: 'Current' }],
    };
    const unrelatedDrift = {
      ...unrelatedBefore,
      options: [{ id: 'READY', name: 'Ready', color: 'RED', description: 'Changed' }],
    };
    const desired = {
      name: 'Evidence', dataType: 'SINGLE_SELECT',
      options: [{ name: 'Unproven', color: 'GRAY', description: 'Current' }],
    };
    const api = {
      request: vi.fn(async (_query: string, _variables: unknown, label: string) => {
        if (label === 'update field Evidence') return { updateProjectV2Field: { projectV2Field: { id: 'FIELD_EVIDENCE' } } };
        if (label === 'list project fields') return projectFieldsResponse([targetExpected, unrelatedDrift]);
        throw new Error(`unexpected GraphQL operation ${label}`);
      }),
    };

    await expect(ensureFields(
      api,
      { fields: [desired] },
      'PROJECT_4',
      [targetBefore, unrelatedBefore],
      verificationOptions().options,
    )).rejects.toThrow(/PROJECT_HARD_DRIFT.*FIELD_SCHEMA_DRIFT/i);
  });

  it('retries view projection while invoking updateProjectV2View exactly once', async () => {
    const fieldsByName = new Map([['Status', { id: 'FIELD_STATUS' }]]);
    const beforeView = {
      id: 'VIEW_NOW', name: 'Now', layout: 'BOARD_LAYOUT', filter: 'old',
      configuration: { visibleFields: { nodes: [{ id: 'FIELD_STATUS', name: 'Status' }] } },
    };
    const expectedView = { ...beforeView, filter: 'new' };
    const reads = [[beforeView], [expectedView]];
    const mutations: string[] = [];
    const api = {
      request: vi.fn(async (_query: string, _variables: unknown, label: string) => {
        if (label === 'update view Now') {
          mutations.push(label);
          return { updateProjectV2View: { projectV2View: { id: 'VIEW_NOW' } } };
        }
        if (label === 'list project views') return projectViewsResponse(reads.shift() ?? []);
        throw new Error(`unexpected GraphQL operation ${label}`);
      }),
    };
    const verifier = verificationOptions();

    const result = await ensureViews(api, {
      views: [{ name: 'Now', layout: 'BOARD', filter: 'new', visibleFields: ['Status'] }],
    }, 'PROJECT_4', fieldsByName, [beforeView], verifier.options);

    expect(mutations).toEqual(['update view Now']);
    expect(result).toEqual({ created: 0, updated: 1 });
    expect(verifier.sleeps).toEqual([500]);
  });

  it('gates one view create before one required view update and never replays either mutation', async () => {
    const fieldsByName = new Map([['Status', { id: 'FIELD_STATUS' }]]);
    const createdView = {
      id: 'VIEW_NOW', name: 'Now', layout: 'BOARD_LAYOUT', filter: '',
      configuration: { visibleFields: { nodes: [{ id: 'FIELD_STATUS', name: 'Status' }] } },
    };
    const expectedView = { ...createdView, filter: 'status:Ready' };
    const reads = [[], [createdView], [createdView], [expectedView]];
    const mutations: string[] = [];
    const api = {
      request: vi.fn(async (_query: string, _variables: unknown, label: string) => {
        if (label === 'create view Now') {
          mutations.push(label);
          return { createProjectV2View: { projectV2View: { id: 'VIEW_NOW', name: 'Now', layout: 'BOARD_LAYOUT' } } };
        }
        if (label === 'update view Now') {
          mutations.push(label);
          return { updateProjectV2View: { projectV2View: { id: 'VIEW_NOW' } } };
        }
        if (label === 'list project views') return projectViewsResponse(reads.shift() ?? []);
        throw new Error(`unexpected GraphQL operation ${label}`);
      }),
    };
    const verifier = verificationOptions();

    const result = await ensureViews(api, {
      views: [{ name: 'Now', layout: 'BOARD', filter: 'status:Ready', visibleFields: ['Status'] }],
    }, 'PROJECT_4', fieldsByName, [], verifier.options);

    expect(mutations).toEqual(['create view Now', 'update view Now']);
    expect(result).toEqual({ created: 1, updated: 1 });
    expect(verifier.sleeps).toEqual([500, 500]);
  });

  it('hard-fails when an unrelated view changes during target-view projection', async () => {
    const fieldsByName = new Map([['Status', { id: 'FIELD_STATUS' }]]);
    const targetBefore = {
      id: 'VIEW_NOW', name: 'Now', layout: 'BOARD_LAYOUT', filter: 'old',
      configuration: { visibleFields: { nodes: [{ id: 'FIELD_STATUS', name: 'Status' }] } },
    };
    const unrelatedBefore = {
      id: 'VIEW_LATER', name: 'Later', layout: 'TABLE_LAYOUT', filter: 'later',
      configuration: { visibleFields: { nodes: [{ id: 'FIELD_STATUS', name: 'Status' }] } },
    };
    const targetExpected = { ...targetBefore, filter: 'new' };
    const unrelatedDrift = { ...unrelatedBefore, filter: 'changed' };
    const api = {
      request: vi.fn(async (_query: string, _variables: unknown, label: string) => {
        if (label === 'update view Now') return { updateProjectV2View: { projectV2View: { id: 'VIEW_NOW' } } };
        if (label === 'list project views') return projectViewsResponse([targetExpected, unrelatedDrift]);
        throw new Error(`unexpected GraphQL operation ${label}`);
      }),
    };

    await expect(ensureViews(api, {
      views: [{ name: 'Now', layout: 'BOARD', filter: 'new', visibleFields: ['Status'] }],
    }, 'PROJECT_4', fieldsByName, [targetBefore, unrelatedBefore], verificationOptions().options))
      .rejects.toThrow(/PROJECT_HARD_DRIFT.*VIEW_SCHEMA_DRIFT/i);
  });

  it('logs only bounded operation metadata and never leaks a token or issue body', async () => {
    const target = issue(72);
    const secret = 'ghp_never_log_this';
    const verifier = verificationOptions();
    const reads = [[], [projectItem(target)]];

    await verifyProjectProjection({
      operationType: 'ADD_ITEM',
      identity: 'Issue #72',
      read: async () => reads.shift(),
      classify: (items) => evaluateItemLifecycleProjection({
        items,
        targetItemId: 'ITEM_72',
        content: { ...target, body: secret },
        repository: REPOSITORY,
        expectedArchived: false,
        allowMissing: true,
      }),
      ...verifier.options,
    });

    expect(JSON.stringify(verifier.logs)).not.toContain(secret);
    expect(JSON.stringify(verifier.logs)).toContain('Issue #72');
  });
});
