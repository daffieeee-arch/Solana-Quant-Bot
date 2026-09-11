import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import {
  classifyRoadmapIntake,
  evaluateFinalItemProjection,
  planRepositoryItems,
  reconcileItems,
  roadmapContentFingerprint,
  validateProjectConfig,
} from '../scripts/github-projects/sync.mjs';

const REPOSITORY = 'daffieeee-arch/Solana-Quant-Bot';
const OWNER = 'daffieeee-arch';
const PROJECT = { id: 'PROJECT_4', number: 4 };
const NOW = '2026-09-11T12:00:00.000Z';
const marker = (value: unknown) => `<!-- roadmap-meta\n${JSON.stringify(value)}\n-->`;
const config = () => validateProjectConfig(JSON.parse(readFileSync('roadmap/project-config.json', 'utf8')));
const content = (number: number, kind = 'Issue', author = OWNER) => ({
  id: `${kind}_${number}`, kind, number, title: `${kind} ${number}`, body: '',
  author: { login: author }, state: 'OPEN', stateReason: null, merged: false, isDraft: false,
  url: `https://github.com/${REPOSITORY}/${kind === 'Issue' ? 'issues' : 'pull'}/${number}`,
  createdAt: '2026-09-01T00:00:00.000Z', updatedAt: NOW, closedAt: null as string | null,
  mergedAt: null as string | null, reopened: { nodes: [] },
});
const historical = () => config().itemRetention.pre_v2_merged_pr_numbers.map((number: number) => ({
  ...content(number, 'PullRequest'), state: 'MERGED', merged: true,
  closedAt: '2026-08-01T00:00:00.000Z', mergedAt: '2026-08-01T00:00:00.000Z',
}));
const connection = (nodes: unknown[]) => ({ nodes, pageInfo: { hasNextPage: false, endCursor: null } });
const item = (source: ReturnType<typeof content>, archived = false) => ({
  id: `ITEM_${source.kind}_${source.number}`, isArchived: archived,
  updatedAt: '2026-09-01T00:00:00.000Z',
  content: {
    id: source.id, __typename: source.kind, number: source.number,
    url: source.url, repository: { nameWithOwner: REPOSITORY },
  },
  fieldValues: connection([{
    __typename: 'ProjectV2ItemFieldSingleSelectValue', optionId: 'fixture', name: 'Fixture',
    field: { id: 'EVIDENCE', name: 'Evidence' },
  }]),
});
const fakeApi = (issues: unknown[], pullRequests: unknown[], items: unknown[]) => ({
  request: vi.fn(async (query: string, _variables: unknown, label: string) => {
    if (label === 'list repository issues' || label === 'list repository pull requests') {
      expect(query).toMatch(/author\s*\{\s*login\s*\}/);
      const key = label === 'list repository issues' ? 'issues' : 'pullRequests';
      return { repository: { [key]: connection(key === 'issues' ? issues : pullRequests) } };
    }
    if (label === 'list project items') return { node: { items: connection(items) } };
    throw new Error(`Unexpected operation: ${label}`);
  }),
});
const approve = (source: ReturnType<typeof content>) => ({
  kind: source.kind, number: source.number, author: source.author.login,
  contentSha256: roadmapContentFingerprint(source),
});

