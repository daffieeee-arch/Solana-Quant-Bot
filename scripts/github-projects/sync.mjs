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
  'v2Phase', 'v2Disposition', 'effort', 'startDate', 'targetDate',
]);
const ALLOWED_FIELD_TYPES = new Set(['SINGLE_SELECT', 'NUMBER', 'DATE']);
const ALLOWED_VIEW_LAYOUTS = new Set(['TABLE', 'BOARD', 'ROADMAP']);
const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const SHA256_PATTERN = /^[a-f0-9]{64}$/;
const RETENTION_ITEM_KINDS = new Set(['Issue', 'PullRequest']);
const DAY_MS = 24 * 60 * 60 * 1_000;

function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function assertExactKeys(value, allowed, name) {
  for (const key of Object.keys(value)) assert(allowed.has(key), `${name} contains unknown key ${key}`);
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
  for (const key of [
    'type', 'area', 'priority', 'phase', 'v2Phase', 'v2Disposition', 'risk', 'evidence', 'workflow',
  ]) {
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
  assertExactKeys(value, new Set([
    'schemaVersion', 'owner', 'repository', 'project', 'fields', 'views', 'itemRetention',
  ]), 'project config');
  assert(value.schemaVersion === 1, 'project config schemaVersion must equal 1');
  const owner = boundedString(value.owner, 'owner', 100);
  const repository = boundedString(value.repository, 'repository', 200);
  assert(/^[-A-Za-z0-9_.]+\/[-A-Za-z0-9_.]+$/.test(repository), 'repository must use owner/name');
  assert(repository.split('/')[0].toLowerCase() === owner.toLowerCase(), 'repository owner must match project owner');
  assert(isObject(value.project), 'project must be an object');
  assertExactKeys(value.project, new Set(['title', 'shortDescription', 'readme']), 'project');
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
        assertExactKeys(option, new Set(['name', 'color', 'description', 'aliases', 'preserveId']), `${name} option`);
        const optionName = boundedString(option.name, `${name}.option.name`, 128);
        assert(!optionNames.has(optionName), `duplicate ${name} option ${optionName}`);
        optionNames.add(optionName);
        boundedString(option.color, `${name}.${optionName}.color`, 32);
        boundedString(option.description, `${name}.${optionName}.description`, 256);
        if (option.aliases !== undefined) {
          assert(Array.isArray(option.aliases) && option.aliases.every((alias) => typeof alias === 'string'),
            `${name}.${optionName}.aliases must be strings`);
          for (const alias of option.aliases) boundedString(alias, `${name}.${optionName}.alias`, 128);
        }
        if (option.preserveId !== undefined) {
          assert(typeof option.preserveId === 'string' && /^[a-f0-9]{8}$/i.test(option.preserveId),
            `${name}.${optionName}.preserveId must be an eight-character option ID`);
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
  const viewAliases = new Set();
  for (const view of value.views) {
    assert(isObject(view), 'every view must be an object');
    assertExactKeys(view, new Set(['name', 'layout', 'filter', 'visibleFields', 'aliases']), 'view');
    const name = boundedString(view.name, 'view.name', 128);
    assert(!viewNames.has(name), `duplicate project view ${name}`);
    viewNames.add(name);
    assert(ALLOWED_VIEW_LAYOUTS.has(view.layout), `unsupported layout for ${name}`);
    if (view.filter !== undefined) boundedString(view.filter, `${name}.filter`, 512);
    if (view.visibleFields !== undefined) {
      assert(Array.isArray(view.visibleFields) && view.visibleFields.every((fieldName) => typeof fieldName === 'string'),
        `${name}.visibleFields must be strings`);
      for (const fieldName of view.visibleFields) boundedString(fieldName, `${name}.visibleField`, 128);
    }
    if (view.aliases !== undefined) {
      assert(Array.isArray(view.aliases) && view.aliases.every((alias) => typeof alias === 'string'),
        `${name}.aliases must be strings`);
      for (const alias of view.aliases) {
        const normalized = boundedString(alias, `${name}.alias`, 128).toLowerCase();
        assert(!viewAliases.has(normalized), `duplicate project view alias ${alias}`);
        viewAliases.add(normalized);
      }
    }
  }

  assert(isObject(value.itemRetention), 'itemRetention must be an object');
  assertExactKeys(value.itemRetention, new Set([
    'closed_item_retention_days', 'pre_v2_merged_pr_max_number', 'pre_v2_merged_pr_numbers',
    'pre_v2_snapshot_sha256', 'pre_v2_merged_pr_set_sha256', 'pinned_items',
  ]), 'itemRetention');
  const retentionDays = value.itemRetention.closed_item_retention_days;
  assert(Number.isInteger(retentionDays) && retentionDays >= 1 && retentionDays <= 3_650,
    'itemRetention.closed_item_retention_days must be an explicit integer between 1 and 3650');
  const preV2Maximum = value.itemRetention.pre_v2_merged_pr_max_number;
  assert(Number.isSafeInteger(preV2Maximum) && preV2Maximum > 0,
    'itemRetention.pre_v2_merged_pr_max_number must be a positive safe integer');
  assert(Array.isArray(value.itemRetention.pre_v2_merged_pr_numbers)
    && value.itemRetention.pre_v2_merged_pr_numbers.length > 0,
  'itemRetention.pre_v2_merged_pr_numbers must bind the non-empty audited candidate set');
  const preV2Numbers = value.itemRetention.pre_v2_merged_pr_numbers;
  assert(preV2Numbers.every((number) => Number.isSafeInteger(number) && number > 0 && number <= preV2Maximum),
    'itemRetention.pre_v2_merged_pr_numbers must contain positive safe integers within the cutoff');
  assert(new Set(preV2Numbers).size === preV2Numbers.length,
    'itemRetention.pre_v2_merged_pr_numbers must not contain duplicates');
  assert(valuesEqual(preV2Numbers, [...preV2Numbers].sort((left, right) => left - right)),
    'itemRetention.pre_v2_merged_pr_numbers must be sorted');
  assert(typeof value.itemRetention.pre_v2_snapshot_sha256 === 'string'
    && SHA256_PATTERN.test(value.itemRetention.pre_v2_snapshot_sha256),
  'itemRetention.pre_v2_snapshot_sha256 must bind the reviewed pre-migration export');
  assert(typeof value.itemRetention.pre_v2_merged_pr_set_sha256 === 'string'
    && SHA256_PATTERN.test(value.itemRetention.pre_v2_merged_pr_set_sha256),
  'itemRetention.pre_v2_merged_pr_set_sha256 must bind the audited candidate set');
  assert(Array.isArray(value.itemRetention.pinned_items), 'itemRetention.pinned_items must be an array');
  const pinnedKeys = new Set();
  for (const item of value.itemRetention.pinned_items) {
    assert(isObject(item), 'every pinned item must be an object');
    assertExactKeys(item, new Set(['kind', 'number']), 'pinned item');
    assert(RETENTION_ITEM_KINDS.has(item.kind), `unsupported pinned item kind ${item.kind}`);
    assert(Number.isSafeInteger(item.number) && item.number > 0, 'pinned item number must be a positive safe integer');
    const key = `${item.kind}:${item.number}`;
    assert(!pinnedKeys.has(key), `duplicate pinned item ${key}`);
    pinnedKeys.add(key);
  }
  return value;
}

export function projectViewLayoutInput(layout) {
  const mapped = {
    TABLE: 'TABLE_LAYOUT',
    BOARD: 'BOARD_LAYOUT',
    ROADMAP: 'ROADMAP_LAYOUT',
  }[layout];
  assert(mapped, `unsupported project view layout ${layout}`);
  return mapped;
}

function valuesEqual(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

export function selectInputOptions(field, existingOptions = []) {
  const claimed = new Set();
  return field.options.map((desired) => {
    const names = [desired.name, ...(desired.aliases ?? [])].map((name) => name.toLowerCase());
    let existing;
    if (desired.preserveId) {
      existing = existingOptions.find((candidate) => candidate.id === desired.preserveId);
      assert(existing, `${field.name}.${desired.name} must preserve missing option ID ${desired.preserveId}`);
      assert(names.includes(String(existing.name).toLowerCase()),
        `${field.name}.${desired.name} preserved option ID ${desired.preserveId} has incompatible name ${existing.name}`);
    } else {
      existing = existingOptions.find((candidate) => !claimed.has(candidate.id)
        && names.includes(String(candidate.name).toLowerCase()));
    }
    assert(!existing || !claimed.has(existing.id), `${field.name}.${desired.name} matched a duplicate option ID`);
    if (existing) claimed.add(existing.id);
    return {
      ...(existing?.id ? { id: existing.id } : {}),
      name: desired.name,
      color: desired.color,
      description: desired.description,
    };
  });
}

function contentKind(content) {
  assert(RETENTION_ITEM_KINDS.has(content?.kind), `unsupported content kind ${content?.kind}`);
  return content.kind;
}

function contentKey(content) {
  const kind = contentKind(content);
  assert(Number.isSafeInteger(content.number) && content.number > 0, `${kind} number must be a positive safe integer`);
  return `${kind}:${content.number}`;
}

function parsedTimestamp(value, name) {
  assert(typeof value === 'string', `${name} is required`);
  const timestamp = Date.parse(value);
  assert(Number.isFinite(timestamp), `${name} must be a valid timestamp`);
  return timestamp;
}

function contentIsOpen(content) {
  return String(content.state).toUpperCase() === 'OPEN';
}

function contentIsMergedPullRequest(content) {
  return content.kind === 'PullRequest'
    && (content.merged === true || String(content.state).toUpperCase() === 'MERGED');
}

function terminalTimestamp(content) {
  if (contentIsOpen(content)) return undefined;
  if (contentIsMergedPullRequest(content)) return parsedTimestamp(content.mergedAt, `${contentKey(content)}.mergedAt`);
  return parsedTimestamp(content.closedAt, `${contentKey(content)}.closedAt`);
}

function retentionPinnedKeys(retention) {
  assert(isObject(retention), 'retention policy is required');
  assert(Number.isInteger(retention.closed_item_retention_days)
    && retention.closed_item_retention_days >= 1
    && retention.closed_item_retention_days <= 3_650,
  'retention.closed_item_retention_days must be an explicit integer between 1 and 3650');
  assert(Number.isSafeInteger(retention.pre_v2_merged_pr_max_number)
    && retention.pre_v2_merged_pr_max_number > 0,
  'retention.pre_v2_merged_pr_max_number must be a positive safe integer');
  assert(Array.isArray(retention.pre_v2_merged_pr_numbers)
    && retention.pre_v2_merged_pr_numbers.length > 0,
  'retention.pre_v2_merged_pr_numbers must bind the audited candidate set');
  assert(Array.isArray(retention.pinned_items), 'retention.pinned_items must be an array');
  return new Set(retention.pinned_items.map((item) => `${item.kind}:${item.number}`));
}

export function planItemLifecycle({ content, item, retention, reconciledAt }) {
  const key = contentKey(content);
  const pinned = retentionPinnedKeys(retention).has(key);
  const now = parsedTimestamp(reconciledAt, 'reconciledAt');
  const preV2MergedPullRequest = contentIsMergedPullRequest(content)
    && retention.pre_v2_merged_pr_numbers.includes(content.number);

  if (item?.isArchived === true) {
    if (contentIsOpen(content)) {
      const reopen = Date.parse(content.latestReopenedAt ?? '');
      const archiveBarrier = Date.parse(item.updatedAt ?? '');
      if (Number.isFinite(reopen) && Number.isFinite(archiveBarrier) && reopen > archiveBarrier) {
        return { action: 'UNARCHIVE', reason: 'actual_reopen_after_archive', eligible: true };
      }
      return { action: 'KEEP_ARCHIVED', reason: 'no_proven_reopen_after_archive', eligible: false };
    }
    return { action: 'KEEP_ARCHIVED', reason: 'closed_item_remains_archived', eligible: false };
  }

  if (!item) {
    if (pinned) return { action: 'ADD', reason: 'pinned_item_missing', eligible: true };
    if (contentIsOpen(content)) return { action: 'ADD', reason: 'open_item_missing', eligible: true };
    if (preV2MergedPullRequest) {
      return { action: 'SKIP', reason: 'pre_v2_merged_pull_request_absent', eligible: false };
    }
    const terminal = terminalTimestamp(content);
    assert(terminal <= now, `${key} terminal timestamp is after reconciledAt`);
    if (now - terminal >= retention.closed_item_retention_days * DAY_MS) {
      return { action: 'SKIP', reason: 'closed_item_expired_before_add', eligible: false };
    }
    return { action: 'ADD', reason: 'closed_item_inside_retention', eligible: true };
  }

  if (pinned) return { action: 'KEEP_ACTIVE', reason: 'pinned_item_active', eligible: true };
  if (preV2MergedPullRequest) {
    return { action: 'ARCHIVE', reason: 'pre_v2_merged_pull_request', eligible: false };
  }
  if (contentIsOpen(content)) return { action: 'KEEP_ACTIVE', reason: 'open_item_active', eligible: true };

  const terminal = terminalTimestamp(content);
  assert(terminal <= now, `${key} terminal timestamp is after reconciledAt`);
  if (now - terminal >= retention.closed_item_retention_days * DAY_MS) {
    return { action: 'ARCHIVE', reason: 'closed_item_retention_expired', eligible: false };
  }
  return { action: 'KEEP_ACTIVE', reason: 'closed_item_inside_retention', eligible: true };
}

export function buildItemReconciliationPlan({ contents, existingItems, retention, reconciledAt }) {
  assert(Array.isArray(contents), 'contents must be an array');
  assert(Array.isArray(existingItems), 'existingItems must be an array');
  retentionPinnedKeys(retention);
  const expectedPreV2Numbers = [...retention.pre_v2_merged_pr_numbers];
  const observedPreV2Numbers = contents
    .filter((content) => contentIsMergedPullRequest(content)
      && content.number <= retention.pre_v2_merged_pr_max_number)
    .map((content) => content.number)
    .sort((left, right) => left - right);
  assert(valuesEqual(observedPreV2Numbers, expectedPreV2Numbers),
    `pre-V2 merged PR candidate drift: expected ${expectedPreV2Numbers.join(',')}; observed ${observedPreV2Numbers.join(',')}`);
  const itemByContentId = new Map();
  for (const item of existingItems) {
    const id = item?.content?.id ?? item?.contentId;
    if (!id) continue;
    assert(!itemByContentId.has(id), `duplicate Project item for content ID ${id}`);
    itemByContentId.set(id, item);
  }

  const ordered = [...contents].sort((left, right) => {
    const leftKind = contentKind(left) === 'Issue' ? 0 : 1;
    const rightKind = contentKind(right) === 'Issue' ? 0 : 1;
    return leftKind - rightKind || left.number - right.number || String(left.id).localeCompare(String(right.id));
  });
  const counts = {
    ADD: 0,
    KEEP_ACTIVE: 0,
    ARCHIVE: 0,
    UNARCHIVE: 0,
    KEEP_ARCHIVED: 0,
    SKIP: 0,
  };
  const reasonMap = new Map();
  const operations = ordered.map((content) => {
    assert(typeof content.id === 'string' && content.id.length > 0, `${contentKey(content)} content ID is required`);
    const item = itemByContentId.get(content.id);
    const decision = planItemLifecycle({ content, item, retention, reconciledAt });
    counts[decision.action] += 1;
    reasonMap.set(decision.reason, (reasonMap.get(decision.reason) ?? 0) + 1);
    return {
      content,
      item,
      contentKind: content.kind,
      contentNumber: content.number,
      ...decision,
    };
  });
  const reasonCounts = Object.fromEntries([...reasonMap.entries()].sort(([left], [right]) => left.localeCompare(right)));
  return {
    operations,
    counts,
    reasonCounts,
    baselines: {
      expectedPreV2MergedPullRequests: expectedPreV2Numbers.length,
      observedPreV2MergedPullRequests: observedPreV2Numbers.length,
    },
  };
}

export function validateMigrationBaseline({ existingFields, plan, retention }) {
  assert(Array.isArray(existingFields), 'existingFields must be an array');
  assert(isObject(plan) && Array.isArray(plan.operations), 'reconciliation plan is required');
  retentionPinnedKeys(retention);
  const names = new Set(existingFields.map((field) => field?.name));
  const hasV2Phase = names.has('V2 Phase');
  const hasV2Disposition = names.has('V2 Disposition');
  assert(hasV2Phase === hasV2Disposition,
    'partial V2 field migration detected; V2 Phase and V2 Disposition must appear together');
  const candidateNumbers = new Set(retention.pre_v2_merged_pr_numbers);
  const candidateOperations = plan.operations.filter((operation) => operation.content.kind === 'PullRequest'
    && candidateNumbers.has(operation.content.number));
  assert(candidateOperations.length === candidateNumbers.size,
    `Project archive baseline has ${candidateOperations.length} candidates; expected ${candidateNumbers.size}`);
  if (hasV2Phase) {
    const lost = candidateOperations.filter((operation) => !['ARCHIVE', 'KEEP_ARCHIVED'].includes(operation.action));
    assert(lost.length === 0,
      `continuing Project archive baseline drift: audited candidates must remain archived/present; found ${lost
        .map((operation) => `#${operation.content.number}:${operation.action}`).join(',')}`);
    return { mode: 'CONTINUING', expectedInitialArchives: candidateOperations.length };
  }

  const unexpected = candidateOperations.filter((operation) => operation.action !== 'ARCHIVE');
  assert(unexpected.length === 0,
    `initial Project archive baseline drift: expected all audited candidates active; found ${unexpected
      .map((operation) => `#${operation.content.number}:${operation.action}`).join(',')}`);
  return { mode: 'INITIAL', expectedInitialArchives: candidateOperations.length };
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
            public
            shortDescription
            readme
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
  const project = context.project;
  assert(project, `reviewed Project ${config.project.title} is unavailable`);
  let updated = 0;
  let repositoryLinks = 0;

  const projectDrift = project.title !== config.project.title
    || project.shortDescription !== config.project.shortDescription
    || project.readme !== config.project.readme
    || project.closed !== false
    || project.public !== false;
  if (projectDrift) {
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
    updated += 1;
  }

  const alreadyLinked = context.project?.repositories?.nodes?.some((repo) => repo.id === context.repository.id) ?? true;
  if (!alreadyLinked) {
    await api.request(`
      mutation LinkRoadmapProject($input: LinkProjectV2ToRepositoryInput!) {
        linkProjectV2ToRepository(input: $input) { repository { id nameWithOwner } }
      }
    `, { input: { projectId: project.id, repositoryId: context.repository.id } }, 'link project to repository');
    repositoryLinks += 1;
  }
  const verification = await api.request(`
    query VerifyRoadmapProject($projectId: ID!) {
      node(id: $projectId) {
        ... on ProjectV2 {
          id number title closed public shortDescription readme
          repositories(first: 100) { nodes { id nameWithOwner } }
        }
      }
    }
  `, { projectId: project.id }, 'verify project');
  const reconciled = verification.node;
  assert(reconciled?.id === project.id, 'reviewed Project was unavailable after reconciliation');
  assert(reconciled.title === config.project.title, 'Project title did not reconcile');
  assert(reconciled.shortDescription === config.project.shortDescription,
    'Project short description did not reconcile');
  assert(reconciled.readme === config.project.readme, 'Project README did not reconcile');
  assert(reconciled.closed === false && reconciled.public === false,
    'Project visibility/state did not reconcile');
  assert(reconciled.repositories?.nodes?.some((repository) => repository.id === context.repository.id),
    `Project did not remain linked to ${config.repository}`);
  return { project: reconciled, counts: { updated, repositoryLinks } };
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

function validateFieldPreconditions(config, existing) {
  for (const desired of config.fields) {
    const field = existing.find((candidate) => candidate.name === desired.name);
    if (!field) {
      assert(!desired.builtin, `built-in field ${desired.name} is missing`);
      assert(!desired.options?.some((option) => option.preserveId),
        `field ${desired.name} is missing but declares preserved option IDs`);
      continue;
    }
    assert(field.dataType === desired.dataType,
      `field ${desired.name} has data type ${field.dataType}, expected ${desired.dataType}`);
    if (desired.dataType === 'SINGLE_SELECT') selectInputOptions(desired, field.options ?? []);
  }
}

async function ensureFields(api, config, projectId, initialExisting) {
  let existing = initialExisting;
  let created = 0;
  let optionsUpdated = 0;
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
      created += 1;
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
        optionsUpdated += 1;
        console.log(JSON.stringify({ event: 'field_options_reconciled', name: desired.name }));
        existing = await listProjectFields(api, projectId);
      }
      const reconciledField = existing.find((candidate) => candidate.name === desired.name);
      for (const option of desired.options.filter((candidate) => candidate.preserveId)) {
        const live = reconciledField?.options?.find((candidate) => candidate.id === option.preserveId);
        assert(live && [option.name, ...(option.aliases ?? [])]
          .some((name) => name.toLowerCase() === String(live.name).toLowerCase()),
        `${desired.name}.${option.name} did not preserve option ID ${option.preserveId}`);
      }
    }
  }
  return {
    fieldsByName: new Map(existing.map((field) => [field.name, field])),
    counts: { created, optionsUpdated },
  };
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

export function validateViewPreconditions(config, existingViews) {
  assert(Array.isArray(existingViews), 'existingViews must be an array');
  const claimedIds = new Set();
  let creates = 0;
  for (const desired of config.views) {
    const acceptedNames = new Set([desired.name, ...(desired.aliases ?? [])]
      .map((name) => name.toLowerCase()));
    const matches = existingViews.filter((view) => acceptedNames.has(String(view.name).toLowerCase()));
    assert(matches.length <= 1, `multiple current/legacy views match ${desired.name}`);
    if (desired.aliases?.length) {
      assert(matches.length === 1, `audited legacy/current view for ${desired.name} is missing`);
    }
    if (matches.length === 0) {
      creates += 1;
      continue;
    }
    assert(!claimedIds.has(matches[0].id), `Project view ${matches[0].name} matched multiple desired views`);
    claimedIds.add(matches[0].id);
  }
  const unexpected = existingViews.filter((view) => !claimedIds.has(view.id));
  assert(unexpected.length === 0,
    `unexpected Project views are outside the reviewed migration: ${unexpected.map((view) => view.name).join(',')}`);
  return { matched: claimedIds.size, creates };
}

async function ensureViews(api, config, projectId, fieldsByName, initialExisting) {
  let existing = initialExisting;
  let created = 0;
  let updated = 0;
  for (const desired of config.views) {
    const apiLayout = projectViewLayoutInput(desired.layout);
    const visibleFieldIds = (desired.visibleFields ?? []).map((name) => {
      const field = fieldsByName.get(name);
      assert(field?.id, `view ${desired.name} references unavailable field ${name}`);
      return field.id;
    });
    let view = existing.find((candidate) => candidate.name === desired.name);
    if (!view && desired.aliases?.length) {
      const aliases = new Set(desired.aliases.map((alias) => alias.toLowerCase()));
      const matches = existing.filter((candidate) => aliases.has(candidate.name.toLowerCase()));
      assert(matches.length <= 1, `multiple legacy views match ${desired.name}`);
      view = matches[0];
    }
    if (!view) {
      const input = {
        projectId,
        name: desired.name,
        layout: apiLayout,
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
      created += 1;
      console.log(JSON.stringify({ event: 'view_created', name: desired.name, layout: desired.layout }));
    }
    const update = {
      viewId: view.id,
      name: desired.name,
      layout: apiLayout,
      ...(desired.filter ? { filter: desired.filter } : {}),
      ...(desired.layout !== 'ROADMAP' && visibleFieldIds.length > 0
        ? { configuration: { visibleFieldIds } }
        : {}),
    };
    const currentVisibleFieldIds = (view.configuration?.visibleFields?.nodes ?? []).map((field) => field.id);
    const viewDrift = view.name !== desired.name
      || view.layout !== apiLayout
      || (view.filter ?? '') !== (desired.filter ?? '')
      || !valuesEqual(currentVisibleFieldIds, visibleFieldIds);
    if (viewDrift) {
      await api.request(`
        mutation UpdateRoadmapView($input: UpdateProjectV2ViewInput!) {
          updateProjectV2View(input: $input) { projectV2View { id name layout filter } }
        }
      `, { input: update }, `update view ${desired.name}`);
      updated += 1;
    }
    existing = await listProjectViews(api, projectId);
    const reconciled = existing.find((candidate) => candidate.name === desired.name);
    assert(reconciled, `view ${desired.name} was not present after reconciliation`);
    const reconciledVisibleFieldIds = (reconciled.configuration?.visibleFields?.nodes ?? []).map((field) => field.id);
    assert(reconciled.layout === apiLayout, `view ${desired.name} layout did not reconcile`);
    assert((reconciled.filter ?? '') === (desired.filter ?? ''), `view ${desired.name} filter did not reconcile`);
    assert(valuesEqual(reconciledVisibleFieldIds, visibleFieldIds),
      `view ${desired.name} visible fields did not reconcile`);
  }
  return { created, updated };
}

async function listRepositoryIssues(api, owner, name) {
  const items = [];
  let after = null;
  do {
    const data = await api.request(`
      query RepositoryIssues($owner: String!, $name: String!, $after: String) {
        repository(owner: $owner, name: $name) {
          issues(first: 100, after: $after, states: [OPEN, CLOSED], orderBy: { field: UPDATED_AT, direction: DESC }) {
            nodes {
              id number title body url state stateReason createdAt updatedAt closedAt
              reopened: timelineItems(last: 1, itemTypes: [REOPENED_EVENT]) {
                nodes { ... on ReopenedEvent { createdAt } }
              }
            }
            pageInfo { hasNextPage endCursor }
          }
        }
      }
    `, { owner, name, after }, 'list repository issues');
    const connection = data.repository?.issues;
    assert(connection, 'repository issues were unavailable');
    items.push(...(connection.nodes ?? []).map(({ reopened, ...item }) => ({
      ...item,
      kind: 'Issue',
      latestReopenedAt: reopened?.nodes?.[0]?.createdAt,
    })));
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
            nodes {
              id number title body url state isDraft merged mergedAt createdAt updatedAt closedAt
              reopened: timelineItems(last: 1, itemTypes: [REOPENED_EVENT]) {
                nodes { ... on ReopenedEvent { createdAt } }
              }
            }
            pageInfo { hasNextPage endCursor }
          }
        }
      }
    `, { owner, name, after }, 'list repository pull requests');
    const connection = data.repository?.pullRequests;
    assert(connection, 'repository pull requests were unavailable');
    items.push(...(connection.nodes ?? []).map(({ reopened, ...item }) => ({
      ...item,
      kind: 'PullRequest',
      latestReopenedAt: reopened?.nodes?.[0]?.createdAt,
    })));
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
            items(first: 100, after: $after, archivedStates: [ARCHIVED, NOT_ARCHIVED]) {
              nodes {
                id
                isArchived
                updatedAt
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
    'V2 Phase': metadata.v2Phase,
    'V2 Disposition': metadata.v2Disposition,
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
    'V2 Phase': metadata.v2Phase,
    'V2 Disposition': metadata.v2Disposition,
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

async function planRepositoryItems(api, config, project, reconciledAt) {
  const [owner, name] = config.repository.split('/');
  const [issues, pullRequests, existingItems] = await Promise.all([
    listRepositoryIssues(api, owner, name),
    listRepositoryPullRequests(api, owner, name),
    listProjectItems(api, project.id),
  ]);
  const issuesByNumber = new Map(issues.map((issue) => [issue.number, issue]));
  const repositoryItems = existingItems.filter((item) => item.content?.repository?.nameWithOwner === config.repository);
  const plan = buildItemReconciliationPlan({
    contents: [...issues, ...pullRequests],
    existingItems: repositoryItems,
    retention: config.itemRetention,
    reconciledAt,
  });
  const configuredFieldsByName = new Map(config.fields.map((field) => [field.name, field]));
  const metadataByContentId = new Map();
  for (const content of [...issues, ...pullRequests]) {
    const metadata = deriveMetadata(content, issuesByNumber);
    validateMetadataAgainstConfig(metadata, configuredFieldsByName);
    metadataByContentId.set(content.id, metadata);
  }
  return {
    issues,
    pullRequests,
    repositoryItems,
    metadataByContentId,
    plan,
  };
}

async function reconcileItems(api, config, project, fieldsByName, itemState) {
  const {
    issues,
    pullRequests,
    repositoryItems,
    metadataByContentId,
    plan,
  } = itemState;
  const executed = { added: 0, archived: 0, unarchived: 0 };
  let fieldUpdates = 0;
  for (const operation of plan.operations) {
    const { content } = operation;
    const metadata = metadataByContentId.get(content.id);
    assert(metadata, `prevalidated metadata is missing for ${content.kind} #${content.number}`);
    validateMetadataAgainstConfig(metadata, fieldsByName);
    let { item } = operation;
    if (operation.action === 'ADD') {
      const data = await api.request(`
        mutation AddRoadmapItem($input: AddProjectV2ItemByIdInput!) {
          addProjectV2ItemById(input: $input) { item { id } }
        }
      `, { input: { projectId: project.id, contentId: content.id } }, `add ${content.kind} #${content.number}`);
      item = { id: data.addProjectV2ItemById.item.id, isArchived: false, fieldValues: { nodes: [] }, content };
      executed.added += 1;
    } else if (operation.action === 'UNARCHIVE') {
      await api.request(`
        mutation UnarchiveRoadmapItem($input: UnarchiveProjectV2ItemInput!) {
          unarchiveProjectV2Item(input: $input) { item { id } }
        }
      `, { input: { projectId: project.id, itemId: item.id } }, `unarchive ${content.kind} #${content.number}`);
      executed.unarchived += 1;
    } else if (operation.action === 'ARCHIVE') {
      await api.request(`
        mutation ArchiveRoadmapItem($input: ArchiveProjectV2ItemInput!) {
          archiveProjectV2Item(input: $input) { item { id } }
        }
      `, { input: { projectId: project.id, itemId: item.id } }, `archive ${content.kind} #${content.number}`);
      executed.archived += 1;
      continue;
    } else if (operation.action === 'KEEP_ARCHIVED' || operation.action === 'SKIP') {
      continue;
    }

    assert(item?.id, `${operation.action} ${content.kind} #${content.number} did not produce an active Project item`);
    const current = currentValueMap(item.fieldValues?.nodes);
    for (const [fieldName, value] of desiredFieldValues(metadata)) {
      const field = fieldsByName.get(fieldName);
      assert(field, `project field ${fieldName} is missing`);
      if (await setProjectField(api, project.id, item.id, field, value, current.get(fieldName))) fieldUpdates += 1;
    }
  }
  const verifiedItems = (await listProjectItems(api, project.id))
    .filter((item) => item.content?.repository?.nameWithOwner === config.repository);
  const verifiedByContentId = new Map(verifiedItems.map((item) => [item.content?.id, item]));
  const verified = { active: 0, archived: 0, absent: 0 };
  for (const operation of plan.operations) {
    const actual = verifiedByContentId.get(operation.content.id);
    if (operation.action === 'SKIP') {
      assert(!actual, `SKIP ${operation.content.kind} #${operation.content.number} unexpectedly has a Project item`);
      verified.absent += 1;
    } else if (operation.action === 'ARCHIVE' || operation.action === 'KEEP_ARCHIVED') {
      assert(actual?.isArchived === true,
        `${operation.action} ${operation.content.kind} #${operation.content.number} was not verified archived`);
      verified.archived += 1;
    } else {
      assert(actual && actual.isArchived === false,
        `${operation.action} ${operation.content.kind} #${operation.content.number} was not verified active`);
      verified.active += 1;
    }
  }
  return {
    observed: {
      issues: issues.length,
      pullRequests: pullRequests.length,
      existingRepositoryItems: repositoryItems.length,
    },
    planned: plan.counts,
    reasonCounts: plan.reasonCounts,
    baselines: plan.baselines,
    executed: { ...executed, fieldUpdates },
    verified,
  };
}

export async function synchronize({ token, config, reconciledAt = new Date().toISOString() }) {
  const api = new GitHubGraphQL(token);
  const context = await bootstrapContext(api, config);
  assert(context.project,
    `reviewed Project ${config.project.title} is unavailable; refusing to create or migrate a replacement`);
  // Compute and validate the complete repository-item lifecycle plan before the
  // first mutation. In particular, audited pre-V2 archive-set drift must fail
  // before Project copy, fields, views or items can change.
  const itemState = await planRepositoryItems(api, config, context.project, reconciledAt);
  const [initialFields, initialViews] = await Promise.all([
    listProjectFields(api, context.project.id),
    listProjectViews(api, context.project.id),
  ]);
  const migrationBaseline = validateMigrationBaseline({
    existingFields: initialFields,
    plan: itemState.plan,
    retention: config.itemRetention,
  });
  validateFieldPreconditions(config, initialFields);
  const viewBaseline = validateViewPreconditions(config, initialViews);
  const projectResult = await ensureProject(api, config, context);
  const fieldResult = await ensureFields(api, config, projectResult.project.id, initialFields);
  const viewCounts = await ensureViews(
    api,
    config,
    projectResult.project.id,
    fieldResult.fieldsByName,
    initialViews,
  );
  const itemSummary = await reconcileItems(
    api,
    config,
    projectResult.project,
    fieldResult.fieldsByName,
    itemState,
  );
  return {
    projectNumber: projectResult.project.number,
    projectUrl: `https://github.com/users/${config.owner}/projects/${projectResult.project.number}`,
    reconciledAt,
    projectChanges: projectResult.counts,
    fieldChanges: fieldResult.counts,
    viewChanges: { baseline: viewBaseline, ...viewCounts },
    migrationBaseline,
    items: itemSummary,
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
      closedItemRetentionDays: config.itemRetention.closed_item_retention_days,
      preV2MergedPrMaxNumber: config.itemRetention.pre_v2_merged_pr_max_number,
      preV2MergedPrCount: config.itemRetention.pre_v2_merged_pr_numbers.length,
      preV2SnapshotSha256: config.itemRetention.pre_v2_snapshot_sha256,
      preV2MergedPrSetSha256: config.itemRetention.pre_v2_merged_pr_set_sha256,
      pinnedItems: config.itemRetention.pinned_items.length,
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
