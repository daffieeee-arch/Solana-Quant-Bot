import { describe, expect, it, vi } from 'vitest';
import {
  GITHUB_QUERY_RETRY_DELAYS_MS,
  GITHUB_RETRY_AFTER_MAX_MS,
  GitHubGraphQL,
  itemListWithAddedItems,
  retryAfterMilliseconds,
  PROJECT_ITEM_VISIBILITY_DELAYS_MS,
  PROJECT_VERIFICATION_DELAYS_MS,
  evaluateItemLifecycleProjection,
  reconcileItems,
  verifyProjectProjection,
} from '../scripts/github-projects/sync.mjs';

const REPOSITORY = 'daffieeee-arch/Solana-Quant-Bot';
const TOKEN = 'ghp_secret_token_value';
const QUERY = '\n  query RepositoryPullRequests($owner: String!) { repository(owner: $owner) { id } }';
const MUTATION = '\n  mutation AddRoadmapItem($input: AddProjectV2ItemByIdInput!) { addProjectV2ItemById(input: $input) { item { id } } }';

const reply = (status: number, body: string) => ({ status, ok: status >= 200 && status < 300, text: async () => body });
const json = (status: number, value: unknown) => reply(status, JSON.stringify(value));
const withRetryAfter = (status: number, value: string) => ({
  ...json(status, { message: 'slow down' }),
  headers: { get: (name: string) => (name.toLowerCase() === 'retry-after' ? value : null) },
});

const client = (responses: Array<unknown>, random = () => 0) => {
  const sleeps: number[] = [];
  const logs: Array<Record<string, unknown>> = [];
  const fetchImpl = vi.fn(async () => {
    const next = responses.shift();
    if (next instanceof Error) throw next;
    if (!next) throw new Error('unexpected extra request');
    return next;
  });
  const api = new GitHubGraphQL(TOKEN, {
    fetchImpl,
    sleep: async (milliseconds: number) => { sleeps.push(milliseconds); },
    random,
    log: (entry: Record<string, unknown>) => { logs.push(entry); },
  });
  return { api, fetchImpl, sleeps, logs };
};

describe('Roadmap Sync GitHub GraphQL retries for read-only queries', () => {
  it('repeats a list query after a 504 gateway page (non-JSON) and returns the data', async () => {
    const { api, fetchImpl, sleeps, logs } = client([
      reply(504, '<html>504 Gateway Time-out</html>'),
      reply(504, '<html>504 Gateway Time-out</html>'),
      json(200, { data: { repository: { id: 'R' } } }),
    ]);
    await expect(api.request(QUERY, { owner: 'o' }, 'list repository pull requests')).resolves.toEqual({ repository: { id: 'R' } });
    expect(fetchImpl).toHaveBeenCalledTimes(3);
    expect(sleeps).toEqual([2_000, 4_000]);
    expect(logs.map((entry) => entry.reason)).toEqual(['NON_JSON_HTTP_504', 'NON_JSON_HTTP_504']);
    expect(logs[0]).toMatchObject({ event: 'github_request_retry', label: 'list repository pull requests', attempt: 1 });
  });

  it('repeats HTTP 5xx with a JSON body, 429 and network errors; jitter adds up to 25%', async () => {
    const { api, sleeps, logs } = client([
      json(502, { message: 'Bad Gateway' }),
      json(429, { message: 'rate limited' }),
      new TypeError('fetch failed'),
      json(200, { data: { ok: true } }),
    ], () => 1);
    await expect(api.request(QUERY, {}, 'list repository issues')).resolves.toEqual({ ok: true });
    expect(sleeps).toEqual([2_500, 5_000, 10_000]);
    expect(logs.map((entry) => entry.reason)).toEqual(['HTTP_502', 'HTTP_429', 'NETWORK']);
    expect(JSON.stringify(logs)).not.toContain(TOKEN);
  });

  it('gives up after the bounded schedule with the last failure and the attempt count', async () => {
    const { api, fetchImpl, sleeps } = client(Array.from({ length: 6 }, () => json(503, { message: 'unavailable' })));
    await expect(api.request(QUERY, {}, 'list repository issues'))
      .rejects.toThrow(/list repository issues failed \(HTTP 503\).* after 6 attempts/);
    expect(fetchImpl).toHaveBeenCalledTimes(1 + GITHUB_QUERY_RETRY_DELAYS_MS.length);
    expect(sleeps).toEqual([...GITHUB_QUERY_RETRY_DELAYS_MS]);
    expect(sleeps.reduce((total, delay) => total + delay, 0)).toBe(60_000);
  });

  it('never repeats a mutation, even on a transient failure', async () => {
    const { api, fetchImpl, sleeps } = client([reply(502, '<html>bad gateway</html>')]);
    await expect(api.request(MUTATION, {}, 'add Issue #5')).rejects.toThrow('add Issue #5 returned non-JSON HTTP 502');
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(sleeps).toEqual([]);
  });

  it('does not repeat client errors or GraphQL errors with HTTP 200', async () => {
    const unauthorized = client([json(401, { message: 'Bad credentials' })]);
    await expect(unauthorized.api.request(QUERY, {}, 'list repository issues')).rejects.toThrow('failed (HTTP 401)');
    expect(unauthorized.fetchImpl).toHaveBeenCalledTimes(1);
    const graphql = client([json(200, { errors: [{ message: 'Field x does not exist' }] })]);
    await expect(graphql.api.request(QUERY, {}, 'list repository issues')).rejects.toThrow('Field x does not exist');
    expect(graphql.fetchImpl).toHaveBeenCalledTimes(1);
  });

  it('sends the token only in the authorization header and never in an error', async () => {
    const { api, fetchImpl } = client(Array.from({ length: 6 }, () => new TypeError(`connect failed ${TOKEN}`)));
    const error = await api.request(QUERY, {}, 'list repository issues').catch((e: Error) => e);
    expect(String(error)).not.toContain(TOKEN);
    const [, init] = fetchImpl.mock.calls[0] as unknown as [string, { headers: Record<string, string> }];
    expect(init.headers.authorization).toBe(`Bearer ${TOKEN}`);
  });
});

