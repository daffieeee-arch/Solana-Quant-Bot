#!/usr/bin/env node

import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const GRAPHQL_URL = 'https://api.github.com/graphql';
const API_VERSION = '2022-11-28';
const DEFAULT_CONFIG_PATH = 'roadmap/project-config.json';
const MAX_CONFIG_BYTES = 256 * 1024;
const MAX_METADATA_BYTES = 16 * 1024;
const MAX_CONTENT_ITEMS = 5_000;
const MAX_FIELDS = 50;
const MAX_VIEWS = 25;
const ALLOWED_METADATA_KEYS = new Set([
  'schemaVersion', 'type', 'area', 'priority', 'phase', 'risk', 'evidence', 'workflow',
  'effort', 'startDate', 'targetDate',
]);
const ALLOWED_FIELD_TYPES = new Set(['SINGLE_SELECT', 'NUMBER', 'DATE']);
const ALLOWED_VIEW_LAYOUTS = new Set(['TABLE', 'BOARD', 'ROADMAP']);
const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function boundedString(value, name, maximum = 256) {
  assert(typeof value === 'string' && value.trim().length > 0, `${name} must be a non-empty string`);
  assert(Buffer.byteLength(value, 'utf8') <= maximum, `${name} exceeds ${maximum} bytes`);
  return value.trim();
}

function canonicalDate(value, name) {
  if (value === undefined) return undefined;
  assert(typeof value === 'string' && DATE_PATTERN.test(value), `${name} must use YYYY-MM-DD`);
  const timestamp = Date.parse(`${value}T00:00:00.000Z`);
  assert(Number.isFinite(timestamp) && new Date(timestamp).toISOString().startsWith(value), `${name} is not a valid date`);
  return value;
}

export function parseRoadmapMeta(body) {
  const text = typeof body === 'string' ? body : '';
  const matches = [...text.matchAll(/<!--\s*roadmap-meta\s*([\s\S]*?)-->/gi)];
  if (matches.length === 0) return undefined;
  assert(matches.length === 1, 'roadmap-meta must appear at most once');
  const raw = matches[0][1].trim();
  assert(Buffer.byteLength(raw, 'utf8') <= MAX_METADATA_BYTES, 'roadmap-meta exceeds its byte limit');
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (error) {
    throw new Error(`roadmap-meta is invalid JSON: ${error instanceof Error ? error.message : String(error)}`);
  }
  assert(isObject(parsed), 'roadmap-meta must be a JSON object');
  for (const key of Object.keys(parsed)) assert(ALLOWED_METADATA_KEYS.has(key), `roadmap-meta contains unknown key ${key}`);
  assert(parsed.schemaVersion === 1, 'roadmap-meta schemaVersion must equal 1');

  const result = { schemaVersion: 1 };
  for (const key of ['type', 'area', 'priority', 'phase', 'risk', 'evidence', 'workflow']) {
    if (parsed[key] !== undefined) result[key] = boundedString(parsed[key], `roadmap-meta.${key}`, 128);
  }
  if (parsed.effort !== undefined) {
    assert(Number.isInteger(parsed.effort) && parsed.effort >= 0 && parsed.effort <= 100,
      'roadmap-meta.effort must be an integer between 0 and 100');
    result.effort = parsed.effort;
  }
  const startDate = canonicalDate(parsed.startDate, 'roadmap-meta.startDate');
  const targetDate = canonicalDate(parsed.targetDate, 'roadmap-meta.targetDate');
  if (startDate !== undefined) result.startDate = startDate;
  if (targetDate !== undefined) result.targetDate = targetDate;
  return Object.freeze(result);
}