describe('Roadmap Sync public content intake', () => {
  it('requires an explicit checked-in policy and validates bounded unique exact-content pins', () => {
    const valid = config();
    expect(valid.contentIntake).toEqual({ trustedAuthors: [OWNER], reviewedExternalItems: [] });
    const without = structuredClone(valid);
    delete without.contentIntake;
    expect(() => validateProjectConfig(without)).toThrow(/contentIntake must be explicitly configured/);
    for (const policy of [
      { trustedAuthors: [], reviewedExternalItems: [] },
      { trustedAuthors: [OWNER, OWNER.toUpperCase()], reviewedExternalItems: [] },
      { trustedAuthors: [OWNER], reviewedExternalItems: [], trustAuthorAssociation: true },
      { trustedAuthors: [OWNER], reviewedExternalItems: [{ kind: 'Issue', number: 123, author: 'outsider' }] },
      { trustedAuthors: [OWNER], reviewedExternalItems: Array(501).fill(approve(content(123))) },
      { trustedAuthors: [OWNER], reviewedExternalItems: [approve(content(123)), approve(content(123))] },
    ]) {
      expect(() => validateProjectConfig({ ...valid, contentIntake: policy })).toThrow();
    }
  });

  it('never treats missing authors, associations, labels or claimed metadata as permission', () => {
    const policy = config().contentIntake;
    for (const source of [
      { ...content(123), author: null },
      { ...content(123), author: undefined },
      { ...content(123), author: { login: '' } },
      { ...content(123, 'Issue', 'outsider'), authorAssociation: 'OWNER', labels: ['roadmap-approved'],
        body: marker({ schemaVersion: 1, evidence: 'Live Proven', v2Disposition: 'ACTIVE NOW' }) },
      { ...content(123), body: undefined },
    ]) expect(classifyRoadmapIntake(source, policy).accepted).toBe(false);
    expect(classifyRoadmapIntake(content(123, 'Issue', OWNER.toUpperCase()), policy))
      .toEqual({ accepted: true, reason: 'TRUSTED_AUTHOR' });
    expect(() => classifyRoadmapIntake(content(123), undefined)).toThrow(/contentIntake/);
  });

  it('pins exact external content identity/title/body and invalidates approval after any content change', () => {
    const source = content(123, 'Issue', 'outsider');
    const policy = { trustedAuthors: [OWNER], reviewedExternalItems: [approve(source)] };
    expect(classifyRoadmapIntake(source, policy)).toEqual({ accepted: true, reason: 'REVIEWED_EXACT_CONTENT' });
    for (const change of [
      { id: 'replacement' }, { title: 'new title' }, { body: 'new body' },
      { author: { login: 'replacement' } }, { kind: 'PullRequest' }, { number: 124 },
    ]) expect(classifyRoadmapIntake({ ...source, ...change }, policy).accepted).toBe(false);
    // State is legitimately live: closing an already reviewed issue must not
    // require a fresh content approval, while its author cannot rewrite metadata.
    expect(classifyRoadmapIntake({ ...source, state: 'CLOSED' }, policy).accepted).toBe(true);
    expect(roadmapContentFingerprint(source)).toBe(roadmapContentFingerprint(structuredClone(source)));
  });

  it('skips malformed external issue/PR metadata and routing before they can block full-state sync', async () => {
    const ownerIssue = content(83);
    const externalIssue = { ...content(123, 'Issue', 'outsider'), body: '<!-- roadmap-meta {broken} -->' };
    const externalPr = { ...content(124, 'PullRequest', 'outsider'), body: 'Roadmap: #999\n<!-- roadmap-meta {broken} -->' };
    const api = fakeApi([ownerIssue, externalIssue], [...historical(), externalPr], []);
    const state = await planRepositoryItems(api, config(), PROJECT, NOW);
    expect(state.contentIntake).toEqual({ accepted: 31, excluded: 2 });
    expect(state.metadataByContentId.has(ownerIssue.id)).toBe(true);
    expect(state.metadataByContentId.has(externalIssue.id)).toBe(false);
    expect(state.metadataByContentId.has(externalPr.id)).toBe(false);
    for (const source of [externalIssue, externalPr]) {
      expect(state.plan.operations.find((operation: any) => operation.content.id === source.id))
        .toMatchObject({ action: 'SKIP', intakeExcluded: true, reason: 'INTAKE_EXTERNAL_REVIEW_REQUIRED' });
    }
    expect(JSON.stringify(state.plan.reasonCounts)).not.toContain('broken');
    expect(api.request).toHaveBeenCalledTimes(3);
  });

  it('keeps strict owner-authored malformed metadata errors instead of silently ignoring governance mistakes', async () => {
    const broken = { ...content(83), body: '<!-- roadmap-meta {broken} -->' };
    await expect(planRepositoryItems(fakeApi([broken], historical(), []), config(), PROJECT, NOW))
      .rejects.toThrow(/invalid JSON/);
  });

  it('never permits a trusted PR to inherit metadata from an unreviewed external issue', async () => {
    const issue = { ...content(123, 'Issue', 'outsider'), body: marker({ schemaVersion: 1, evidence: 'Live Proven' }) };
    const pr = { ...content(124, 'PullRequest'), body: 'Roadmap: #123' };
    const api = fakeApi([issue], [...historical(), pr], []);
    const state = await planRepositoryItems(api, config(), PROJECT, NOW);
    expect(state.metadataByContentId.has(pr.id)).toBe(false);
    expect(state.plan.operations.find((operation: any) => operation.content.id === pr.id))
      .toMatchObject({ action: 'SKIP', intakeExcluded: true, reason: 'INTAKE_INHERITANCE_REVIEW_REQUIRED' });
    expect(api.request).toHaveBeenCalledTimes(3);
  });

  it('pauses only dependent PRs when an external source changes after review; unrelated trusted PRs still reconcile', async () => {
    const external = { ...content(123, 'Issue', 'outsider'), body: marker({ schemaVersion: 1, evidence: 'Fixture' }) };
    const changed = { ...external, body: '<!-- roadmap-meta {broken} -->' };
    const paused = { ...content(124, 'PullRequest'), body: 'Roadmap: #123' };
    const normal = content(125, 'PullRequest');
    const settings = config();
    settings.contentIntake.reviewedExternalItems = [approve(external)];
    const old = historical();
    const normalItem = item(normal);
    normalItem.fieldValues = connection([
      { __typename: 'ProjectV2ItemFieldSingleSelectValue', optionId: 'review', name: 'In Review', field: { id: 'status', name: 'Status' } },
      { __typename: 'ProjectV2ItemFieldSingleSelectValue', optionId: 'pr', name: 'Pull Request', field: { id: 'type', name: 'Work Type' } },
    ]);
    const items = [...old.map((source: any) => item(source, true)), item(external), item(paused), normalItem];
    const before = JSON.stringify(items);
    const api = fakeApi([changed], [...old, paused, normal], items);
    const state = await planRepositoryItems(api, settings, PROJECT, NOW);
    expect(state.metadataByContentId.has(paused.id)).toBe(false);
    expect(state.metadataByContentId.get(normal.id)).toMatchObject({ workflow: 'In Review', type: 'Pull Request' });
    expect(state.plan.operations.find((operation: any) => operation.content.id === paused.id))
      .toMatchObject({ action: 'KEEP_ACTIVE', intakeExcluded: true, reason: 'INTAKE_INHERITANCE_REVIEW_REQUIRED' });
    const fields = new Map(settings.fields.map((field: any) => [field.name, { ...field, id: `FIELD_${field.name}` }]));
    fields.set('Status', { id: 'status', name: 'Status', dataType: 'SINGLE_SELECT', options: [{ id: 'review', name: 'In Review' }, { id: 'done', name: 'Done' }] });
    fields.set('Work Type', { id: 'type', name: 'Work Type', dataType: 'SINGLE_SELECT', options: [{ id: 'pr', name: 'Pull Request' }] });
    const summary = await reconcileItems(api, settings, PROJECT, fields, state, { log: () => {} });
    expect(summary.contentIntake).toEqual({ accepted: 31, excluded: 2 });
    expect(summary.managedFieldActions.planned.NO_OP).toBe(settings.fields.length);
    expect(summary.executed).toEqual({ added: 0, archived: 0, unarchived: 0, fieldUpdates: 0, fieldClears: 0 });
    expect(summary.verified).toEqual({ active: 3, archived: 30, absent: 0 });
    expect(JSON.stringify(items)).toBe(before);
    expect(api.request).toHaveBeenCalledTimes(3);
  });

  it('allows reviewed exact external issue/PR metadata and ordinary trusted primary-owner inheritance', async () => {
    const issue = { ...content(123, 'Issue', 'outsider'), body: marker({ schemaVersion: 1, evidence: 'Fixture' }) };
    const pr = { ...content(124, 'PullRequest', 'contributor'), body: 'Roadmap: #123' };
    const approved = config();
    approved.contentIntake.reviewedExternalItems = [approve(issue), approve(pr)];
    const state = await planRepositoryItems(fakeApi([issue], [...historical(), pr], []), approved, PROJECT, NOW);
    expect(state.contentIntake).toEqual({ accepted: 32, excluded: 0 });
    expect(state.metadataByContentId.get(pr.id)).toMatchObject({ evidence: 'Fixture', workflow: 'In Review' });
    expect(state.plan.operations.find((operation: any) => operation.content.id === pr.id)).toMatchObject({ action: 'ADD' });
  });

  it('preserves excluded active/archived items and historical retention without a single mutation', async () => {
    const active = { ...content(123, 'Issue', 'outsider'), state: 'CLOSED', closedAt: '2026-08-01T00:00:00.000Z', body: '<!-- roadmap-meta {broken} -->' };
    const archived = { ...content(124, 'PullRequest', 'outsider'), reopened: { nodes: [{ createdAt: NOW }] } };
    const absent = { ...content(125), author: null };
    const old = historical();
    const items = [...old.map((source: any) => item(source, true)), item(active), item(archived, true)];
    const before = JSON.stringify(items);
    const api = fakeApi([active, absent], [...old, archived], items);
    const settings = config();
    const state = await planRepositoryItems(api, settings, PROJECT, NOW);
    expect(state.plan.counts).toEqual({ ADD: 0, KEEP_ACTIVE: 1, ARCHIVE: 0, UNARCHIVE: 0, KEEP_ARCHIVED: 31, SKIP: 1 });
    expect(state.plan.baselines).toEqual({ expectedPreV2MergedPullRequests: 30, observedPreV2MergedPullRequests: 30 });
    const fields = new Map(settings.fields.map((field: any) => [field.name, field]));
    const logs: unknown[] = [];
    const summary = await reconcileItems(api, settings, PROJECT, fields, state, { log: (entry: unknown) => logs.push(entry) });
    expect(summary.executed).toEqual({ added: 0, archived: 0, unarchived: 0, fieldUpdates: 0, fieldClears: 0 });
    expect(summary.verified).toEqual({ active: 1, archived: 31, absent: 1 });
    expect(summary.contentIntake).toEqual({ accepted: 30, excluded: 3 });
    expect(JSON.stringify(items)).toBe(before);
    expect(JSON.stringify({ logs, summary })).not.toContain('broken');
    expect(api.request).toHaveBeenCalledTimes(3);
    // The final audit still detects drift to an excluded archived item's fields.
    const changed = structuredClone(items);
    changed.at(-1)!.fieldValues.nodes[0].name = 'Live Proven';
    const projection = evaluateFinalItemProjection({
      items: changed, config: settings, beforeValuesByContentId: new Map(), expectedValuesByContentId: new Map(),
      fieldActionsByContentId: new Map(), itemIdByContentId: new Map(items.map((entry) => [entry.content.id, entry.id])),
      plan: state.plan,
    });
    expect(projection).toMatchObject({ outcome: 'HARD_DRIFT', reason: 'UNEXPECTED_FIELD_VALUE' });
  });
});