const issue = (number: number) => ({
  id: `ISSUE_${number}`,
  kind: 'Issue',
  number,
  title: `Issue ${number}`,
  body: '',
  author: { login: 'daffieeee-arch' },
  state: 'OPEN',
});

const projectItem = (content: ReturnType<typeof issue>, overrides: Record<string, unknown> = {}) => ({
  id: `ITEM_${content.number}`,
  isArchived: false,
  updatedAt: '2026-10-12T00:00:00.000Z',
  content: {
    id: content.id,
    __typename: content.kind,
    number: content.number,
    url: `https://github.test/issues/${content.number}`,
    repository: { nameWithOwner: REPOSITORY },
  },
  fieldValues: { nodes: [], pageInfo: { hasNextPage: false, endCursor: null } },
  ...overrides,
});

const itemsResponse = (nodes: unknown[]) => ({
  node: { items: { nodes, pageInfo: { hasNextPage: false, endCursor: null } } },
});

const verification = () => {
  const sleeps: number[] = [];
  const logs: Array<Record<string, unknown>> = [];
  return {
    sleeps,
    logs,
    options: {
      sleep: async (milliseconds: number) => { sleeps.push(milliseconds); },
      log: (entry: Record<string, unknown>) => { logs.push(entry); },
    },
  };
};

const addPlan = (content: ReturnType<typeof issue>) => ({
  issues: [content],
  pullRequests: [],
  projectItems: [],
  repositoryItems: [],
  metadataByContentId: new Map([[content.id, {}]]),
  plan: {
    operations: [{ content, item: undefined, action: 'ADD', reason: 'add', eligible: true }],
    counts: { ADD: 1, KEEP_ACTIVE: 0, ARCHIVE: 0, UNARCHIVE: 0, KEEP_ARCHIVED: 0, SKIP: 0 },
    reasonCounts: { add: 1 },
    baselines: { expectedPreV2MergedPullRequests: 0, observedPreV2MergedPullRequests: 0 },
  },
});