export function linkedIssueNumbers(body, title = '') {
  const numbers = new Set();
  const inspect = `${title}\n${typeof body === 'string' ? body : ''}`;
  for (const line of inspect.split(/\r?\n/)) {
    if (!/\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?|roadmap|implements?|tracks?)\b/i.test(line)
      && !/^\s*\[?#\d+\]?/i.test(line)) continue;
    for (const match of line.matchAll(/#(\d{1,10})\b/g)) numbers.add(Number(match[1]));
    for (const match of line.matchAll(/\/issues\/(\d{1,10})\b/g)) numbers.add(Number(match[1]));
  }
  return [...numbers].filter((number) => Number.isSafeInteger(number) && number > 0).sort((a, b) => a - b);
}

function inferFromTitle(title) {
  const result = {};
  const standard = /^\[(P[0-3])\]\[([^\]]+)\]/.exec(title ?? '');
  if (standard) {
    result.priority = standard[1];
    result.area = standard[2];
  }
  const epic = /^\[Epic\s+(P[0-3])\]/i.exec(title ?? '');
  if (epic) {
    result.type = 'Epic';
    result.priority = epic[1].toUpperCase();
  }
  if (/^\[Program\]/i.test(title ?? '')) result.type = 'Program';
  return result;
}

export function deriveStatus(content, metadata = {}) {
  if (content.kind === 'PullRequest') {
    if (content.merged === true || content.state === 'MERGED') return 'Done';
    if (content.state === 'CLOSED') return 'Cancelled';
    if (content.isDraft === true) return 'In Progress';
    return 'In Review';
  }
  if (content.state === 'CLOSED') {
    return String(content.stateReason ?? '').toUpperCase() === 'NOT_PLANNED' ? 'Cancelled' : 'Done';
  }
  return metadata.workflow ?? 'Backlog';
}

export function deriveMetadata(content, issuesByNumber = new Map()) {
  const direct = parseRoadmapMeta(content.body) ?? { schemaVersion: 1 };
  const inferred = inferFromTitle(content.title);
  let inherited = {};
  if (content.kind === 'PullRequest') {
    for (const issueNumber of linkedIssueNumbers(content.body, content.title)) {
      const issue = issuesByNumber.get(issueNumber);
      if (!issue) continue;
      inherited = { ...inferFromTitle(issue.title), ...(parseRoadmapMeta(issue.body) ?? {}) };
      break;
    }
  }
  const metadata = { ...inferred, ...inherited, ...direct };
  metadata.workflow = deriveStatus(content, metadata);
  if (content.kind === 'PullRequest') metadata.type = 'Pull Request';
  return metadata;
}

export function validateProjectConfig(value) {
  assert(isObject(value), 'project config must be an object');
  assert(value.schemaVersion === 1, 'project config schemaVersion must equal 1');
  const owner = boundedString(value.owner, 'owner', 100);
  const repository = boundedString(value.repository, 'repository', 200);
  assert(/^[-A-Za-z0-9_.]+\/[-A-Za-z0-9_.]+$/.test(repository), 'repository must use owner/name');
  assert(repository.split('/')[0].toLowerCase() === owner.toLowerCase(), 'repository owner must match project owner');
  assert(isObject(value.project), 'project must be an object');
  boundedString(value.project.title, 'project.title', 256);
  boundedString(value.project.shortDescription, 'project.shortDescription', 512);
  boundedString(value.project.readme, 'project.readme', 16_384);

  assert(Array.isArray(value.fields) && value.fields.length > 0 && value.fields.length <= MAX_FIELDS,
    `fields must contain 1..${MAX_FIELDS} entries`);
  const fieldNames = new Set();
  for (const field of value.fields) {
    assert(isObject(field), 'every field must be an object');
    const name = boundedString(field.name, 'field.name', 128);
    assert(!fieldNames.has(name), `duplicate project field ${name}`);
    fieldNames.add(name);
    assert(ALLOWED_FIELD_TYPES.has(field.dataType), `unsupported data type for ${name}`);
    if (field.dataType === 'SINGLE_SELECT') {
      assert(Array.isArray(field.options) && field.options.length > 0 && field.options.length <= 50,
        `${name} requires 1..50 options`);
      const optionNames = new Set();
      for (const option of field.options) {
        assert(isObject(option), `${name} option must be an object`);
        const optionName = boundedString(option.name, `${name}.option.name`, 128);
        assert(!optionNames.has(optionName), `duplicate ${name} option ${optionName}`);
        optionNames.add(optionName);
        boundedString(option.color, `${name}.${optionName}.color`, 32);
        boundedString(option.description, `${name}.${optionName}.description`, 256);
        if (option.aliases !== undefined) {
          assert(Array.isArray(option.aliases) && option.aliases.every((alias) => typeof alias === 'string'),
            `${name}.${optionName}.aliases must be strings`);
        }
      }
    } else {
      assert(field.options === undefined, `${name} may not define options`);
    }
  }
  assert(fieldNames.has('Status'), 'project config must define Status');

  assert(Array.isArray(value.views) && value.views.length > 0 && value.views.length <= MAX_VIEWS,
    `views must contain 1..${MAX_VIEWS} entries`);
  const viewNames = new Set();
  for (const view of value.views) {
    assert(isObject(view), 'every view must be an object');
    const name = boundedString(view.name, 'view.name', 128);
    assert(!viewNames.has(name), `duplicate project view ${name}`);
    viewNames.add(name);
    assert(ALLOWED_VIEW_LAYOUTS.has(view.layout), `unsupported layout for ${name}`);
    if (view.filter !== undefined) boundedString(view.filter, `${name}.filter`, 512);
    if (view.visibleFields !== undefined) {
      assert(Array.isArray(view.visibleFields) && view.visibleFields.every((fieldName) => typeof fieldName === 'string'),
        `${name}.visibleFields must be strings`);
    }
  }
  return value;
}

function valuesEqual(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function selectInputOptions(field, existingOptions = []) {
  const claimed = new Set();
  return field.options.map((desired) => {
    const names = [desired.name, ...(desired.aliases ?? [])].map((name) => name.toLowerCase());
    const existing = existingOptions.find((candidate) => !claimed.has(candidate.id)
      && names.includes(String(candidate.name).toLowerCase()));
    if (existing) claimed.add(existing.id);
    return {
      ...(existing?.id ? { id: existing.id } : {}),
      name: desired.name,
      color: desired.color,
      description: desired.description,
    };
  });
}

function currentValueMap(fieldValues) {
  const map = new Map();
  for (const node of fieldValues ?? []) {
    const name = node?.field?.name;
    if (!name) continue;
    if (node.__typename === 'ProjectV2ItemFieldSingleSelectValue') {
      map.set(name, { kind: 'SINGLE_SELECT', optionId: node.optionId ?? null, name: node.name ?? null });
    } else if (node.__typename === 'ProjectV2ItemFieldNumberValue') {
      map.set(name, { kind: 'NUMBER', number: node.number ?? null });
    } else if (node.__typename === 'ProjectV2ItemFieldDateValue') {
      map.set(name, { kind: 'DATE', date: node.date ?? null });
    }
  }
  return map;
}

class GitHubGraphQL {
  constructor(token) {
    this.token = token;
  }

  async request(query, variables = {}, label = 'GitHub GraphQL request') {
    const response = await fetch(GRAPHQL_URL, {
      method: 'POST',
      headers: {
        accept: 'application/vnd.github+json',
        authorization: `Bearer ${this.token}`,
        'content-type': 'application/json',
        'x-github-api-version': API_VERSION,
        'user-agent': 'solana-paper-scanner-roadmap-sync',
      },
      body: JSON.stringify({ query, variables }),
      signal: AbortSignal.timeout(30_000),
    });
    const text = await response.text();
    let payload;
    try {
      payload = JSON.parse(text);
    } catch {
      throw new Error(`${label} returned non-JSON HTTP ${response.status}`);
    }
    if (!response.ok || payload.errors?.length) {
      const messages = (payload.errors ?? []).map((error) => error.message).join('; ');
      throw new Error(`${label} failed (HTTP ${response.status}): ${messages || 'unknown error'}`);
    }
    return payload.data;
  }
}

async function loadConfig(path) {
  const absolute = resolve(path);
  const bytes = await readFile(absolute);
  assert(bytes.length <= MAX_CONFIG_BYTES, 'project config exceeds its byte limit');
  let parsed;
  try {
    parsed = JSON.parse(bytes.toString('utf8'));
  } catch (error) {
    throw new Error(`project config is invalid JSON: ${error instanceof Error ? error.message : String(error)}`);
  }
  return validateProjectConfig(parsed);
}

async function bootstrapContext(api, config) {
  const [repoOwner, repoName] = config.repository.split('/');
  const data = await api.request(`
    query RoadmapBootstrap($owner: String!, $repoOwner: String!, $repoName: String!, $projectQuery: String!) {
      viewer { id login }
      user(login: $owner) {
        id
        login
        projectsV2(first: 100, query: $projectQuery) {
          nodes {
            id
            number
            title
            closed
            shortDescription
            repositories(first: 100) { nodes { id nameWithOwner } }
          }
        }
      }
      repository(owner: $repoOwner, name: $repoName) { id nameWithOwner }
    }
  `, { owner: config.owner, repoOwner, repoName, projectQuery: config.project.title }, 'bootstrap query');
  assert(data.viewer?.login?.toLowerCase() === config.owner.toLowerCase(),
    `PROJECT_TOKEN must belong to ${config.owner}; authenticated as ${data.viewer?.login ?? 'unknown'}`);
  assert(data.user?.id, `project owner ${config.owner} was not found`);
  assert(data.repository?.id, `repository ${config.repository} was not found or is inaccessible`);
  const exact = (data.user.projectsV2.nodes ?? []).filter((project) => project.title === config.project.title);
  assert(exact.length <= 1, `multiple projects have the exact title ${config.project.title}`);
  return { owner: data.user, repository: data.repository, project: exact[0] };
}

async function ensureProject(api, config, context) {
  let project = context.project;
  if (!project) {
    const data = await api.request(`
      mutation CreateRoadmapProject($input: CreateProjectV2Input!) {
        createProjectV2(input: $input) { projectV2 { id number title closed } }
      }
    `, {
      input: {
        ownerId: context.owner.id,
        repositoryId: context.repository.id,
        title: config.project.title,
      },
    }, 'create project');
    project = data.createProjectV2.projectV2;
    console.log(JSON.stringify({ event: 'project_created', number: project.number, title: project.title }));
  }

  await api.request(`
    mutation UpdateRoadmapProject($input: UpdateProjectV2Input!) {
      updateProjectV2(input: $input) { projectV2 { id number title closed } }
    }
  `, {
    input: {
      projectId: project.id,
      title: config.project.title,
      shortDescription: config.project.shortDescription,
      readme: config.project.readme,
      closed: false,
      public: false,
    },
  }, 'update project');

  const linked = context.project?.repositories?.nodes?.some((repo) => repo.id === context.repository.id) ?? true;
  if (!linked) {
    await api.request(`
      mutation LinkRoadmapProject($input: LinkProjectV2ToRepositoryInput!) {
        linkProjectV2ToRepository(input: $input) { repository { id nameWithOwner } }
      }
    `, { input: { projectId: project.id, repositoryId: context.repository.id } }, 'link project to repository');
  }
  return project;
}

async function listProjectFields(api, projectId) {
  const fields = [];
  let after = null;
  do {
    const data = await api.request(`
      query ProjectFields($projectId: ID!, $after: String) {
        node(id: $projectId) {
          ... on ProjectV2 {
            fields(first: 100, after: $after) {
              nodes {
                __typename
                ... on ProjectV2Field { id name dataType }
                ... on ProjectV2SingleSelectField { id name dataType options { id name color description } }
                ... on ProjectV2IterationField { id name dataType }
              }
              pageInfo { hasNextPage endCursor }
            }
          }
        }
      }
    `, { projectId, after }, 'list project fields');
    const connection = data.node?.fields;
    assert(connection, 'project fields were unavailable');
    fields.push(...(connection.nodes ?? []));
    after = connection.pageInfo.hasNextPage ? connection.pageInfo.endCursor : null;
  } while (after);
  return fields;
}

async function ensureFields(api, config, projectId) {
  let existing = await listProjectFields(api, projectId);
  for (const desired of config.fields) {
    const field = existing.find((candidate) => candidate.name === desired.name);
    if (!field) {
      assert(!desired.builtin, `built-in field ${desired.name} is missing`);
      const input = {
        projectId,
        name: desired.name,
        dataType: desired.dataType,
        ...(desired.dataType === 'SINGLE_SELECT'
          ? { singleSelectOptions: selectInputOptions(desired) }
          : {}),
      };
      await api.request(`
        mutation CreateRoadmapField($input: CreateProjectV2FieldInput!) {
          createProjectV2Field(input: $input) { projectV2Field { ... on ProjectV2FieldCommon { id name dataType } } }
        }
      `, { input }, `create field ${desired.name}`);
      console.log(JSON.stringify({ event: 'field_created', name: desired.name }));
      existing = await listProjectFields(api, projectId);
      continue;
    }
    assert(field.dataType === desired.dataType,
      `field ${desired.name} has data type ${field.dataType}, expected ${desired.dataType}`);
    if (desired.dataType === 'SINGLE_SELECT') {
      const options = selectInputOptions(desired, field.options ?? []);
      const currentShape = (field.options ?? []).map(({ id, name, color, description }) => ({ id, name, color, description }));
      const desiredShape = options.map(({ id, name, color, description }) => ({ id: id ?? null, name, color, description }));
      const normalizedCurrent = currentShape.map((option) => ({ ...option, id: option.id ?? null }));
      if (!valuesEqual(normalizedCurrent, desiredShape)) {
        await api.request(`
          mutation UpdateRoadmapField($input: UpdateProjectV2FieldInput!) {
            updateProjectV2Field(input: $input) { projectV2Field { ... on ProjectV2FieldCommon { id name dataType } } }
          }
        `, { input: { fieldId: field.id, singleSelectOptions: options } }, `update field ${desired.name}`);
        console.log(JSON.stringify({ event: 'field_options_reconciled', name: desired.name }));
        existing = await listProjectFields(api, projectId);
      }
    }
  }
  return new Map(existing.map((field) => [field.name, field]));
}

async function listProjectViews(api, projectId) {
  const views = [];
  let after = null;
  do {
    const data = await api.request(`
      query ProjectViews($projectId: ID!, $after: String) {
        node(id: $projectId) {
          ... on ProjectV2 {
            views(first: 100, after: $after) {
              nodes {
                id
                name
                layout
                filter
                configuration {
                  visibleFields(first: 100) {
                    nodes { ... on ProjectV2FieldCommon { id name } }
                  }
                }
              }
              pageInfo { hasNextPage endCursor }
            }
          }
        }
      }
    `, { projectId, after }, 'list project views');
    const connection = data.node?.views;
    assert(connection, 'project views were unavailable');
    views.push(...(connection.nodes ?? []));
    after = connection.pageInfo.hasNextPage ? connection.pageInfo.endCursor : null;
  } while (after);
  return views;
}

async function ensureViews(api, config, projectId, fieldsByName) {
  let existing = await listProjectViews(api, projectId);
  let reusableDefault = existing.find((view) => /^View\s+\d+$/i.test(view.name));
  for (const desired of config.views) {
    const visibleFieldIds = (desired.visibleFields ?? [])
      .map((name) => fieldsByName.get(name)?.id)
      .filter(Boolean);
    let view = existing.find((candidate) => candidate.name === desired.name);
    if (!view && reusableDefault) {
      view = reusableDefault;
      reusableDefault = undefined;
    }
    if (!view) {
      const input = {
        projectId,
        name: desired.name,
        layout: desired.layout,
        ...(desired.layout !== 'ROADMAP' && visibleFieldIds.length > 0
          ? { configuration: { visibleFieldIds } }
          : {}),
      };
      const data = await api.request(`
        mutation CreateRoadmapView($input: CreateProjectV2ViewInput!) {
          createProjectV2View(input: $input) { projectV2View { id name layout filter } }
        }
      `, { input }, `create view ${desired.name}`);
      view = data.createProjectV2View.projectV2View;
      console.log(JSON.stringify({ event: 'view_created', name: desired.name, layout: desired.layout }));
    }
    const update = {
      viewId: view.id,
      name: desired.name,
      layout: desired.layout,
      ...(desired.filter ? { filter: desired.filter } : {}),
      ...(desired.layout !== 'ROADMAP' && visibleFieldIds.length > 0
        ? { configuration: { visibleFieldIds } }
        : {}),
    };
    try {
      await api.request(`
        mutation UpdateRoadmapView($input: UpdateProjectV2ViewInput!) {
          updateProjectV2View(input: $input) { projectV2View { id name layout filter } }
        }
      `, { input: update }, `update view ${desired.name}`);
    } catch (error) {
      // A filter grammar change must not block issue/PR synchronization. The view
      // remains present and the warning is visible in the workflow log.
      console.warn(JSON.stringify({
        event: 'view_update_warning',
        name: desired.name,
        message: error instanceof Error ? error.message.slice(0, 240) : String(error).slice(0, 240),
      }));
    }
    existing = await listProjectViews(api, projectId);
  }
}

async function listRepositoryIssues(api, owner, name) {
  const items = [];
  let after = null;
  do {
    const data = await api.request(`
      query RepositoryIssues($owner: String!, $name: String!, $after: String) {
        repository(owner: $owner, name: $name) {
          issues(first: 100, after: $after, states: [OPEN, CLOSED], orderBy: { field: UPDATED_AT, direction: DESC }) {
            nodes { id number title body url state stateReason updatedAt closedAt }
            pageInfo { hasNextPage endCursor }
          }
        }
      }
    `, { owner, name, after }, 'list repository issues');
    const connection = data.repository?.issues;
    assert(connection, 'repository issues were unavailable');
    items.push(...(connection.nodes ?? []).map((item) => ({ ...item, kind: 'Issue' })));
    assert(items.length <= MAX_CONTENT_ITEMS, `repository issue count exceeds ${MAX_CONTENT_ITEMS}`);
    after = connection.pageInfo.hasNextPage ? connection.pageInfo.endCursor : null;
  } while (after);
  return items;
}

async function listRepositoryPullRequests(api, owner, name) {
  const items = [];
  let after = null;
  do {
    const data = await api.request(`
      query RepositoryPullRequests($owner: String!, $name: String!, $after: String) {
        repository(owner: $owner, name: $name) {
          pullRequests(first: 100, after: $after, states: [OPEN, CLOSED, MERGED], orderBy: { field: UPDATED_AT, direction: DESC }) {
            nodes { id number title body url state isDraft merged mergedAt updatedAt closedAt }
            pageInfo { hasNextPage endCursor }
          }
        }
      }
    `, { owner, name, after }, 'list repository pull requests');
    const connection = data.repository?.pullRequests;
    assert(connection, 'repository pull requests were unavailable');
    items.push(...(connection.nodes ?? []).map((item) => ({ ...item, kind: 'PullRequest' })));
    assert(items.length <= MAX_CONTENT_ITEMS, `repository pull request count exceeds ${MAX_CONTENT_ITEMS}`);
    after = connection.pageInfo.hasNextPage ? connection.pageInfo.endCursor : null;
  } while (after);
  return items;
}

async function listProjectItems(api, projectId) {
  const items = [];
  let after = null;
  do {
    const data = await api.request(`
      query ProjectItems($projectId: ID!, $after: String) {
        node(id: $projectId) {
          ... on ProjectV2 {
            items(first: 100, after: $after) {
              nodes {
                id
                isArchived
                content {
                  __typename
                  ... on Issue { id number url repository { nameWithOwner } }
                  ... on PullRequest { id number url repository { nameWithOwner } }
                }
                fieldValues(first: 100) {
                  nodes {
                    __typename
                    ... on ProjectV2ItemFieldSingleSelectValue {
                      optionId
                      name
                      field { ... on ProjectV2FieldCommon { id name } }
                    }
                    ... on ProjectV2ItemFieldNumberValue {
                      number
                      field { ... on ProjectV2FieldCommon { id name } }
                    }
                    ... on ProjectV2ItemFieldDateValue {
                      date
                      field { ... on ProjectV2FieldCommon { id name } }
                    }
                  }
                }
              }
              pageInfo { hasNextPage endCursor }
            }
          }
        }
      }
    `, { projectId, after }, 'list project items');
    const connection = data.node?.items;
    assert(connection, 'project items were unavailable');
    items.push(...(connection.nodes ?? []));
    assert(items.length <= MAX_CONTENT_ITEMS * 2, `project item count exceeds ${MAX_CONTENT_ITEMS * 2}`);
    after = connection.pageInfo.hasNextPage ? connection.pageInfo.endCursor : null;
  } while (after);
  return items;
}

function validateMetadataAgainstConfig(metadata, fieldsByName) {
  const mapping = {
    Status: metadata.workflow,
    Priority: metadata.priority,
    Area: metadata.area,
    'Work Type': metadata.type,
    Phase: metadata.phase,
    Risk: metadata.risk,
    Evidence: metadata.evidence,
  };
  for (const [fieldName, value] of Object.entries(mapping)) {
    if (value === undefined) continue;
    const field = fieldsByName.get(fieldName);
    assert(field?.dataType === 'SINGLE_SELECT', `single-select field ${fieldName} is unavailable`);
    assert(field.options?.some((option) => option.name === value),
      `metadata value ${JSON.stringify(value)} is not a configured ${fieldName} option`);
  }
}

function desiredFieldValues(metadata) {
  return new Map(Object.entries({
    Status: metadata.workflow,
    Priority: metadata.priority,
    Area: metadata.area,
    'Work Type': metadata.type,
    Phase: metadata.phase,
    Risk: metadata.risk,
    Evidence: metadata.evidence,
    Effort: metadata.effort,
    'Start date': metadata.startDate,
    'Target date': metadata.targetDate,
  }).filter(([, value]) => value !== undefined));
}

async function setProjectField(api, projectId, itemId, field, value, current) {
  if (field.dataType === 'SINGLE_SELECT') {
    const option = field.options.find((candidate) => candidate.name === value);
    assert(option, `option ${value} not found for ${field.name}`);
    if (current?.kind === 'SINGLE_SELECT' && current.optionId === option.id) return false;
    await api.request(`
      mutation SetRoadmapSelect($input: UpdateProjectV2ItemFieldValueInput!) {
        updateProjectV2ItemFieldValue(input: $input) { projectV2Item { id } }
      }
    `, { input: { projectId, itemId, fieldId: field.id, value: { singleSelectOptionId: option.id } } },
    `set ${field.name}`);
    return true;
  }
  if (field.dataType === 'NUMBER') {
    if (current?.kind === 'NUMBER' && current.number === value) return false;
    await api.request(`
      mutation SetRoadmapNumber($input: UpdateProjectV2ItemFieldValueInput!) {
        updateProjectV2ItemFieldValue(input: $input) { projectV2Item { id } }
      }
    `, { input: { projectId, itemId, fieldId: field.id, value: { number: value } } }, `set ${field.name}`);
    return true;
  }
  if (field.dataType === 'DATE') {
    if (current?.kind === 'DATE' && current.date === value) return false;
    await api.request(`
      mutation SetRoadmapDate($input: UpdateProjectV2ItemFieldValueInput!) {
        updateProjectV2ItemFieldValue(input: $input) { projectV2Item { id } }
      }
    `, { input: { projectId, itemId, fieldId: field.id, value: { date: value } } }, `set ${field.name}`);
    return true;
  }
  throw new Error(`unsupported project field type ${field.dataType}`);
}

async function reconcileItems(api, config, project, fieldsByName) {
  const [owner, name] = config.repository.split('/');
  const [issues, pullRequests, existingItems] = await Promise.all([
    listRepositoryIssues(api, owner, name),
    listRepositoryPullRequests(api, owner, name),
    listProjectItems(api, project.id),
  ]);
  const issuesByNumber = new Map(issues.map((issue) => [issue.number, issue]));
  const itemByContentId = new Map();
  for (const item of existingItems) {
    if (item.content?.repository?.nameWithOwner !== config.repository) continue;
    if (item.content?.id) itemByContentId.set(item.content.id, item);
  }

  let added = 0;
  let unarchived = 0;
  let fieldUpdates = 0;
  for (const content of [...issues, ...pullRequests]) {
    const metadata = deriveMetadata(content, issuesByNumber);
    validateMetadataAgainstConfig(metadata, fieldsByName);
    let item = itemByContentId.get(content.id);
    if (!item) {
      const data = await api.request(`
        mutation AddRoadmapItem($input: AddProjectV2ItemByIdInput!) {
          addProjectV2ItemById(input: $input) { item { id } }
        }
      `, { input: { projectId: project.id, contentId: content.id } }, `add ${content.kind} #${content.number}`);
      item = { id: data.addProjectV2ItemById.item.id, isArchived: false, fieldValues: { nodes: [] }, content };
      itemByContentId.set(content.id, item);
      added += 1;
    } else if (item.isArchived) {
      await api.request(`
        mutation UnarchiveRoadmapItem($input: UnarchiveProjectV2ItemInput!) {
          unarchiveProjectV2Item(input: $input) { item { id } }
        }
      `, { input: { projectId: project.id, itemId: item.id } }, `unarchive ${content.kind} #${content.number}`);
      unarchived += 1;
    }

    const current = currentValueMap(item.fieldValues?.nodes);
    for (const [fieldName, value] of desiredFieldValues(metadata)) {
      const field = fieldsByName.get(fieldName);
      assert(field, `project field ${fieldName} is missing`);
      if (await setProjectField(api, project.id, item.id, field, value, current.get(fieldName))) fieldUpdates += 1;
    }
  }
  return { issues: issues.length, pullRequests: pullRequests.length, added, unarchived, fieldUpdates };
}

export async function synchronize({ token, config }) {
  const api = new GitHubGraphQL(token);
  const context = await bootstrapContext(api, config);
  const project = await ensureProject(api, config, context);
  const fieldsByName = await ensureFields(api, config, project.id);
  await ensureViews(api, config, project.id, fieldsByName);
  const itemSummary = await reconcileItems(api, config, project, fieldsByName);
  return {
    projectNumber: project.number,
    projectUrl: `https://github.com/users/${config.owner}/projects/${project.number}`,
    ...itemSummary,
  };
}

async function main() {
  const args = new Set(process.argv.slice(2));
  const dryRun = args.has('--dry-run');
  const configPathArgument = process.argv.find((argument) => argument.startsWith('--config='));
  const configPath = configPathArgument?.slice('--config='.length) || process.env.ROADMAP_CONFIG || DEFAULT_CONFIG_PATH;
  const config = await loadConfig(configPath);
  const runtimeRepository = process.env.GITHUB_REPOSITORY;
  if (runtimeRepository) assert(runtimeRepository === config.repository,
    `GITHUB_REPOSITORY ${runtimeRepository} does not match ${config.repository}`);

  if (dryRun) {
    console.log(JSON.stringify({
      event: 'roadmap_sync_dry_run_ok',
      repository: config.repository,
      project: config.project.title,
      fields: config.fields.length,
      views: config.views.length,
    }));
    return;
  }

  const token = process.env.PROJECT_TOKEN?.trim();
  assert(token, 'PROJECT_TOKEN is required (classic PAT with project and repo scopes for this private user-owned Project)');
  const summary = await synchronize({ token, config });
  console.log(JSON.stringify({ event: 'roadmap_sync_complete', ...summary }));
}

const invoked = process.argv[1] ? pathToFileURL(resolve(process.argv[1])).href : '';
if (invoked === import.meta.url) {
  main().catch((error) => {
    console.error(JSON.stringify({
      event: 'roadmap_sync_failed',
      message: error instanceof Error ? error.message.slice(0, 500) : String(error).slice(0, 500),
    }));
    process.exitCode = 1;
  });
}
