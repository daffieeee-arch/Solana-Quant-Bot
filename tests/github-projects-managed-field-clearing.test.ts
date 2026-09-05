import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import {
  buildExpectedFinalFieldValues,
  clearProjectField,
  deriveMetadata,
  evaluateFinalItemProjection,
  evaluateItemFieldClearProjection,
  MANAGED_FIELD_ACTIONS,
  planManagedFieldReconciliation,
  PROJECT_FIELD_UNSET,
  PROJECT_VERIFICATION_OUTCOMES,
  reconcileItems,
  resolvePullRequestInheritanceRoute,
  verifyProjectProjection,
} from '../scripts/github-projects/sync.mjs';

const REPOSITORY = 'daffieeee-arch/Solana-Quant-Bot';
const PROJECT = { id: 'PROJECT_4', number: 4 };
const fullConfig = JSON.parse(readFileSync('roadmap/project-config.json', 'utf8'));
const impactFixture = JSON.parse(readFileSync(
  'tests/fixtures/github-projects/g0-managed-field-impact.json',
  'utf8',
));

type Field = {
  id: string;
  name: string;
  dataType: 'SINGLE_SELECT' | 'NUMBER' | 'DATE';
  options?: Array<{ id: string; name: string }>;
};

const configuredField = (name: string, dataType: Field['dataType'] = 'SINGLE_SELECT') => ({ name, dataType });

const fieldId = (name: string) => `FIELD_${name.replaceAll(' ', '_')}`;

const selectField = (name: string, optionNames: string[]): Field => ({
  id: fieldId(name),
  name,
  dataType: 'SINGLE_SELECT',
  options: optionNames.map((optionName) => ({
    id: `OPTION_${name.replaceAll(' ', '_')}_${optionName.replaceAll(' ', '_')}`,
    name: optionName,
  })),
});

const numberField = (name: string): Field => ({ id: fieldId(name), name, dataType: 'NUMBER' });
const dateField = (name: string): Field => ({ id: fieldId(name), name, dataType: 'DATE' });

const selectValue = (field: Field, name: string | null, optionId?: string | null) => ({
  __typename: 'ProjectV2ItemFieldSingleSelectValue',
  optionId: optionId === undefined ? field.options?.find((option) => option.name === name)?.id : optionId,
  name,
  field: { id: field.id, name: field.name },
});

const numberValue = (field: Field, number: number | null) => ({
  __typename: 'ProjectV2ItemFieldNumberValue',
  number,
  field: { id: field.id, name: field.name },
});

const dateValue = (field: Field, date: string | null) => ({
  __typename: 'ProjectV2ItemFieldDateValue',
  date,
  field: { id: field.id, name: field.name },
});

const normalizedSelect = (field: Field, name: string) => ({
  kind: 'SINGLE_SELECT',
  fieldId: field.id,
  optionId: field.options?.find((option) => option.name === name)?.id,
  name,
});

const normalizedNumber = (field: Field, number: number) => ({ kind: 'NUMBER', fieldId: field.id, number });
const normalizedDate = (field: Field, date: string) => ({ kind: 'DATE', fieldId: field.id, date });

const normalizedFixtureValue = (field: Field, value: string | number) => {
  if (field.dataType === 'SINGLE_SELECT') return normalizedSelect(field, String(value));
  if (field.dataType === 'NUMBER') return normalizedNumber(field, Number(value));
  return normalizedDate(field, String(value));
};

const content = (number: number, kind: 'Issue' | 'PullRequest' = 'PullRequest') => ({
  id: `${kind.toUpperCase()}_${number}`,
  kind,
  number,
  title: `${kind} ${number}`,
  body: '',
  state: 'OPEN',
  isDraft: false,
  merged: false,
});