describe('Roadmap Sync item visibility after ADD_ITEM', () => {
  it('has a ~3 minute exponential window for item visibility that starts like the standard one', () => {
    expect(PROJECT_ITEM_VISIBILITY_DELAYS_MS.slice(0, PROJECT_VERIFICATION_DELAYS_MS.length)).toEqual([...PROJECT_VERIFICATION_DELAYS_MS]);
    const total = PROJECT_ITEM_VISIBILITY_DELAYS_MS.reduce((sum, delay) => sum + delay, 0);
    expect(total).toBeGreaterThanOrEqual(170_000);
    expect(total).toBeLessThanOrEqual(200_000);
    for (let i = 2; i < PROJECT_ITEM_VISIBILITY_DELAYS_MS.length; i += 1) {
      expect(PROJECT_ITEM_VISIBILITY_DELAYS_MS[i]).toBeGreaterThanOrEqual(PROJECT_ITEM_VISIBILITY_DELAYS_MS[i - 1]);
    }
  });

  it('converges when the item list shows a new item only after the standard window', async () => {
    const target = issue(93);
    const verifier = verification();
    let reads = 0;
    const result = await verifyProjectProjection({
      operationType: 'ADD_ITEM',
      identity: 'Issue #93',
      delays: PROJECT_ITEM_VISIBILITY_DELAYS_MS,
      read: async () => { reads += 1; return reads < 8 ? [] : [projectItem(target)]; },
      classify: (items: unknown[]) => evaluateItemLifecycleProjection({
        items,
        targetItemId: 'ITEM_93',
        content: target,
        repository: REPOSITORY,
        expectedArchived: false,
        beforeArchived: undefined,
        allowMissing: true,
      }),
      ...verifier.options,
    });
    expect(result.attempts).toBe(8);
    expect(verifier.sleeps).toEqual(PROJECT_ITEM_VISIBILITY_DELAYS_MS.slice(1, 8));
  });

  it('finds a new item through a direct read when the item list keeps lagging, without repeating the add', async () => {
    const target = issue(95);
    const labels: string[] = [];
    let listReads = 0;
    const api = {
      request: vi.fn(async (_query: string, variables: Record<string, unknown>, label: string) => {
        labels.push(label);
        if (label === 'add Issue #95') return { addProjectV2ItemById: { item: { id: 'ITEM_95' } } };
        if (label === 'list project items') {
          listReads += 1;
          return itemsResponse([]);
        }
        if (label === 'read project item') {
          expect(variables).toEqual({ itemId: 'ITEM_95' });
          return { node: { project: { id: 'PROJECT_4' }, ...projectItem(target, { id: 'ITEM_95' }) } };
        }
        throw new Error(`unexpected GraphQL operation ${label}`);
      }),
    };
    const verifier = verification();
    const summary = await reconcileItems(api, { repository: REPOSITORY, fields: [] }, { id: 'PROJECT_4', number: 4 },
      new Map(), addPlan(target), verifier.options);

    expect(labels.filter((label) => label === 'add Issue #95')).toHaveLength(1);
    // The standard window reads only the list; the direct read joins from the next read on.
    expect(labels.indexOf('read project item')).toBe(1 + PROJECT_VERIFICATION_DELAYS_MS.length + 1);
    expect(listReads).toBe(PROJECT_VERIFICATION_DELAYS_MS.length + 1);
    expect(summary.executed).toEqual({ added: 1, archived: 0, unarchived: 0, fieldUpdates: 0, fieldClears: 0 });
    expect(summary.verified).toEqual({ active: 1, archived: 0, absent: 0 });
  });

  it('ignores a direct read of an item in another Project and fails closed after the long window', async () => {
    const target = issue(96);
    const api = {
      request: vi.fn(async (_query: string, _variables: unknown, label: string) => {
        if (label === 'add Issue #96') return { addProjectV2ItemById: { item: { id: 'ITEM_96' } } };
        if (label === 'list project items') return itemsResponse([]);
        if (label === 'read project item') return { node: { project: { id: 'PROJECT_OTHER' }, ...projectItem(target, { id: 'ITEM_96' }) } };
        throw new Error(`unexpected GraphQL operation ${label}`);
      }),
    };
    const verifier = verification();
    await expect(reconcileItems(api, { repository: REPOSITORY, fields: [] }, { id: 'PROJECT_4', number: 4 },
      new Map(), addPlan(target), verifier.options))
      .rejects.toThrow(new RegExp(`PROJECT_VERIFICATION_EXHAUSTED operation=ADD_ITEM .*attempts=${PROJECT_ITEM_VISIBILITY_DELAYS_MS.length} reason=ITEM_NOT_VISIBLE`));
    expect(api.request.mock.calls.filter((call) => call[2] === 'add Issue #96')).toHaveLength(1);
    expect(verifier.sleeps).toEqual(PROJECT_ITEM_VISIBILITY_DELAYS_MS.slice(1));
  });
});


describe('Roadmap Sync retry-after and the final audit read schedule', () => {
  it('reads retry-after as seconds or an HTTP date', () => {
    const now = Date.parse('2026-10-12T18:00:00Z');
    expect(retryAfterMilliseconds('45', now)).toBe(45_000);
    expect(retryAfterMilliseconds(' 0 ', now)).toBe(0);
    expect(retryAfterMilliseconds('Mon, 12 Oct 2026 18:00:30 GMT', now)).toBe(30_000);
    expect(retryAfterMilliseconds('Mon, 12 Oct 2026 17:59:00 GMT', now)).toBe(0);
    expect(retryAfterMilliseconds('soon', now)).toBeUndefined();
    expect(retryAfterMilliseconds(undefined, now)).toBeUndefined();
  });

  it('waits as long as retry-after asks when that is longer than the backoff, at most 60 s', async () => {
    const { api, sleeps, logs } = client([
      withRetryAfter(429, '45'),
      withRetryAfter(503, '300'),
      withRetryAfter(429, '1'),
      json(200, { data: { ok: true } }),
    ]);
    await expect(api.request(QUERY, {}, 'list repository issues')).resolves.toEqual({ ok: true });
    expect(sleeps).toEqual([45_000, GITHUB_RETRY_AFTER_MAX_MS, 8_000]);
    expect(logs.map((entry) => entry.waitMilliseconds)).toEqual(sleeps);
  });

  it('the direct item read joins at the given read of the list', async () => {
    const labels: string[] = [];
    const api = {
      request: vi.fn(async (_query: string, _variables: unknown, label: string) => {
        labels.push(label);
        if (label === 'list project items') return itemsResponse([]);
        return { node: null };
      }),
    };
    // The final audit's reader: its first call is the audit's second read.
    const read = itemListWithAddedItems(api, 'PROJECT_4', ['ITEM_9'], PROJECT_VERIFICATION_DELAYS_MS.length);
    for (let i = 0; i < PROJECT_VERIFICATION_DELAYS_MS.length; i += 1) await read();
    expect(labels.filter((label) => label === 'read project item')).toHaveLength(1);
    expect(labels.at(-1)).toBe('read project item');
  });
});