const projectItem = (
  repositoryContent: ReturnType<typeof content>,
  nodes: unknown[],
  overrides: Record<string, unknown> = {},
) => ({
  id: `ITEM_${repositoryContent.number}`,
  isArchived: false,
  updatedAt: '2026-09-02T00:00:00.000Z',
  content: {
    id: repositoryContent.id,
    __typename: repositoryContent.kind,
    number: repositoryContent.number,
    url: `https://github.test/${repositoryContent.kind.toLowerCase()}/${repositoryContent.number}`,
    repository: { nameWithOwner: REPOSITORY },
  },
  fieldValues: {
    nodes,
    pageInfo: { hasNextPage: false, endCursor: null },
  },
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

const fieldsMap = (fields: Field[]) => new Map(fields.map((field) => [field.name, field]));

const oneItemState = (
  repositoryContent: ReturnType<typeof content>,
  item: ReturnType<typeof projectItem>,
  metadata: Record<string, unknown>,
  action = 'KEEP_ACTIVE',
) => ({
  issues: repositoryContent.kind === 'Issue' ? [repositoryContent] : [],
  pullRequests: repositoryContent.kind === 'PullRequest' ? [repositoryContent] : [],
  projectItems: [item],
  repositoryItems: [item],
  metadataByContentId: new Map([[repositoryContent.id, metadata]]),
  plan: {
    operations: [{ content: repositoryContent, item, action, reason: 'test', eligible: action !== 'KEEP_ARCHIVED' }],
    counts: {
      ADD: 0,
      KEEP_ACTIVE: action === 'KEEP_ACTIVE' ? 1 : 0,
      ARCHIVE: 0,
      UNARCHIVE: 0,
      KEEP_ARCHIVED: action === 'KEEP_ARCHIVED' ? 1 : 0,
      SKIP: 0,
    },
    reasonCounts: { test: 1 },
    baselines: { expectedPreV2MergedPullRequests: 0, observedPreV2MergedPullRequests: 0 },
  },
});

const verifier = () => {
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

const clearResult = (itemId: string) => ({
  clearProjectV2ItemFieldValue: { projectV2Item: { id: itemId } },
});

describe('Roadmap Sync exact managed-field clearing', () => {
  it('plans the exact PR #69 stale-field regression without placeholders', () => {
    const fieldNames = fullConfig.fields.map((field: { name: string }) => field.name);
    expect(fieldNames).toEqual([
      'Status', 'Priority', 'Area', 'Work Type', 'Phase', 'V2 Phase', 'V2 Disposition',
      'Risk', 'Evidence', 'Effort', 'Start date', 'Target date',
    ]);
    const actualFields = fullConfig.fields.map((field: { name: string; dataType: Field['dataType']; options?: Array<{ name: string }> }) => (
      field.dataType === 'SINGLE_SELECT'
        ? selectField(field.name, (field.options ?? []).map((option) => option.name))
        : field.dataType === 'NUMBER' ? numberField(field.name) : dateField(field.name)
    ));
    const byName = fieldsMap(actualFields);
    const current = new Map([
      ['Status', normalizedSelect(byName.get('Status')!, 'Done')],
      ['Priority', normalizedSelect(byName.get('Priority')!, 'P1')],
      ['Area', normalizedSelect(byName.get('Area')!, 'Platform')],
      ['Work Type', normalizedSelect(byName.get('Work Type')!, 'Pull Request')],
      ['Phase', normalizedSelect(byName.get('Phase')!, '0 Governance')],
      ['V2 Phase', normalizedSelect(byName.get('V2 Phase')!, '0 Cutover & Cleanup')],
      ['V2 Disposition', normalizedSelect(byName.get('V2 Disposition')!, 'SUPERSEDED')],
      ['Risk', normalizedSelect(byName.get('Risk')!, 'High')],
      ['Evidence', normalizedSelect(byName.get('Evidence')!, 'Unproven')],
    ]);
    const actions = planManagedFieldReconciliation({
      configuredFields: fullConfig.fields,
      fieldsByName: byName,
      metadata: {
        workflow: 'Done', priority: 'P1', area: 'Platform', type: 'Pull Request',
        phase: '0 Governance', risk: 'High', evidence: 'Unproven',
      },
      currentValues: current,
    });

    expect(actions.map((action) => action.fieldName)).toEqual(fieldNames);
    expect(actions.filter((action) => action.action === MANAGED_FIELD_ACTIONS.SET)).toHaveLength(0);
    expect(actions.filter((action) => action.action === MANAGED_FIELD_ACTIONS.CLEAR).map((action) => action.fieldName))
      .toEqual(['V2 Phase', 'V2 Disposition']);
    expect(actions.filter((action) => action.action === MANAGED_FIELD_ACTIONS.NO_OP)).toHaveLength(10);
    expect(actions.filter((action) => action.action === MANAGED_FIELD_ACTIONS.CLEAR)
      .every((action) => action.expectedValue === PROJECT_FIELD_UNSET)).toBe(true);
    const expectedFinal = buildExpectedFinalFieldValues({ currentValues: current, actions });
    expect(expectedFinal.has('V2 Phase')).toBe(false);
    expect(expectedFinal.has('V2 Disposition')).toBe(false);
    expect(Object.fromEntries([...expectedFinal].map(([name, value]) => [name, value.name]))).toEqual({
      Status: 'Done',
      Priority: 'P1',
      Area: 'Platform',
      'Work Type': 'Pull Request',
      Phase: '0 Governance',
      Risk: 'High',
      Evidence: 'Unproven',
    });
  });

  it('executes one clear once, reads until absence, and reports a separate clear count', async () => {
    const phase = selectField('V2 Phase', ['0 Cutover & Cleanup']);
    const status = selectField('Status', ['Done']);
    const pullRequest = content(69);
    const stale = projectItem(pullRequest, [
      selectValue(status, 'Done'),
      selectValue(phase, '0 Cutover & Cleanup'),
    ]);
    const cleared = projectItem(pullRequest, [selectValue(status, 'Done')]);
    const reads = [[stale], [cleared]];
    const mutationInputs: unknown[] = [];
    const api = {
      request: vi.fn(async (query: string, variables: unknown, label: string) => {
        if (label === 'clear V2 Phase') {
          mutationInputs.push(variables);
          expect(query).toContain('clearProjectV2ItemFieldValue');
          return clearResult('ITEM_69');
        }
        if (label === 'list project items') return projectItemsResponse(reads.shift() ?? [cleared]);
        throw new Error(`unexpected GraphQL operation ${label}`);
      }),
    };
    const verification = verifier();

    const summary = await reconcileItems(
      api,
      { repository: REPOSITORY, fields: [configuredField('V2 Phase')] },
      PROJECT,
      fieldsMap([phase]),
      oneItemState(pullRequest, stale, {}),
      verification.options,
    );

    expect(mutationInputs).toEqual([{ input: { projectId: 'PROJECT_4', itemId: 'ITEM_69', fieldId: phase.id } }]);
    expect(summary.executed).toMatchObject({ fieldUpdates: 0, fieldClears: 1 });
    expect(summary.managedFieldActions).toEqual({
      planned: { SET: 0, CLEAR: 1, NO_OP: 0 },
      verified: { SET: 0, CLEAR: 1, NO_OP: 0 },
    });
    expect(verification.sleeps).toEqual([500]);
    expect(verification.logs).toEqual(expect.arrayContaining([
      expect.objectContaining({
        event: 'project_verification_delayed', operationType: 'CLEAR_ITEM_FIELD', reason: 'FIELD_NOT_VISIBLE',
      }),
      expect.objectContaining({
        event: 'project_verification_converged', operationType: 'CLEAR_ITEM_FIELD', verificationAttempt: 1,
      }),
    ]));
  });

  it('replans a later item from the latest verified snapshot and never clears an already absent field', async () => {
    const status = selectField('Status', ['Backlog', 'Ready']);
    const phase = selectField('V2 Phase', ['0 Cutover & Cleanup']);
    const first = content(90, 'Issue');
    const second = content(91, 'Issue');
    const firstBefore = projectItem(first, [selectValue(status, 'Backlog')]);
    const firstReady = projectItem(first, [selectValue(status, 'Ready')]);
    const secondStale = projectItem(second, [selectValue(phase, '0 Cutover & Cleanup')]);
    const secondAlreadyClear = projectItem(second, []);
    const mutations: string[] = [];
    const api = {
      request: vi.fn(async (_query: string, _variables: unknown, label: string) => {
        if (label === 'set Status') {
          mutations.push(label);
          return { updateProjectV2ItemFieldValue: { projectV2Item: { id: 'ITEM_90' } } };
        }
        if (label.startsWith('clear ')) {
          mutations.push(label);
          return clearResult('ITEM_91');
        }
        if (label === 'list project items') return projectItemsResponse([firstReady, secondAlreadyClear]);
        throw new Error(`unexpected GraphQL operation ${label}`);
      }),
    };
    const state = {
      issues: [first, second],
      pullRequests: [],
      projectItems: [firstBefore, secondStale],
      repositoryItems: [firstBefore, secondStale],
      metadataByContentId: new Map([[first.id, { workflow: 'Ready' }], [second.id, {}]]),
      plan: {
        operations: [
          { content: first, item: firstBefore, action: 'KEEP_ACTIVE', reason: 'test', eligible: true },
          { content: second, item: secondStale, action: 'KEEP_ACTIVE', reason: 'test', eligible: true },
        ],
        counts: { ADD: 0, KEEP_ACTIVE: 2, ARCHIVE: 0, UNARCHIVE: 0, KEEP_ARCHIVED: 0, SKIP: 0 },
        reasonCounts: { test: 2 },
        baselines: { expectedPreV2MergedPullRequests: 0, observedPreV2MergedPullRequests: 0 },
      },
    };

    const summary = await reconcileItems(
      api,
      {
        repository: REPOSITORY,
        fields: [configuredField('Status'), configuredField('V2 Phase')],
      },
      PROJECT,
      fieldsMap([status, phase]),
      state,
      verifier().options,
    );

    expect(mutations).toEqual(['set Status']);
    expect(summary.executed).toMatchObject({ fieldUpdates: 1, fieldClears: 0 });
    expect(summary.managedFieldActions.planned).toEqual({ SET: 1, CLEAR: 0, NO_OP: 3 });
  });

  it('fails closed after five stale reads and never executes the next clear', async () => {
    const phase = selectField('V2 Phase', ['0 Cutover & Cleanup']);
    const disposition = selectField('V2 Disposition', ['SUPERSEDED']);
    const pullRequest = content(69);
    const stale = projectItem(pullRequest, [
      selectValue(phase, '0 Cutover & Cleanup'),
      selectValue(disposition, 'SUPERSEDED'),
    ]);
    const mutations: string[] = [];
    const api = {
      request: vi.fn(async (_query: string, _variables: unknown, label: string) => {
        if (label.startsWith('clear ')) {
          mutations.push(label);
          return clearResult('ITEM_69');
        }
        if (label === 'list project items') return projectItemsResponse([stale]);
        throw new Error(`unexpected GraphQL operation ${label}`);
      }),
    };
    const verification = verifier();

    await expect(reconcileItems(
      api,
      { repository: REPOSITORY, fields: [configuredField('V2 Phase'), configuredField('V2 Disposition')] },
      PROJECT,
      fieldsMap([phase, disposition]),
      oneItemState(pullRequest, stale, {}),
      verification.options,
    )).rejects.toThrow(/PROJECT_VERIFICATION_EXHAUSTED.*CLEAR_ITEM_FIELD.*attempts=5.*UNSET.*finalObserved/i);

    expect(mutations).toEqual(['clear V2 Phase']);
    expect(verification.sleeps).toEqual([500, 1_000, 2_000, 4_000]);
  });

  it('classifies a contradictory third value as immediate HARD_DRIFT', async () => {
    const phase = selectField('V2 Phase', ['0 Cutover & Cleanup', '1 Pump Protocol Truth']);
    const pullRequest = content(69);
    const before = new Map([['V2 Phase', normalizedSelect(phase, '0 Cutover & Cleanup')]]);
    const verification = verifier();
    await expect(verifyProjectProjection({
      operationType: 'CLEAR_ITEM_FIELD',
      identity: 'PullRequest #69 field=V2 Phase',
      read: async () => [projectItem(pullRequest, [selectValue(phase, '1 Pump Protocol Truth')])],
      classify: (items) => evaluateItemFieldClearProjection({
        items,
        targetItemId: 'ITEM_69',
        content: pullRequest,
        repository: REPOSITORY,
        beforeValues: before,
        field: phase,
      }),
      ...verification.options,
    })).rejects.toThrow(/PROJECT_HARD_DRIFT.*UNEXPECTED_FIELD_VALUE/i);
    expect(verification.sleeps).toEqual([]);
    expect(verification.logs).toEqual([]);
  });

  it('accepts only compatible partial target projection as delayed, then true absence', async () => {
    const phase = selectField('V2 Phase', ['0 Cutover & Cleanup']);
    const pullRequest = content(69);
    const before = new Map([['V2 Phase', normalizedSelect(phase, '0 Cutover & Cleanup')]]);
    const reads = [
      [projectItem(pullRequest, [selectValue(phase, null, null)])],
      [projectItem(pullRequest, [])],
    ];
    const verification = verifier();

    const result = await verifyProjectProjection({
      operationType: 'CLEAR_ITEM_FIELD',
      identity: 'PullRequest #69 field=V2 Phase',
      read: async () => reads.shift(),
      classify: (items) => evaluateItemFieldClearProjection({
        items,
        targetItemId: 'ITEM_69',
        content: pullRequest,
        repository: REPOSITORY,
        beforeValues: before,
        field: phase,
      }),
      ...verification.options,
    });

    expect(result.attempts).toBe(2);
    expect(verification.sleeps).toEqual([500]);
    expect(verification.logs).toEqual(expect.arrayContaining([
      expect.objectContaining({ reason: 'FIELD_NOT_VISIBLE', verificationAttempt: 0 }),
      expect.objectContaining({ event: 'project_verification_converged', verificationAttempt: 1 }),
    ]));
    expect(result.result.outcome).toBe(PROJECT_VERIFICATION_OUTCOMES.CONVERGED);
  });

  it('plans NO_OP for true absent/absent state and performs no mutation', async () => {
    const phase = selectField('V2 Phase', ['0 Cutover & Cleanup']);
    const pullRequest = content(69);
    const clean = projectItem(pullRequest, []);
    const actions = planManagedFieldReconciliation({
      configuredFields: [configuredField('V2 Phase')],
      fieldsByName: fieldsMap([phase]),
      metadata: {},
      currentValues: new Map(),
    });
    expect(actions).toMatchObject([{ action: 'NO_OP', expectedValue: { kind: 'UNSET' }, reason: 'ALREADY_UNSET' }]);

    const api = { request: vi.fn() };
    const summary = await reconcileItems(
      api,
      { repository: REPOSITORY, fields: [configuredField('V2 Phase')] },
      PROJECT,
      fieldsMap([phase]),
      oneItemState(pullRequest, clean, {}),
      verifier().options,
    );
    expect(api.request).not.toHaveBeenCalled();
    expect(summary.executed.fieldClears).toBe(0);
  });

  it('keeps a direct PR metadata override SET when the owner omits the field', () => {
    const phase = selectField('V2 Phase', ['1 Pump Protocol Truth']);
    const issue62 = { ...content(62, 'Issue'), title: 'Cross-cutting control' };
    const pullRequest = {
      ...content(90),
      body: 'Roadmap: #62\n\n<!-- roadmap-meta\n{"schemaVersion":1,"v2Phase":"1 Pump Protocol Truth"}\n-->',
    };
    const metadata = deriveMetadata(pullRequest, new Map([[62, issue62]]), REPOSITORY);
    const actions = planManagedFieldReconciliation({
      configuredFields: [configuredField('V2 Phase')],
      fieldsByName: fieldsMap([phase]),
      metadata,
      currentValues: new Map(),
    });

    expect(resolvePullRequestInheritanceRoute({ body: pullRequest.body, issuesByNumber: new Map([[62, issue62]]), repository: REPOSITORY }))
      .toMatchObject({ primaryIssueNumber: 62 });
    expect(actions).toMatchObject([{ action: 'SET', desiredValue: '1 Pump Protocol Truth' }]);
  });

  it('clears a value omitted by a changed owner and handles an unrouted PR without guessing', () => {
    const priority = selectField('Priority', ['P1']);
    const status = selectField('Status', ['In Review']);
    const type = selectField('Work Type', ['Pull Request']);
    const phase = selectField('V2 Phase', ['0 Cutover & Cleanup']);
    const oldOwner = {
      ...content(56, 'Issue'),
      body: '<!-- roadmap-meta\n{"schemaVersion":1,"v2Phase":"0 Cutover & Cleanup"}\n-->',
    };
    const newOwner = content(62, 'Issue');
    const ownerIssues = new Map([[56, oldOwner], [62, newOwner]]);
    const oldMetadata = deriveMetadata({ ...content(89), body: 'Roadmap: #56' }, ownerIssues, REPOSITORY);
    const newMetadata = deriveMetadata({ ...content(89), body: 'Roadmap: #62' }, ownerIssues, REPOSITORY);
    expect(oldMetadata.v2Phase).toBe('0 Cutover & Cleanup');
    expect(newMetadata.v2Phase).toBeUndefined();
    const ownerChange = planManagedFieldReconciliation({
      configuredFields: [configuredField('V2 Phase')],
      fieldsByName: fieldsMap([phase]),
      metadata: newMetadata,
      currentValues: new Map([['V2 Phase', normalizedSelect(phase, oldMetadata.v2Phase)]]),
    });
    expect(ownerChange[0]).toMatchObject({ action: 'CLEAR', reason: 'STALE_MANAGED_VALUE' });

    const unrouted = { ...content(66), body: 'This tracks historical issue #56 in prose.' };
    const issues = new Map([[56, content(56, 'Issue')]]);
    const route = resolvePullRequestInheritanceRoute({ body: unrouted.body, issuesByNumber: issues, repository: REPOSITORY });
    const metadata = deriveMetadata(unrouted, issues, REPOSITORY);
    const fields = fieldsMap([status, priority, type]);
    const current = new Map([
      ['Status', normalizedSelect(status, 'In Review')],
      ['Priority', normalizedSelect(priority, 'P1')],
      ['Work Type', normalizedSelect(type, 'Pull Request')],
    ]);
    const actions = planManagedFieldReconciliation({
      configuredFields: [configuredField('Status'), configuredField('Priority'), configuredField('Work Type')],
      fieldsByName: fields,
      metadata,
      currentValues: current,
    });
    expect(route.source).toBe('NONE');
    expect(actions.map(({ fieldName, action }) => [fieldName, action])).toEqual([
      ['Status', 'NO_OP'], ['Priority', 'CLEAR'], ['Work Type', 'NO_OP'],
    ]);
    const cleanActions = planManagedFieldReconciliation({
      configuredFields: [configuredField('Status'), configuredField('Priority'), configuredField('Work Type')],
      fieldsByName: fields,
      metadata,
      currentValues: new Map([
        ['Status', normalizedSelect(status, 'In Review')],
        ['Work Type', normalizedSelect(type, 'Pull Request')],
      ]),
    });
    expect(cleanActions.every((action) => action.action === 'NO_OP')).toBe(true);
  });

  it('preserves supported unmanaged fields and hard-fails unrelated clear drift', () => {
    const phase = selectField('V2 Phase', ['0 Cutover & Cleanup']);
    const userField = selectField('User Score', ['Reviewed']);
    const pullRequest = content(69);
    const before = new Map([
      ['V2 Phase', normalizedSelect(phase, '0 Cutover & Cleanup')],
      ['User Score', normalizedSelect(userField, 'Reviewed')],
    ]);
    const actions = planManagedFieldReconciliation({
      configuredFields: [configuredField('V2 Phase')],
      fieldsByName: fieldsMap([phase]),
      metadata: {},
      currentValues: before,
    });
    expect(buildExpectedFinalFieldValues({ currentValues: before, actions }))
      .toEqual(new Map([['User Score', normalizedSelect(userField, 'Reviewed')]]));

    const ignoredUnsupported = evaluateItemFieldClearProjection({
      items: [projectItem(pullRequest, [
        selectValue(userField, 'Reviewed'),
        {
          __typename: 'ProjectV2ItemFieldTextValue',
          text: 'operator note',
          field: { id: 'FIELD_Operator_Notes', name: 'Operator Notes' },
        },
      ])],
      targetItemId: 'ITEM_69',
      content: pullRequest,
      repository: REPOSITORY,
      beforeValues: before,
      field: phase,
    });
    expect(ignoredUnsupported.outcome).toBe(PROJECT_VERIFICATION_OUTCOMES.CONVERGED);

    const changed = evaluateItemFieldClearProjection({
      items: [projectItem(pullRequest, [
        selectValue(phase, '0 Cutover & Cleanup'),
        selectValue(userField, null, 'OPTION_User_Score_Other'),
      ])],
      targetItemId: 'ITEM_69',
      content: pullRequest,
      repository: REPOSITORY,
      beforeValues: before,
      field: phase,
    });
    expect(changed.outcome).toBe(PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT);
  });

  it('clears stale number and date fields in deterministic config order', async () => {
    const effort = numberField('Effort');
    const start = dateField('Start date');
    const target = dateField('Target date');
    const repositoryIssue = content(90, 'Issue');
    const initial = projectItem(repositoryIssue, [
      numberValue(effort, 8), dateValue(start, '2026-09-01'), dateValue(target, '2026-09-30'),
    ]);
    const afterEffort = projectItem(repositoryIssue, [dateValue(start, '2026-09-01'), dateValue(target, '2026-09-30')]);
    const afterStart = projectItem(repositoryIssue, [dateValue(target, '2026-09-30')]);
    const afterTarget = projectItem(repositoryIssue, []);
    const reads = [[afterEffort], [afterStart], [afterTarget]];
    const mutations: Array<{ label: string; variables: unknown }> = [];
    const api = {
      request: vi.fn(async (_query: string, variables: unknown, label: string) => {
        if (label.startsWith('clear ')) {
          mutations.push({ label, variables });
          return clearResult('ITEM_90');
        }
        if (label === 'list project items') return projectItemsResponse(reads.shift() ?? [afterTarget]);
        throw new Error(`unexpected GraphQL operation ${label}`);
      }),
    };

    const summary = await reconcileItems(
      api,
      {
        repository: REPOSITORY,
        fields: [configuredField('Effort', 'NUMBER'), configuredField('Start date', 'DATE'), configuredField('Target date', 'DATE')],
      },
      PROJECT,
      fieldsMap([effort, start, target]),
      oneItemState(repositoryIssue, initial, {}),
      verifier().options,
    );

    expect(mutations.map(({ label }) => label)).toEqual(['clear Effort', 'clear Start date', 'clear Target date']);
    expect(mutations.map(({ variables }) => variables)).toEqual([
      { input: { projectId: 'PROJECT_4', itemId: 'ITEM_90', fieldId: effort.id } },
      { input: { projectId: 'PROJECT_4', itemId: 'ITEM_90', fieldId: start.id } },
      { input: { projectId: 'PROJECT_4', itemId: 'ITEM_90', fieldId: target.id } },
    ]);
    expect(summary.executed).toMatchObject({ fieldUpdates: 0, fieldClears: 3 });
  });

  it('does not plan or execute field mutations for an archived item', async () => {
    const phase = selectField('V2 Phase', ['0 Cutover & Cleanup']);
    const pullRequest = content(1);
    const archived = projectItem(pullRequest, [selectValue(phase, '0 Cutover & Cleanup')], { isArchived: true });
    const api = { request: vi.fn() };

    const summary = await reconcileItems(
      api,
      { repository: REPOSITORY, fields: [configuredField('V2 Phase')] },
      PROJECT,
      fieldsMap([phase]),
      oneItemState(pullRequest, archived, {}, 'KEEP_ARCHIVED'),
      verifier().options,
    );

    expect(api.request).not.toHaveBeenCalled();
    expect(summary.managedFieldActions.planned).toEqual({ SET: 0, CLEAR: 0, NO_OP: 0 });
    expect(summary.executed).toMatchObject({ unarchived: 0, fieldUpdates: 0, fieldClears: 0 });
  });

  it('keeps deterministic config ordering and distinguishes SET/CLEAR/NO_OP', () => {
    const evidence = selectField('Evidence', ['Unproven']);
    const effort = numberField('Effort');
    const target = dateField('Target date');
    const actions = planManagedFieldReconciliation({
      configuredFields: [
        configuredField('Evidence'), configuredField('Effort', 'NUMBER'), configuredField('Target date', 'DATE'),
      ],
      fieldsByName: fieldsMap([target, evidence, effort]),
      metadata: { evidence: 'Unproven', targetDate: '2026-09-30' },
      currentValues: new Map([
        ['Evidence', normalizedSelect(evidence, 'Unproven')],
        ['Effort', normalizedNumber(effort, 5)],
      ]),
    });
    expect(actions.map(({ fieldName, action }) => [fieldName, action])).toEqual([
      ['Evidence', 'NO_OP'], ['Effort', 'CLEAR'], ['Target date', 'SET'],
    ]);
  });

  it('final audit exhausts on a stale managed clear, accepts absence, and rejects unmanaged drift', async () => {
    const phase = selectField('V2 Phase', ['0 Cutover & Cleanup']);
    const userField = numberField('User Score');
    const pullRequest = content(69);
    const before = new Map([
      ['V2 Phase', normalizedSelect(phase, '0 Cutover & Cleanup')],
      ['User Score', normalizedNumber(userField, 7)],
    ]);
    const actions = planManagedFieldReconciliation({
      configuredFields: [configuredField('V2 Phase')],
      fieldsByName: fieldsMap([phase]),
      metadata: {},
      currentValues: before,
    });
    const base = {
      config: { repository: REPOSITORY },
      beforeValuesByContentId: new Map([[pullRequest.id, before]]),
      expectedValuesByContentId: new Map([[
        pullRequest.id,
        buildExpectedFinalFieldValues({ currentValues: before, actions }),
      ]]),
      fieldActionsByContentId: new Map([[pullRequest.id, actions]]),
      itemIdByContentId: new Map([[pullRequest.id, 'ITEM_69']]),
      plan: {
        operations: [{ content: pullRequest, item: projectItem(pullRequest, []), action: 'KEEP_ACTIVE' }],
      },
    };
    const stale = projectItem(pullRequest, [
      selectValue(phase, '0 Cutover & Cleanup'), numberValue(userField, 7),
    ]);
    const verification = verifier();
    await expect(verifyProjectProjection({
      operationType: 'FINAL_ITEM_AUDIT',
      identity: 'Project #4',
      read: async () => [stale],
      classify: (items) => evaluateFinalItemProjection({ items, ...base }),
      ...verification.options,
    })).rejects.toThrow(/PROJECT_VERIFICATION_EXHAUSTED.*attempts=5.*FIELD_NOT_VISIBLE/i);

    const cleared = evaluateFinalItemProjection({
      items: [projectItem(pullRequest, [numberValue(userField, 7)])],
      ...base,
    });
    expect(cleared.outcome).toBe(PROJECT_VERIFICATION_OUTCOMES.CONVERGED);

    const unrelatedChanged = evaluateFinalItemProjection({
      items: [projectItem(pullRequest, [numberValue(userField, 8)])],
      ...base,
    });
    expect(unrelatedChanged.outcome).toBe(PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT);
  });

  it('requires the clear mutation to return the exact target item identity', async () => {
    const phase = selectField('V2 Phase', ['0 Cutover & Cleanup']);
    const api = { request: vi.fn(async () => clearResult('ITEM_OTHER')) };
    await expect(clearProjectField(api, 'PROJECT_4', 'ITEM_69', phase))
      .rejects.toThrow(/unexpected Project item identity ITEM_OTHER/i);
    expect(api.request).toHaveBeenCalledTimes(1);
  });

  it('never retries an ambiguous clear mutation error or starts verification', async () => {
    const phase = selectField('V2 Phase', ['0 Cutover & Cleanup']);
    const api = {
      request: vi.fn(async () => { throw new Error('ambiguous GraphQL transport failure'); }),
    };
    const laterRead = vi.fn();

    await expect(clearProjectField(api, 'PROJECT_4', 'ITEM_69', phase))
      .rejects.toThrow(/ambiguous GraphQL transport failure/i);
    expect(api.request).toHaveBeenCalledTimes(1);
    expect(laterRead).not.toHaveBeenCalled();
  });

  it('hard-fails a missing target item and a truncated item-field projection', () => {
    const phase = selectField('V2 Phase', ['0 Cutover & Cleanup']);
    const pullRequest = content(69);
    const before = new Map([['V2 Phase', normalizedSelect(phase, '0 Cutover & Cleanup')]]);
    expect(evaluateItemFieldClearProjection({
      items: [],
      targetItemId: 'ITEM_69',
      content: pullRequest,
      repository: REPOSITORY,
      beforeValues: before,
      field: phase,
      projectionLagEligibleItemIds: new Set(['ITEM_69']),
    }).outcome).toBe(PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT);

    const truncated = projectItem(pullRequest, [], {
      fieldValues: { nodes: [], pageInfo: { hasNextPage: true, endCursor: 'CURSOR' } },
    });
    expect(evaluateItemFieldClearProjection({
      items: [truncated],
      targetItemId: 'ITEM_69',
      content: pullRequest,
      repository: REPOSITORY,
      beforeValues: before,
      field: phase,
    }).outcome).toBe(PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT);
  });

  it('hard-fails a partial lifecycle projection during clear verification', () => {
    const phase = selectField('V2 Phase', ['0 Cutover & Cleanup']);
    const pullRequest = content(69);
    const before = new Map([['V2 Phase', normalizedSelect(phase, '0 Cutover & Cleanup')]]);
    const partialLifecycle = projectItem(pullRequest, [selectValue(phase, '0 Cutover & Cleanup')], {
      isArchived: null,
    });

    const result = evaluateItemFieldClearProjection({
      items: [partialLifecycle],
      targetItemId: 'ITEM_69',
      content: pullRequest,
      repository: REPOSITORY,
      beforeValues: before,
      field: phase,
    });

    expect(result).toMatchObject({
      outcome: PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
      reason: 'UNEXPECTED_LIFECYCLE_STATE',
    });
  });

  it('models the reviewed active corpus as exactly two PR #69 clears and no archived mutations', () => {
    expect(impactFixture.source).toMatchObject({
      repositoryMainSha: '51d49fcbab52068f7ac59b4aced3040a30a6c9d4',
      sourceSnapshotSha256: '64ceca7db84138375ed2aef632fa580fa330a0e295af40f8f1ca33cf23b29039',
      projectNumber: 4,
      totalItems: 88,
      activeItems: 58,
      archivedItems: 30,
    });
    expect(impactFixture.managedFields.map((field: Field) => field.name))
      .toEqual(fullConfig.fields.map((field: { name: string }) => field.name));
    const byName = fieldsMap(impactFixture.managedFields);
    const actionsByContent = impactFixture.active.map((row: {
      kind: 'Issue' | 'PullRequest';
      number: number;
      metadata: Record<string, unknown>;
      current: Record<string, string | number>;
    }) => ({
      identity: `${row.kind} #${row.number}`,
      actions: planManagedFieldReconciliation({
        configuredFields: fullConfig.fields,
        fieldsByName: byName,
        metadata: row.metadata,
        currentValues: new Map(Object.entries(row.current).map(([name, value]) => [
          name,
          normalizedFixtureValue(byName.get(name)!, value),
        ])),
      }),
    }));
    const counts = actionsByContent.flatMap(({ actions }) => actions).reduce((result, action) => {
      result[action.action] += 1;
      return result;
    }, { SET: 0, CLEAR: 0, NO_OP: 0 });
    expect(actionsByContent).toHaveLength(58);
    expect(actionsByContent.flatMap(({ actions }) => actions)).toHaveLength(58 * 12);
    expect(counts).toEqual({ SET: 0, CLEAR: 2, NO_OP: 694 });
    expect(actionsByContent.flatMap(({ identity, actions }) => actions
      .filter((action) => action.action === 'CLEAR')
      .map((action) => `${identity} — ${action.fieldName}`))).toEqual([
      'PullRequest #69 — V2 Phase',
      'PullRequest #69 — V2 Disposition',
    ]);

    const routes = new Map(impactFixture.active
      .filter((row: { kind: string }) => row.kind === 'PullRequest')
      .map((row: { number: number; route: unknown }) => [row.number, row.route]));
    expect(routes.get(66)).toEqual({ source: 'NONE', secondaryIssueNumbers: [], orderedIssueNumbers: [] });
    expect(routes.get(69)).toMatchObject({ source: 'DIRECT_ROADMAP', primaryIssueNumber: 62, secondaryIssueNumbers: [63] });
    for (const number of [71, 73, 88]) expect(routes.get(number)).toMatchObject({ primaryIssueNumber: 70 });

    expect(impactFixture.archived.map((item: { kind: string; number: number }) => `${item.kind} #${item.number}`))
      .toEqual([
        ...Array.from({ length: 26 }, (_, index) => `PullRequest #${index + 1}`),
        'PullRequest #64', 'PullRequest #65', 'PullRequest #67', 'PullRequest #68',
      ]);
  });
});
