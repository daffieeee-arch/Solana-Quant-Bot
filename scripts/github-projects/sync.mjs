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
const MANAGED_FIELD_METADATA_KEYS = new Map([
  ['Status', 'workflow'],
  ['Priority', 'priority'],
  ['Area', 'area'],
  ['Work Type', 'type'],
  ['Phase', 'phase'],
  ['V2 Phase', 'v2Phase'],
  ['V2 Disposition', 'v2Disposition'],
  ['Risk', 'risk'],
  ['Evidence', 'evidence'],
  ['Effort', 'effort'],
  ['Start date', 'startDate'],
  ['Target date', 'targetDate'],
]);
export const MANAGED_FIELD_ACTIONS = Object.freeze({
  SET: 'SET',
  CLEAR: 'CLEAR',
  NO_OP: 'NO_OP',
});
export const PROJECT_FIELD_UNSET = Object.freeze({ kind: 'UNSET' });
const ALLOWED_VIEW_LAYOUTS = new Set(['TABLE', 'BOARD', 'ROADMAP']);
const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const SHA256_PATTERN = /^[a-f0-9]{64}$/;
const RETENTION_ITEM_KINDS = new Set(['Issue', 'PullRequest']);
const DAY_MS = 24 * 60 * 60 * 1_000;
export const PROJECT_VERIFICATION_DELAYS_MS = Object.freeze([0, 500, 1_000, 2_000, 4_000]);
export const PROJECT_VERIFICATION_OUTCOMES = Object.freeze({
  CONVERGED: 'CONVERGED',
  NOT_YET_CONVERGED: 'NOT_YET_CONVERGED',
  HARD_DRIFT: 'HARD_DRIFT',
});
const PROJECT_VERIFICATION_REASONS = new Set([
  'EXACT_STATE_VISIBLE',
  'ITEM_NOT_VISIBLE',
  'FIELD_NOT_VISIBLE',
  'ARCHIVE_STATE_NOT_VISIBLE',
  'PROJECT_METADATA_NOT_VISIBLE',
  'FIELD_SCHEMA_NOT_VISIBLE',
  'VIEW_NOT_VISIBLE',
  'DUPLICATE_CONTENT',
  'CONTENT_IDENTITY_DRIFT',
  'ITEM_DELETED',
  'UNEXPECTED_ITEM',
  'UNEXPECTED_FIELD_VALUE',
  'UNEXPECTED_LIFECYCLE_STATE',
  'PROJECT_SCHEMA_DRIFT',
  'FIELD_SCHEMA_DRIFT',
  'VIEW_SCHEMA_DRIFT',
]);
const RETRYABLE_PROJECT_VERIFICATION_REASONS = new Set([
  'ITEM_NOT_VISIBLE',
  'FIELD_NOT_VISIBLE',
  'ARCHIVE_STATE_NOT_VISIBLE',
  'PROJECT_METADATA_NOT_VISIBLE',
  'FIELD_SCHEMA_NOT_VISIBLE',
  'VIEW_NOT_VISIBLE',
]);
const HARD_PROJECT_VERIFICATION_REASONS = new Set([
  'DUPLICATE_CONTENT',
  'CONTENT_IDENTITY_DRIFT',
  'ITEM_DELETED',
  'UNEXPECTED_ITEM',
  'UNEXPECTED_FIELD_VALUE',
  'UNEXPECTED_LIFECYCLE_STATE',
  'PROJECT_SCHEMA_DRIFT',
  'FIELD_SCHEMA_DRIFT',
  'VIEW_SCHEMA_DRIFT',
]);

function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function verificationResult(outcome, reason, expected, observed, details = undefined) {
  assert(Object.values(PROJECT_VERIFICATION_OUTCOMES).includes(outcome), `unsupported verification outcome ${outcome}`);
  assert(PROJECT_VERIFICATION_REASONS.has(reason), `unsupported verification reason ${reason}`);
  assert((outcome === PROJECT_VERIFICATION_OUTCOMES.CONVERGED && reason === 'EXACT_STATE_VISIBLE')
    || (outcome === PROJECT_VERIFICATION_OUTCOMES.NOT_YET_CONVERGED
      && RETRYABLE_PROJECT_VERIFICATION_REASONS.has(reason))
    || (outcome === PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT
      && HARD_PROJECT_VERIFICATION_REASONS.has(reason)),
  `verification outcome ${outcome} is incompatible with reason ${reason}`);
  return {
    outcome,
    reason,
    expected: boundedString(expected, 'verification expected summary', 512),
    observed: boundedString(observed, 'verification observed summary', 512),
    ...(details === undefined ? {} : { details }),
  };
}

const projectVerificationSleep = (milliseconds) => new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds));

/**
 * Read-only GitHub Projects projection convergence. Mutations deliberately are
 * not accepted by this function, so a retry cannot replay one.
 */
export async function verifyProjectProjection({
  operationType,
  identity,
  read,
  classify,
  sleep = projectVerificationSleep,
  log = (entry) => console.log(JSON.stringify(entry)),
}) {
  const operation = boundedString(operationType, 'verification operation type', 64);
  const target = boundedString(identity, 'verification identity', 128);
  assert(typeof read === 'function', 'verification read function is required');
  assert(typeof classify === 'function', 'verification classifier is required');
  assert(typeof sleep === 'function', 'verification sleep function is required');
  assert(typeof log === 'function', 'verification log function is required');

  let finalResult;
  for (let attempt = 0; attempt < PROJECT_VERIFICATION_DELAYS_MS.length; attempt += 1) {
    const waitMilliseconds = PROJECT_VERIFICATION_DELAYS_MS[attempt];
    if (waitMilliseconds > 0) await sleep(waitMilliseconds);
    const snapshot = await read();
    const result = classify(snapshot);
    assert(isObject(result), 'verification classifier must return an object');
    assert(Object.values(PROJECT_VERIFICATION_OUTCOMES).includes(result.outcome),
      `verification classifier returned unsupported outcome ${result.outcome}`);
    assert(PROJECT_VERIFICATION_REASONS.has(result.reason),
      `verification classifier returned unsupported reason ${result.reason}`);
    assert((result.outcome === PROJECT_VERIFICATION_OUTCOMES.CONVERGED && result.reason === 'EXACT_STATE_VISIBLE')
      || (result.outcome === PROJECT_VERIFICATION_OUTCOMES.NOT_YET_CONVERGED
        && RETRYABLE_PROJECT_VERIFICATION_REASONS.has(result.reason))
      || (result.outcome === PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT
        && HARD_PROJECT_VERIFICATION_REASONS.has(result.reason)),
    `verification classifier returned incompatible outcome/reason ${result.outcome}/${result.reason}`);
    boundedString(result.expected, 'verification expected summary', 512);
    boundedString(result.observed, 'verification observed summary', 512);
    finalResult = result;

    if (result.outcome === PROJECT_VERIFICATION_OUTCOMES.CONVERGED) {
      if (attempt > 0) {
        log({
          event: 'project_verification_converged',
          operationType: operation,
          identity: target,
          verificationAttempt: attempt,
        });
      }
      return { snapshot, attempts: attempt + 1, result };
    }
    if (result.outcome === PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT) {
      throw new Error(`PROJECT_HARD_DRIFT operation=${operation} identity=${target} attempts=${attempt + 1} reason=${result.reason} expected=${result.expected} observed=${result.observed}`);
    }
    log({
      event: 'project_verification_delayed',
      operationType: operation,
      identity: target,
      verificationAttempt: attempt,
      reason: result.reason,
    });
  }

  throw new Error(`PROJECT_VERIFICATION_EXHAUSTED operation=${operation} identity=${target} attempts=${PROJECT_VERIFICATION_DELAYS_MS.length} reason=${finalResult.reason} expected=${finalResult.expected} finalObserved=${finalResult.observed}`);
}

function assertExactKeys(value, allowed, name) {
  for (const key of Object.keys(value)) assert(allowed.has(key), `${name} contains unknown key ${key}`);
}

function boundedString(value, name, maximum = 256) {
  assert(typeof value === 'string' && value.trim().length > 0, `${name} must be a non-empty string`);
  assert(Buffer.byteLength(value, 'utf8') <= maximum, `${name} exceeds ${maximum} bytes`);
  return value.trim();
}

const REPOSITORY_IDENTITY = /^[-A-Za-z0-9_.]+\/[-A-Za-z0-9_.]+$/;

export function normalizeRepositoryIdentity(value) {
  return typeof value === 'string' ? value.trim().toLowerCase() : '';
}

export function configuredRepositoryAliases(configOrAliases = []) {
  if (Array.isArray(configOrAliases)) return configOrAliases;
  return Array.isArray(configOrAliases?.repositoryAliases) ? configOrAliases.repositoryAliases : [];
}

export function isConfiguredRepository(candidate, canonical, aliases = []) {
  const needle = normalizeRepositoryIdentity(candidate);
  if (!needle || typeof canonical !== 'string') return false;
  if (needle === normalizeRepositoryIdentity(canonical)) return true;
  return aliases.some((alias) => needle === normalizeRepositoryIdentity(alias));
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

// Generic reporting helper only. Metadata inheritance must use the explicit,
// order-preserving resolver below and must never consume this sorted set.
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

export const PULL_REQUEST_ROUTE_SOURCES = Object.freeze({
  DIRECT_ROADMAP: 'DIRECT_ROADMAP',
  CLOSING_DIRECTIVE: 'CLOSING_DIRECTIVE',
  IMPLEMENTATION_DIRECTIVE: 'IMPLEMENTATION_DIRECTIVE',
  NONE: 'NONE',
});

function markdownFenceOpening(line) {
  const match = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line);
  if (!match) return undefined;
  if (match[1][0] === '`' && match[2].includes('`')) return undefined;
  return { marker: match[1][0], length: match[1].length };
}

function markdownFenceCloses(line, fence) {
  const match = /^ {0,3}(`{3,}|~{3,})[ \t]*$/.exec(line);
  return Boolean(match && match[1][0] === fence.marker && match[1].length >= fence.length);
}

function stripHtmlComments(line, initiallyInComment) {
  let visible = '';
  let cursor = 0;
  let inComment = initiallyInComment;
  while (cursor < line.length) {
    if (inComment) {
      const end = line.indexOf('-->', cursor);
      if (end === -1) return { line: visible, inComment };
      inComment = false;
      cursor = end + 3;
      continue;
    }
    const start = line.indexOf('<!--', cursor);
    if (start === -1) return { line: visible + line.slice(cursor), inComment };
    visible += line.slice(cursor, start);
    inComment = true;
    cursor = start + 4;
  }
  return { line: visible, inComment };
}

function visibleMarkdownLines(body) {
  const lines = (typeof body === 'string' ? body : '').split(/\r?\n/);
  const visible = [];
  let fence;
  let inComment = false;

  for (const rawLine of lines) {
    if (fence) {
      if (markdownFenceCloses(rawLine, fence)) fence = undefined;
      continue;
    }

    if (!inComment) {
      const rawOpening = markdownFenceOpening(rawLine);
      if (rawOpening) {
        fence = rawOpening;
        continue;
      }
    }

    const stripped = stripHtmlComments(rawLine, inComment);
    inComment = stripped.inComment;
    const visibleOpening = markdownFenceOpening(stripped.line);
    if (visibleOpening) {
      fence = visibleOpening;
      continue;
    }
    visible.push(stripped.line);
  }
  return visible;
}

function parseRouteReferenceToken(token, repository, directiveName, repositoryAliases = []) {
  const shorthand = /^#([1-9]\d{0,9})$/.exec(token);
  if (shorthand) return Number(shorthand[1]);

  let parsed;
  try {
    parsed = new URL(token);
  } catch {
    throw new Error(`${directiveName} contains an invalid issue reference`);
  }
  assert(parsed.protocol === 'https:' && parsed.hostname.toLowerCase() === 'github.com',
    `${directiveName} contains a non-GitHub issue URL`);
  assert(parsed.username === '' && parsed.password === '' && parsed.port === ''
    && parsed.search === '' && parsed.hash === '', `${directiveName} contains an ambiguous issue URL`);
  const path = /^\/([-A-Za-z0-9_.]+)\/([-A-Za-z0-9_.]+)\/issues\/([1-9]\d{0,9})\/?$/.exec(parsed.pathname);
  assert(path, `${directiveName} URL must identify a GitHub issue`);
  assert(typeof repository === 'string' && repository.trim().length > 0,
    `${directiveName} URL cannot be checked without repository identity`);
  const referencedRepository = `${path[1]}/${path[2]}`;
  assert(isConfiguredRepository(referencedRepository, repository, repositoryAliases),
    `${directiveName} references foreign repository ${referencedRepository}`);
  return Number(path[3]);
}

function parseRouteReferences(payload, { repository, issuesByNumber, directiveName, repositoryAliases = [] }) {
  const raw = typeof payload === 'string' ? payload.trim() : '';
  assert(raw.length > 0, `${directiveName} must contain at least one issue reference`);
  const tokens = raw.split(/[\s,]+/).filter(Boolean);
  assert(tokens.length > 0, `${directiveName} must contain at least one issue reference`);
  return tokens.map((token) => {
    const number = parseRouteReferenceToken(token, repository, directiveName, repositoryAliases);
    const issue = issuesByNumber.get(number);
    assert(issue?.kind === 'Issue' && issue.number === number,
      `${directiveName} references unavailable same-repository issue #${number}`);
    return number;
  });
}

function routeResult(source, issueNumbers) {
  const seen = new Set();
  const orderedIssueNumbers = [];
  for (const issueNumber of issueNumbers) {
    if (seen.has(issueNumber)) continue;
    seen.add(issueNumber);
    orderedIssueNumbers.push(issueNumber);
  }
  const frozenOrdered = Object.freeze(orderedIssueNumbers);
  return Object.freeze({
    source,
    primaryIssueNumber: frozenOrdered[0],
    secondaryIssueNumbers: Object.freeze(frozenOrdered.slice(1)),
    orderedIssueNumbers: frozenOrdered,
  });
}

function looksLikeReferenceOnlyPayload(payload) {
  const tokens = String(payload ?? '').trim().split(/[\s,]+/).filter(Boolean);
  return tokens.length > 0
    && tokens.every((token) => token.startsWith('#') || /^https?:\/\//i.test(token));
}

/**
 * Resolve the one issue whose roadmap metadata a pull request may inherit.
 * This function is deliberately pure: selected directives are validated
 * against the already-read repository issue map and no GitHub mutation is
 * available from this boundary.
 */
export function resolvePullRequestInheritanceRoute({
  body,
  issuesByNumber = new Map(),
  repository,
  repositoryAliases = [],
}) {
  assert(issuesByNumber instanceof Map, 'issuesByNumber must be a Map');
  const lines = visibleMarkdownLines(body);
  const roadmapDirectives = lines
    .map((line) => /^ {0,3}Roadmap:\s*(.*?)\s*$/i.exec(line))
    .filter(Boolean);
  assert(roadmapDirectives.length <= 1, 'pull request must contain at most one Roadmap: directive');
  if (roadmapDirectives.length === 1) {
    const issueNumbers = parseRouteReferences(roadmapDirectives[0][1], {
      repository,
      repositoryAliases,
      issuesByNumber,
      directiveName: 'Roadmap: directive',
    });
    return routeResult(PULL_REQUEST_ROUTE_SOURCES.DIRECT_ROADMAP, issueNumbers);
  }

  const closingDirectives = lines
    .map((line) => /^ {0,3}(?:Closes|Close|Fixes|Fix|Resolves|Resolve)(?:\s*:\s*|\s+)(.*?)\s*$/i.exec(line))
    .filter((directive) => directive && looksLikeReferenceOnlyPayload(directive[1]));
  if (closingDirectives.length > 0) {
    const issueNumbers = closingDirectives.flatMap((directive) => parseRouteReferences(directive[1], {
      repository,
      repositoryAliases,
      issuesByNumber,
      directiveName: 'closing directive',
    }));
    return routeResult(PULL_REQUEST_ROUTE_SOURCES.CLOSING_DIRECTIVE, issueNumbers);
  }

  const implementationDirectives = lines
    .map((line) => /^ {0,3}(?:Implements|Tracks):\s*(.*?)\s*$/i.exec(line))
    .filter((directive) => directive && looksLikeReferenceOnlyPayload(directive[1]));
  if (implementationDirectives.length > 0) {
    const issueNumbers = implementationDirectives.flatMap((directive) => parseRouteReferences(directive[1], {
      repository,
      repositoryAliases,
      issuesByNumber,
      directiveName: 'implementation directive',
    }));
    return routeResult(PULL_REQUEST_ROUTE_SOURCES.IMPLEMENTATION_DIRECTIVE, issueNumbers);
  }

  return routeResult(PULL_REQUEST_ROUTE_SOURCES.NONE, []);
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

export function deriveMetadata(content, issuesByNumber = new Map(), repository = undefined, repositoryAliases = []) {
  const direct = parseRoadmapMeta(content.body) ?? { schemaVersion: 1 };
  const inferred = inferFromTitle(content.title);
  let inherited = {};
  if (content.kind === 'PullRequest') {
    const route = resolvePullRequestInheritanceRoute({
      body: content.body,
      issuesByNumber,
      repository,
      repositoryAliases,
    });
    if (route.primaryIssueNumber !== undefined) {
      const issue = issuesByNumber.get(route.primaryIssueNumber);
      inherited = { ...inferFromTitle(issue.title), ...(parseRoadmapMeta(issue.body) ?? {}) };
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
    'schemaVersion', 'owner', 'repository', 'repositoryAliases', 'project', 'fields', 'views', 'itemRetention',
  ]), 'project config');
  assert(value.schemaVersion === 1, 'project config schemaVersion must equal 1');
  const owner = boundedString(value.owner, 'owner', 100);
  const repository = boundedString(value.repository, 'repository', 200);
  assert(REPOSITORY_IDENTITY.test(repository), 'repository must use owner/name');
  assert(repository.split('/')[0].toLowerCase() === owner.toLowerCase(), 'repository owner must match project owner');
  let repositoryAliases = [];
  if (value.repositoryAliases !== undefined) {
    assert(Array.isArray(value.repositoryAliases) && value.repositoryAliases.length <= 8,
      'repositoryAliases must be an array of at most 8 former repository names');
    const seen = new Set([normalizeRepositoryIdentity(repository)]);
    for (const alias of value.repositoryAliases) {
      const normalizedAlias = boundedString(alias, 'repositoryAliases entry', 200);
      assert(REPOSITORY_IDENTITY.test(normalizedAlias), 'repositoryAliases entries must use owner/name');
      assert(normalizedAlias.split('/')[0].toLowerCase() === owner.toLowerCase(),
        'repositoryAliases owner must match project owner');
      const identity = normalizeRepositoryIdentity(normalizedAlias);
      assert(!seen.has(identity), `duplicate repository identity ${normalizedAlias}`);
      seen.add(identity);
      repositoryAliases.push(normalizedAlias);
    }
  }
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

function inspectCurrentValueMap(fieldValues) {
  if (fieldValues === undefined || fieldValues === null) {
    return { error: 'Project item field-value projection is missing' };
  }
  const connection = Array.isArray(fieldValues) ? { nodes: fieldValues } : fieldValues;
  if (!Array.isArray(connection.nodes)) return { error: 'Project item field-value nodes are missing' };
  if (!Array.isArray(fieldValues) && connection.pageInfo?.hasNextPage !== false) {
    return { error: 'Project item field-value pagination state is missing or incomplete' };
  }
  if (connection.pageInfo?.hasNextPage === true) {
    return { error: 'Project item field values exceed the verified projection page' };
  }
  const map = new Map();
  const fieldIds = new Set();
  for (const node of connection.nodes ?? []) {
    if (![
      'ProjectV2ItemFieldSingleSelectValue',
      'ProjectV2ItemFieldNumberValue',
      'ProjectV2ItemFieldDateValue',
    ].includes(node?.__typename)) continue;
    const name = node?.field?.name;
    const fieldId = node?.field?.id;
    if (!name || !fieldId) return { error: 'supported field value has incomplete field identity' };
    if (map.has(name)) return { error: `duplicate field value for ${name}` };
    if (fieldIds.has(fieldId)) return { error: `duplicate field identity ${fieldId}` };
    fieldIds.add(fieldId);
    if (node.__typename === 'ProjectV2ItemFieldSingleSelectValue') {
      map.set(name, {
        kind: 'SINGLE_SELECT', fieldId, optionId: node.optionId ?? null, name: node.name ?? null,
      });
    } else if (node.__typename === 'ProjectV2ItemFieldNumberValue') {
      map.set(name, { kind: 'NUMBER', fieldId, number: node.number ?? null });
    } else if (node.__typename === 'ProjectV2ItemFieldDateValue') {
      map.set(name, { kind: 'DATE', fieldId, date: node.date ?? null });
    }
  }
  return { map };
}

function currentValueMap(fieldValues) {
  const inspected = inspectCurrentValueMap(fieldValues);
  assert(!inspected.error, inspected.error);
  return inspected.map;
}

function expectedProjectFieldValue(field, value) {
  if (field.dataType === 'SINGLE_SELECT') {
    const option = field.options?.find((candidate) => candidate.name === value);
    assert(option?.id, `option ${value} not found for ${field.name}`);
    return {
      kind: 'SINGLE_SELECT', fieldId: field.id, optionId: option.id, name: option.name,
    };
  }
  if (field.dataType === 'NUMBER') return { kind: 'NUMBER', fieldId: field.id, number: value };
  if (field.dataType === 'DATE') return { kind: 'DATE', fieldId: field.id, date: value };
  throw new Error(`unsupported project field type ${field.dataType}`);
}

function projectFieldValuesEqual(left, right) {
  if (left?.kind !== right?.kind || left?.fieldId !== right?.fieldId) return false;
  if (left?.kind === 'SINGLE_SELECT') return left.optionId === right.optionId && left.name === right.name;
  if (left?.kind === 'NUMBER') return left.number === right.number;
  if (left?.kind === 'DATE') return left.date === right.date;
  return false;
}

function projectFieldValueStillProjecting(left, before, expected) {
  if (left === undefined || projectFieldValuesEqual(left, before)) return true;
  if (left?.kind !== 'SINGLE_SELECT'
    || left.fieldId !== expected?.fieldId
    || (left.name !== null && left.name !== undefined)) return false;
  return (before?.kind === 'SINGLE_SELECT' && left.optionId === before.optionId)
    || (expected?.kind === 'SINGLE_SELECT' && left.optionId === expected.optionId);
}

function projectFieldValueStillClearing(actual, before) {
  if (!actual || !before || actual.kind !== before.kind || actual.fieldId !== before.fieldId) return false;
  if (projectFieldValuesEqual(actual, before)) return true;
  if (actual.kind === 'SINGLE_SELECT') {
    const optionCompatible = actual.optionId === null || actual.optionId === undefined
      || actual.optionId === before.optionId;
    const nameCompatible = actual.name === null || actual.name === undefined || actual.name === before.name;
    const isPartial = actual.optionId === null || actual.optionId === undefined
      || actual.name === null || actual.name === undefined;
    return optionCompatible && nameCompatible && isPartial;
  }
  if (actual.kind === 'NUMBER') return actual.number === null || actual.number === undefined;
  if (actual.kind === 'DATE') return actual.date === null || actual.date === undefined;
  return false;
}

function projectFieldValueSummary(value) {
  if (value === undefined) return 'UNSET';
  if (value?.kind === 'SINGLE_SELECT') {
    return `SINGLE_SELECT(fieldId=${value.fieldId},optionId=${value.optionId ?? 'null'},name=${value.name ?? 'null'})`;
  }
  if (value?.kind === 'NUMBER') return `NUMBER(fieldId=${value.fieldId},number=${value.number ?? 'null'})`;
  if (value?.kind === 'DATE') return `DATE(fieldId=${value.fieldId},date=${value.date ?? 'null'})`;
  return 'UNSUPPORTED';
}

function projectFieldValueIsComplete(value) {
  if (value?.kind === 'SINGLE_SELECT') {
    return typeof value.optionId === 'string' && value.optionId.length > 0
      && typeof value.name === 'string' && value.name.length > 0;
  }
  if (value?.kind === 'NUMBER') return Number.isFinite(value.number);
  if (value?.kind === 'DATE' && typeof value.date === 'string' && DATE_PATTERN.test(value.date)) {
    const timestamp = Date.parse(`${value.date}T00:00:00.000Z`);
    return Number.isFinite(timestamp) && new Date(timestamp).toISOString().startsWith(value.date);
  }
  return false;
}

function itemProjectionIndex(items) {
  if (!Array.isArray(items)) {
    return { error: 'Project item projection is not an array', reason: 'CONTENT_IDENTITY_DRIFT' };
  }
  const byItemId = new Map();
  const byContentId = new Map();
  for (const item of items) {
    if (!item?.id) return { error: 'Project item without identity', reason: 'CONTENT_IDENTITY_DRIFT' };
    if (byItemId.has(item.id)) {
      return { error: `duplicate Project item identity ${item.id}`, reason: 'DUPLICATE_CONTENT' };
    }
    byItemId.set(item.id, item);
    const contentId = item.content?.id;
    if (!contentId) continue;
    if (byContentId.has(contentId)) {
      return { error: `duplicate Project item content ${contentId}`, reason: 'DUPLICATE_CONTENT' };
    }
    byContentId.set(contentId, item);
  }
  return { byItemId, byContentId };
}

export function validateProjectItemPreconditions({ items, contents, repository, repositoryAliases = [] }) {
  assert(Array.isArray(contents), 'repository contents must be an array');
  const indexed = itemProjectionIndex(items);
  assert(!indexed.error, indexed.error);
  const contentsById = new Map(contents.map((content) => [content.id, content]));
  assert(contentsById.size === contents.length, 'repository content identities must be unique');
  for (const item of items) {
    const projected = item.content;
    assert(projected?.id && projected?.__typename && projected?.number
      && projected?.repository?.nameWithOwner,
    `Project item ${item.id} has incomplete repository content identity`);
    assert(isConfiguredRepository(projected.repository.nameWithOwner, repository, repositoryAliases),
      `Project item ${item.id} belongs to unexpected repository ${projected.repository.nameWithOwner}`);
    const expected = contentsById.get(projected.id);
    assert(expected, `Project item ${item.id} references unplanned repository content ${projected.id}`);
    assert(projected.__typename === expected.kind && projected.number === expected.number,
      `Project item ${item.id} content identity does not match ${expected.kind} #${expected.number}`);
  }
  return { projectItems: items.length, repositoryContents: contents.length };
}

function expectedItemSummary(content, archived, fieldCount = undefined) {
  return `${content.kind} #${content.number} item=${archived ? 'archived' : 'active'}${fieldCount === undefined ? '' : ` managedFields=${fieldCount}`}`;
}

function observedItemSummary(item) {
  if (!item) return 'item=absent';
  const content = item.content;
  const identity = content?.__typename && content?.number
    ? `${content.__typename} #${content.number}`
    : 'content=partial';
  const archive = typeof item.isArchived === 'boolean' ? (item.isArchived ? 'archived' : 'active') : 'archive=partial';
  const fieldCount = Array.isArray(item.fieldValues?.nodes) ? item.fieldValues.nodes.length : 0;
  return `${identity} item=${archive} projectedFields=${fieldCount}`;
}

function inspectTargetItemIdentity({
  items,
  targetItemId,
  content,
  repository,
  repositoryAliases = [],
  allowMissing,
  knownItemsById,
  projectionLagEligibleItemIds,
}) {
  const indexed = itemProjectionIndex(items);
  if (indexed.error) {
    return {
      result: verificationResult(
        PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
        indexed.reason,
        expectedItemSummary(content, false),
        indexed.error,
      ),
    };
  }
  let relatedProjectionDelay;
  if (knownItemsById) {
    for (const item of items) {
      const expectedIdentity = knownItemsById.get(item.id);
      if (!expectedIdentity) {
        return {
          result: verificationResult(
            PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
            'UNEXPECTED_ITEM',
            'only captured or mutation-returned Project item identities',
            `unexpected Project item=${item.id}`,
          ),
        };
      }
      if (item.id === targetItemId) continue;
      const projectedRepository = item.content?.repository?.nameWithOwner;
      if ((item.content?.id !== undefined && item.content.id !== expectedIdentity.contentId)
        || (item.content?.__typename !== undefined && item.content.__typename !== expectedIdentity.kind)
        || (item.content?.number !== undefined && item.content.number !== expectedIdentity.number)
        || (projectedRepository !== undefined
          && !isConfiguredRepository(projectedRepository, repository, repositoryAliases)
          && projectedRepository !== expectedIdentity.repository)) {
        return {
          result: verificationResult(
            PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
            'CONTENT_IDENTITY_DRIFT',
            `Project item=${item.id} captured repository identity`,
            `Project item=${item.id} identity contradicts the captured content`,
          ),
        };
      }
      if (!item.content?.id || !item.content?.__typename || !item.content?.number || !projectedRepository) {
        if (!projectionLagEligibleItemIds?.has(item.id)) {
          return {
            result: verificationResult(
              PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
              'CONTENT_IDENTITY_DRIFT',
              `Project item=${item.id} captured repository identity`,
              `Project item=${item.id} identity is partial`,
            ),
          };
        }
        relatedProjectionDelay ??= verificationResult(
          PROJECT_VERIFICATION_OUTCOMES.NOT_YET_CONVERGED,
          'ITEM_NOT_VISIBLE',
          `Project item=${item.id} complete mutation-created identity`,
          `Project item=${item.id} identity is partially projected`,
        );
      }
    }
    for (const itemId of knownItemsById.keys()) {
      if (indexed.byItemId.has(itemId) || (allowMissing && itemId === targetItemId)) continue;
      if (projectionLagEligibleItemIds?.has(itemId)) {
        relatedProjectionDelay ??= verificationResult(
          PROJECT_VERIFICATION_OUTCOMES.NOT_YET_CONVERGED,
          'ITEM_NOT_VISIBLE',
          `Project item=${itemId} mutation-created identity remains visible`,
          `Project item=${itemId} is temporarily absent`,
        );
        continue;
      }
      return {
        result: verificationResult(
          PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
          'ITEM_DELETED',
          `Project item=${itemId} remains present`,
          `Project item=${itemId} is absent`,
        ),
      };
    }
  }
  const target = indexed.byItemId.get(targetItemId);
  const byContent = indexed.byContentId.get(content.id);
  const targetMayLag = allowMissing || projectionLagEligibleItemIds?.has(targetItemId);
  if (!target) {
    if (byContent) {
      return {
        result: verificationResult(
          PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
          'CONTENT_IDENTITY_DRIFT',
          `${content.kind} #${content.number} Project item=${targetItemId}`,
          `${content.kind} #${content.number} projected under a different item identity`,
        ),
      };
    }
    return {
      result: verificationResult(
        targetMayLag ? PROJECT_VERIFICATION_OUTCOMES.NOT_YET_CONVERGED : PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
        targetMayLag ? 'ITEM_NOT_VISIBLE' : 'ITEM_DELETED',
        `${content.kind} #${content.number} Project item=${targetItemId}`,
        'item=absent',
      ),
    };
  }
  if (byContent && byContent.id !== target.id) {
    return {
      result: verificationResult(
        PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
        'DUPLICATE_CONTENT',
        `${content.kind} #${content.number} exactly one Project item`,
        `${content.kind} #${content.number} has conflicting item identities`,
      ),
    };
  }
  const projectedContent = target.content;
  const projectedRepository = projectedContent?.repository?.nameWithOwner;
  if ((projectedContent?.id !== undefined && projectedContent.id !== content.id)
    || (projectedContent?.__typename !== undefined && projectedContent.__typename !== content.kind)
    || (projectedContent?.number !== undefined && projectedContent.number !== content.number)
    || (projectedRepository !== undefined
      && !isConfiguredRepository(projectedRepository, repository, repositoryAliases))) {
    return {
      result: verificationResult(
        PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
        'CONTENT_IDENTITY_DRIFT',
        `${content.kind} #${content.number} repository=${repository}`,
        observedItemSummary(target),
      ),
    };
  }
  if (!projectedContent?.id || !projectedContent?.__typename || !projectedContent?.number
    || !projectedRepository) {
    return {
      result: verificationResult(
        targetMayLag ? PROJECT_VERIFICATION_OUTCOMES.NOT_YET_CONVERGED : PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
        targetMayLag ? 'ITEM_NOT_VISIBLE' : 'CONTENT_IDENTITY_DRIFT',
        `${content.kind} #${content.number} complete repository identity`,
        observedItemSummary(target),
      ),
    };
  }
  return { target, relatedProjectionDelay };
}

export function evaluateItemLifecycleProjection({
  items,
  targetItemId,
  content,
  repository,
  repositoryAliases = [],
  expectedArchived,
  beforeArchived,
  allowMissing = false,
  knownItemsById,
  projectionLagEligibleItemIds,
}) {
  const inspected = inspectTargetItemIdentity({
    items,
    targetItemId,
    content,
    repository,
    repositoryAliases,
    allowMissing,
    knownItemsById,
    projectionLagEligibleItemIds,
  });
  if (inspected.result) return inspected.result;
  const target = inspected.target;
  const expected = expectedItemSummary(content, expectedArchived);
  if (target.isArchived === expectedArchived) {
    if (inspected.relatedProjectionDelay) return inspected.relatedProjectionDelay;
    return verificationResult(
      PROJECT_VERIFICATION_OUTCOMES.CONVERGED,
      'EXACT_STATE_VISIBLE',
      expected,
      observedItemSummary(target),
      { itemId: target.id },
    );
  }
  if (target.isArchived === undefined || target.isArchived === null
    || (typeof beforeArchived === 'boolean' && target.isArchived === beforeArchived)) {
    return verificationResult(
      PROJECT_VERIFICATION_OUTCOMES.NOT_YET_CONVERGED,
      'ARCHIVE_STATE_NOT_VISIBLE',
      expected,
      observedItemSummary(target),
    );
  }
  return verificationResult(
    PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
    'UNEXPECTED_LIFECYCLE_STATE',
    expected,
    observedItemSummary(target),
  );
}

export function evaluateItemFieldProjection({
  items,
  targetItemId,
  content,
  repository,
  repositoryAliases = [],
  beforeValues,
  expectedValues,
  allowMissing = false,
  knownItemsById,
  projectionLagEligibleItemIds,
}) {
  const inspected = inspectTargetItemIdentity({
    items,
    targetItemId,
    content,
    repository,
    repositoryAliases,
    allowMissing,
    knownItemsById,
    projectionLagEligibleItemIds,
  });
  if (inspected.result) return inspected.result;
  const target = inspected.target;
  if (target.isArchived === undefined || target.isArchived === null) {
    return verificationResult(
      PROJECT_VERIFICATION_OUTCOMES.NOT_YET_CONVERGED,
      'ARCHIVE_STATE_NOT_VISIBLE',
      expectedItemSummary(content, false, expectedValues.size),
      observedItemSummary(target),
    );
  }
  if (target.isArchived !== false) {
    return verificationResult(
      PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
      'UNEXPECTED_LIFECYCLE_STATE',
      expectedItemSummary(content, false, expectedValues.size),
      observedItemSummary(target),
    );
  }
  const inspectedValues = inspectCurrentValueMap(target.fieldValues);
  if (inspectedValues.error) {
    return verificationResult(
      PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
      'UNEXPECTED_FIELD_VALUE',
      expectedItemSummary(content, false, expectedValues.size),
      inspectedValues.error,
    );
  }
  const actualValues = inspectedValues.map;
  for (const fieldName of actualValues.keys()) {
    if (!expectedValues.has(fieldName)) {
      return verificationResult(
        PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
        'UNEXPECTED_FIELD_VALUE',
        `${content.kind} #${content.number} unchanged unrelated fields`,
        `unexpected field=${fieldName}`,
      );
    }
  }
  let delayedField;
  for (const [fieldName, expectedValue] of expectedValues) {
    const actualValue = actualValues.get(fieldName);
    if (projectFieldValuesEqual(actualValue, expectedValue)) continue;
    const beforeValue = beforeValues.get(fieldName);
    if (projectFieldValueStillProjecting(actualValue, beforeValue, expectedValue)) {
      delayedField ??= fieldName;
      continue;
    }
    return verificationResult(
      PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
      'UNEXPECTED_FIELD_VALUE',
      `${content.kind} #${content.number} field=${fieldName} expected configured value`,
      `${content.kind} #${content.number} field=${fieldName} has contradictory value`,
    );
  }
  if (delayedField) {
    return verificationResult(
      PROJECT_VERIFICATION_OUTCOMES.NOT_YET_CONVERGED,
      'FIELD_NOT_VISIBLE',
      expectedItemSummary(content, false, expectedValues.size),
      `${observedItemSummary(target)} delayedField=${delayedField}`,
    );
  }
  if (inspected.relatedProjectionDelay) return inspected.relatedProjectionDelay;
  return verificationResult(
    PROJECT_VERIFICATION_OUTCOMES.CONVERGED,
    'EXACT_STATE_VISIBLE',
    expectedItemSummary(content, false, expectedValues.size),
    observedItemSummary(target),
    { itemId: target.id },
  );
}

export function evaluateItemFieldClearProjection({
  items,
  targetItemId,
  content,
  repository,
  repositoryAliases = [],
  beforeValues,
  field,
  knownItemsById,
  projectionLagEligibleItemIds,
}) {
  const clearProjectionLagEligibleItemIds = projectionLagEligibleItemIds === undefined
    ? undefined
    : new Set(projectionLagEligibleItemIds);
  clearProjectionLagEligibleItemIds?.delete(targetItemId);
  const inspected = inspectTargetItemIdentity({
    items,
    targetItemId,
    content,
    repository,
    repositoryAliases,
    allowMissing: false,
    knownItemsById,
    projectionLagEligibleItemIds: clearProjectionLagEligibleItemIds,
  });
  if (inspected.result) return inspected.result;
  const target = inspected.target;
  const expected = `${content.kind} #${content.number} field=${field.name} UNSET`;
  if (target.isArchived === undefined || target.isArchived === null) {
    return verificationResult(
      PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
      'UNEXPECTED_LIFECYCLE_STATE',
      expected,
      observedItemSummary(target),
    );
  }
  if (target.isArchived !== false) {
    return verificationResult(
      PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
      'UNEXPECTED_LIFECYCLE_STATE',
      expected,
      observedItemSummary(target),
    );
  }
  const beforeTarget = beforeValues.get(field.name);
  if (!beforeTarget || beforeTarget.fieldId !== field.id) {
    return verificationResult(
      PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
      'FIELD_SCHEMA_DRIFT',
      expected,
      `${content.kind} #${content.number} clear contract lacks the captured target field identity`,
    );
  }
  const inspectedValues = inspectCurrentValueMap(target.fieldValues);
  if (inspectedValues.error) {
    return verificationResult(
      PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
      'UNEXPECTED_FIELD_VALUE',
      expected,
      inspectedValues.error,
    );
  }
  const actualValues = inspectedValues.map;
  for (const [fieldName, actualValue] of actualValues) {
    if (fieldName === field.name) continue;
    const capturedValue = beforeValues.get(fieldName);
    if (!capturedValue || !projectFieldValuesEqual(actualValue, capturedValue)) {
      return verificationResult(
        PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
        'UNEXPECTED_FIELD_VALUE',
        `${content.kind} #${content.number} unrelated supported fields unchanged`,
        `${content.kind} #${content.number} field=${fieldName} appeared or changed during clear`,
      );
    }
  }
  for (const [fieldName, capturedValue] of beforeValues) {
    if (fieldName === field.name) continue;
    if (!projectFieldValuesEqual(actualValues.get(fieldName), capturedValue)) {
      return verificationResult(
        PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
        'UNEXPECTED_FIELD_VALUE',
        `${content.kind} #${content.number} unrelated supported fields unchanged`,
        `${content.kind} #${content.number} field=${fieldName} disappeared or changed during clear`,
      );
    }
  }
  const actualTarget = actualValues.get(field.name);
  if (actualTarget !== undefined) {
    if (actualTarget.fieldId !== field.id || !projectFieldValueStillClearing(actualTarget, beforeTarget)) {
      return verificationResult(
        PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
        'UNEXPECTED_FIELD_VALUE',
        expected,
        `${content.kind} #${content.number} field=${field.name} actual=${projectFieldValueSummary(actualTarget)}`,
      );
    }
    return verificationResult(
      PROJECT_VERIFICATION_OUTCOMES.NOT_YET_CONVERGED,
      'FIELD_NOT_VISIBLE',
      expected,
      `${observedItemSummary(target)} field=${field.name} actual=${projectFieldValueSummary(actualTarget)}`,
    );
  }
  if (inspected.relatedProjectionDelay) return inspected.relatedProjectionDelay;
  return verificationResult(
    PROJECT_VERIFICATION_OUTCOMES.CONVERGED,
    'EXACT_STATE_VISIBLE',
    expected,
    `${observedItemSummary(target)} field=${field.name} absent`,
    { itemId: target.id },
  );
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

async function readProjectProjection(api, projectId) {
  const verification = await api.request(`
    query VerifyRoadmapProject($projectId: ID!) {
      node(id: $projectId) {
        ... on ProjectV2 {
          id number title closed public shortDescription readme
          repositories(first: 100) { nodes { id nameWithOwner } }
        }
      }
    }
  `, { projectId }, 'verify project');
  return verification.node;
}

function evaluateProjectProjection({
  observed,
  before,
  config,
  repository,
  projectMutated,
  linkMutated,
  repositoryLinkExpected,
}) {
  const expectedSummary = `Project #${before.number} configured metadata and repository link`;
  if (!observed || observed.id !== before.id || observed.number !== before.number) {
    return verificationResult(
      PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
      'PROJECT_SCHEMA_DRIFT',
      expectedSummary,
      observed ? 'Project identity changed' : 'Project missing',
    );
  }
  const expectedProperties = {
    title: config.project.title,
    shortDescription: config.project.shortDescription,
    readme: config.project.readme,
    closed: false,
    public: false,
  };
  let delayed = false;
  for (const [name, expected] of Object.entries(expectedProperties)) {
    const actual = observed[name];
    if (actual === expected) continue;
    if ((projectMutated && (actual === before[name] || actual === undefined || actual === null))
      || (linkMutated && (actual === undefined || actual === null))) {
      delayed = true;
      continue;
    }
    return verificationResult(
      PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
      'PROJECT_SCHEMA_DRIFT',
      expectedSummary,
      `Project property ${name} has contradictory value`,
    );
  }

  const expectedRepositories = new Map((before.repositories?.nodes ?? []).map((node) => [node.id, node.nameWithOwner]));
  if (repositoryLinkExpected) expectedRepositories.set(repository.id, repository.nameWithOwner);
  const observedRepositories = observed.repositories?.nodes;
  if (!Array.isArray(observedRepositories)) {
    if (projectMutated || linkMutated) delayed = true;
    else {
      return verificationResult(
        PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
        'PROJECT_SCHEMA_DRIFT',
        expectedSummary,
        'Project repository links missing',
      );
    }
  } else {
    const seen = new Set();
    for (const node of observedRepositories) {
      if (!node?.id || seen.has(node.id)) {
        return verificationResult(
          PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
          'PROJECT_SCHEMA_DRIFT',
          expectedSummary,
          'Project repository links are duplicate or incomplete',
        );
      }
      seen.add(node.id);
      const expectedName = expectedRepositories.get(node.id);
      if (!expectedName || node.nameWithOwner !== expectedName) {
        return verificationResult(
          PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
          'PROJECT_SCHEMA_DRIFT',
          expectedSummary,
          'Project repository identity drifted',
        );
      }
    }
    for (const expectedId of expectedRepositories.keys()) {
      if (seen.has(expectedId)) continue;
      if (linkMutated && expectedId === repository.id) delayed = true;
      else {
        return verificationResult(
          PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
          'PROJECT_SCHEMA_DRIFT',
          expectedSummary,
          'Expected repository link disappeared',
        );
      }
    }
  }
  if (delayed) {
    return verificationResult(
      PROJECT_VERIFICATION_OUTCOMES.NOT_YET_CONVERGED,
      'PROJECT_METADATA_NOT_VISIBLE',
      expectedSummary,
      'Project still exposes captured pre-mutation or partial metadata',
    );
  }
  return verificationResult(
    PROJECT_VERIFICATION_OUTCOMES.CONVERGED,
    'EXACT_STATE_VISIBLE',
    expectedSummary,
    'Project metadata and repository link converged',
  );
}

export async function ensureProject(api, config, context, verificationOptions = {}) {
  const project = context.project;
  assert(project, `reviewed Project ${config.project.title} is unavailable`);
  let updated = 0;
  let repositoryLinks = 0;
  let reconciled = project;
  assert(Array.isArray(project.repositories?.nodes), 'Project repository links were unavailable before reconciliation');
  const alreadyLinked = project.repositories.nodes.some((repo) => repo.id === context.repository.id);

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
    const metadataProjection = await verifyProjectProjection({
      ...verificationOptions,
      operationType: 'PROJECT_METADATA',
      identity: `Project #${project.number}`,
      read: () => readProjectProjection(api, project.id),
      classify: (observed) => evaluateProjectProjection({
        observed,
        before: project,
        config,
        repository: context.repository,
        projectMutated: true,
        linkMutated: false,
        repositoryLinkExpected: alreadyLinked,
      }),
    });
    reconciled = metadataProjection.snapshot;
  }

  if (!alreadyLinked) {
    await api.request(`
      mutation LinkRoadmapProject($input: LinkProjectV2ToRepositoryInput!) {
        linkProjectV2ToRepository(input: $input) { repository { id nameWithOwner } }
      }
    `, { input: { projectId: project.id, repositoryId: context.repository.id } }, 'link project to repository');
    repositoryLinks += 1;
    const linkProjection = await verifyProjectProjection({
      ...verificationOptions,
      operationType: 'LINK_PROJECT_REPOSITORY',
      identity: `Project #${project.number}`,
      read: () => readProjectProjection(api, project.id),
      classify: (observed) => evaluateProjectProjection({
        observed,
        before: reconciled,
        config,
        repository: context.repository,
        projectMutated: false,
        linkMutated: true,
        repositoryLinkExpected: true,
      }),
    });
    reconciled = linkProjection.snapshot;
  }
  if (!projectDrift && alreadyLinked) {
    const unchangedProjection = await verifyProjectProjection({
      ...verificationOptions,
      operationType: 'VERIFY_PROJECT',
      identity: `Project #${project.number}`,
      read: () => readProjectProjection(api, project.id),
      classify: (observed) => evaluateProjectProjection({
        observed,
        before: project,
        config,
        repository: context.repository,
        projectMutated: false,
        linkMutated: false,
        repositoryLinkExpected: true,
      }),
    });
    reconciled = unchangedProjection.snapshot;
  }
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

export function validateFieldPreconditions(config, existing) {
  const inspected = inspectFieldDefinitions(existing);
  assert(!inspected.error, inspected.error);
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

function inspectFieldDefinitions(fields) {
  if (!Array.isArray(fields)) return { error: 'Project field projection is not an array' };
  const byId = new Map();
  const byName = new Map();
  for (const field of fields) {
    if (!field?.id || !field?.name) return { error: 'Project field identity is incomplete' };
    if (byId.has(field.id) || byName.has(field.name)) return { error: 'Project field identity is duplicated' };
    byId.set(field.id, field);
    byName.set(field.name, field);
  }
  return { byId, byName };
}

function fieldOptionExact(desired, expectedOption, observed) {
  if (!observed?.id || observed.name !== desired.name
    || observed.color !== desired.color || observed.description !== desired.description) return false;
  const expectedId = expectedOption?.id ?? desired.preserveId;
  return expectedId === undefined || observed.id === expectedId;
}

function fieldOptionsExact(desired, expectedOptions, observedOptions) {
  return Array.isArray(observedOptions)
    && observedOptions.length === desired.options.length
    && desired.options.every((option, index) => fieldOptionExact(option, expectedOptions?.[index], observedOptions[index]));
}

function fieldOptionsCompatible(desired, expectedOptions, before, observedOptions) {
  if (!Array.isArray(observedOptions)) return true;
  const beforeOptions = before?.options ?? [];
  const seenIds = new Set();
  const seenNames = new Set();
  const claimedDesiredIndexes = new Set();
  for (const observed of observedOptions) {
    if (!observed?.id || !observed?.name || seenIds.has(observed.id) || seenNames.has(observed.name)) return false;
    seenIds.add(observed.id);
    seenNames.add(observed.name);
    const desiredIndex = desired.options.findIndex((option, index) => {
      if (claimedDesiredIndexes.has(index)) return false;
      const acceptedNames = [option.name, ...(option.aliases ?? [])];
      const expectedId = expectedOptions?.[index]?.id ?? option.preserveId;
      return acceptedNames.includes(observed.name)
        && (expectedId === undefined || expectedId === observed.id)
        && [option.color, beforeOptions.find((candidate) => candidate.id === observed.id)?.color].includes(observed.color)
        && [option.description, beforeOptions.find((candidate) => candidate.id === observed.id)?.description]
          .includes(observed.description);
    });
    const beforeMatch = beforeOptions.some((option) => valuesEqual(option, observed));
    if (desiredIndex >= 0) claimedDesiredIndexes.add(desiredIndex);
    else if (!beforeMatch) return false;
  }
  return true;
}

function evaluateFieldProjection({
  fields,
  beforeFields,
  desired,
  expectedOptions,
  expectedFieldId,
  beforeField,
  created,
}) {
  const expectedSummary = `field=${desired.name} schema=configured`;
  const inspected = inspectFieldDefinitions(fields);
  if (inspected.error) {
    return verificationResult(
      PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
      'FIELD_SCHEMA_DRIFT',
      expectedSummary,
      inspected.error,
    );
  }
  const beforeInspected = inspectFieldDefinitions(beforeFields);
  assert(!beforeInspected.error, beforeInspected.error);
  for (const [fieldId, beforeDefinition] of beforeInspected.byId) {
    const projected = inspected.byId.get(fieldId);
    if (!projected || projected.name !== beforeDefinition.name) {
      return verificationResult(
        PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
        'FIELD_SCHEMA_DRIFT',
        expectedSummary,
        `existing field=${beforeDefinition.name} disappeared or changed identity`,
      );
    }
    if (fieldId !== beforeField?.id && !valuesEqual(projected, beforeDefinition)) {
      return verificationResult(
        PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
        'FIELD_SCHEMA_DRIFT',
        expectedSummary,
        `unrelated field=${beforeDefinition.name} changed`,
      );
    }
  }
  for (const [fieldId, projected] of inspected.byId) {
    if (beforeInspected.byId.has(fieldId)) continue;
    if (!created || fieldId !== expectedFieldId || projected.name !== desired.name) {
      return verificationResult(
        PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
        'FIELD_SCHEMA_DRIFT',
        expectedSummary,
        `unexpected field=${projected.name}`,
      );
    }
  }

  const observed = inspected.byName.get(desired.name);
  if (!observed) {
    return verificationResult(
      created ? PROJECT_VERIFICATION_OUTCOMES.NOT_YET_CONVERGED : PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
      created ? 'FIELD_SCHEMA_NOT_VISIBLE' : 'FIELD_SCHEMA_DRIFT',
      expectedSummary,
      'field=absent',
    );
  }
  if (observed.dataType !== desired.dataType) {
    return verificationResult(
      PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
      'FIELD_SCHEMA_DRIFT',
      expectedSummary,
      `field=${desired.name} has contradictory data type`,
    );
  }
  if (desired.dataType !== 'SINGLE_SELECT' || fieldOptionsExact(desired, expectedOptions, observed.options)) {
    return verificationResult(
      PROJECT_VERIFICATION_OUTCOMES.CONVERGED,
      'EXACT_STATE_VISIBLE',
      expectedSummary,
      `field=${desired.name} schema converged`,
    );
  }
  if ((beforeField && valuesEqual(observed.options ?? [], beforeField.options ?? []))
    || fieldOptionsCompatible(desired, expectedOptions, beforeField, observed.options)) {
    return verificationResult(
      PROJECT_VERIFICATION_OUTCOMES.NOT_YET_CONVERGED,
      'FIELD_SCHEMA_NOT_VISIBLE',
      expectedSummary,
      `field=${desired.name} exposes captured or partial options`,
    );
  }
  return verificationResult(
    PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
    'FIELD_SCHEMA_DRIFT',
    expectedSummary,
    `field=${desired.name} option identity drifted`,
  );
}

export async function ensureFields(api, config, projectId, initialExisting, verificationOptions = {}) {
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
      const data = await api.request(`
        mutation CreateRoadmapField($input: CreateProjectV2FieldInput!) {
          createProjectV2Field(input: $input) { projectV2Field { ... on ProjectV2FieldCommon { id name dataType } } }
        }
      `, { input }, `create field ${desired.name}`);
      const createdFieldId = data.createProjectV2Field?.projectV2Field?.id;
      assert(createdFieldId, `create field ${desired.name} returned no Project field identity`);
      created += 1;
      console.log(JSON.stringify({ event: 'field_created', name: desired.name }));
      const projection = await verifyProjectProjection({
        ...verificationOptions,
        operationType: 'CREATE_FIELD',
        identity: `Field ${desired.name}`,
        read: () => listProjectFields(api, projectId),
        classify: (fields) => evaluateFieldProjection({
          fields,
          beforeFields: existing,
          desired,
          expectedOptions: input.singleSelectOptions,
          expectedFieldId: createdFieldId,
          beforeField: undefined,
          created: true,
        }),
      });
      existing = projection.snapshot;
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
        const projection = await verifyProjectProjection({
          ...verificationOptions,
          operationType: 'UPDATE_FIELD_OPTIONS',
          identity: `Field ${desired.name}`,
          read: () => listProjectFields(api, projectId),
          classify: (fields) => evaluateFieldProjection({
            fields,
            beforeFields: existing,
            desired,
            expectedOptions: options,
            expectedFieldId: field.id,
            beforeField: field,
            created: false,
          }),
        });
        existing = projection.snapshot;
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

function inspectViewDefinitions(views) {
  if (!Array.isArray(views)) return { error: 'Project view projection is not an array' };
  const byId = new Map();
  const byName = new Map();
  for (const view of views) {
    if (!view?.id || !view?.name) return { error: 'Project view identity is incomplete' };
    if (byId.has(view.id) || byName.has(view.name)) return { error: 'Project view identity is duplicated' };
    byId.set(view.id, view);
    byName.set(view.name, view);
  }
  return { byId, byName };
}

function evaluateViewProjection({
  views,
  beforeViews,
  targetViewId,
  desired,
  apiLayout,
  visibleFieldIds,
  beforeView,
  created,
  visibilityOnly,
}) {
  const expectedSummary = `view=${desired.name} ${visibilityOnly ? 'visible' : 'configured'}`;
  const inspected = inspectViewDefinitions(views);
  if (inspected.error) {
    return verificationResult(
      PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
      'VIEW_SCHEMA_DRIFT',
      expectedSummary,
      inspected.error,
    );
  }
  const beforeInspected = inspectViewDefinitions(beforeViews);
  assert(!beforeInspected.error, beforeInspected.error);
  for (const [viewId, prior] of beforeInspected.byId) {
    const projected = inspected.byId.get(viewId);
    if (!projected) {
      return verificationResult(
        PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
        'VIEW_SCHEMA_DRIFT',
        expectedSummary,
        `existing view=${prior.name} disappeared`,
      );
    }
    if (viewId !== targetViewId && !valuesEqual(projected, prior)) {
      return verificationResult(
        PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
        'VIEW_SCHEMA_DRIFT',
        expectedSummary,
        `unrelated view=${prior.name} changed`,
      );
    }
  }
  for (const [viewId, projected] of inspected.byId) {
    if (beforeInspected.byId.has(viewId)) continue;
    if (!created || viewId !== targetViewId) {
      return verificationResult(
        PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
        'VIEW_SCHEMA_DRIFT',
        expectedSummary,
        `unexpected view=${projected.name}`,
      );
    }
  }
  const observed = inspected.byId.get(targetViewId);
  if (!observed) {
    return verificationResult(
      created ? PROJECT_VERIFICATION_OUTCOMES.NOT_YET_CONVERGED : PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
      created ? 'VIEW_NOT_VISIBLE' : 'VIEW_SCHEMA_DRIFT',
      expectedSummary,
      'view=absent',
    );
  }
  const identityValues = [
    ['name', desired.name],
    ['layout', apiLayout],
  ];
  let delayed = false;
  for (const [name, expected] of identityValues) {
    const actual = observed[name];
    if (actual === expected) continue;
    if (actual === undefined || actual === null || actual === beforeView?.[name]) {
      delayed = true;
      continue;
    }
    return verificationResult(
      PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
      'VIEW_SCHEMA_DRIFT',
      expectedSummary,
      `view=${desired.name} ${name} drifted`,
    );
  }
  if (visibilityOnly) {
    const actualVisibleFieldIds = (observed.configuration?.visibleFields?.nodes ?? []).map((field) => field?.id);
    if (!valuesEqual(actualVisibleFieldIds, visibleFieldIds)) {
      const compatiblePartial = actualVisibleFieldIds.length < visibleFieldIds.length
        && actualVisibleFieldIds.every((id) => id && visibleFieldIds.includes(id))
        && new Set(actualVisibleFieldIds).size === actualVisibleFieldIds.length;
      if (compatiblePartial) delayed = true;
      else {
        return verificationResult(
          PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
          'VIEW_SCHEMA_DRIFT',
          expectedSummary,
          `view=${desired.name} create configuration drifted`,
        );
      }
    }
  } else {
    const expectedFilter = desired.filter ?? '';
    const actualFilter = observed.filter ?? '';
    if (actualFilter !== expectedFilter) {
      if (actualFilter === (beforeView?.filter ?? '') || observed.filter === undefined) delayed = true;
      else {
        return verificationResult(
          PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
          'VIEW_SCHEMA_DRIFT',
          expectedSummary,
          `view=${desired.name} filter drifted`,
        );
      }
    }
    const actualVisibleFieldIds = (observed.configuration?.visibleFields?.nodes ?? []).map((field) => field?.id);
    const beforeVisibleFieldIds = (beforeView?.configuration?.visibleFields?.nodes ?? []).map((field) => field?.id);
    if (!valuesEqual(actualVisibleFieldIds, visibleFieldIds)) {
      const compatible = actualVisibleFieldIds.every((id) => id
        && (visibleFieldIds.includes(id) || beforeVisibleFieldIds.includes(id)))
        && new Set(actualVisibleFieldIds).size === actualVisibleFieldIds.length;
      if (valuesEqual(actualVisibleFieldIds, beforeVisibleFieldIds) || compatible) delayed = true;
      else {
        return verificationResult(
          PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
          'VIEW_SCHEMA_DRIFT',
          expectedSummary,
          `view=${desired.name} visible-field identity drifted`,
        );
      }
    }
  }
  if (delayed) {
    return verificationResult(
      PROJECT_VERIFICATION_OUTCOMES.NOT_YET_CONVERGED,
      visibilityOnly ? 'VIEW_NOT_VISIBLE' : 'PROJECT_METADATA_NOT_VISIBLE',
      expectedSummary,
      `view=${desired.name} exposes captured or partial projection`,
    );
  }
  return verificationResult(
    PROJECT_VERIFICATION_OUTCOMES.CONVERGED,
    'EXACT_STATE_VISIBLE',
    expectedSummary,
    `view=${desired.name} converged`,
  );
}

export async function ensureViews(
  api,
  config,
  projectId,
  fieldsByName,
  initialExisting,
  verificationOptions = {},
) {
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
      assert(view?.id, `create view ${desired.name} returned no Project view identity`);
      created += 1;
      console.log(JSON.stringify({ event: 'view_created', name: desired.name, layout: desired.layout }));
      const creationProjection = await verifyProjectProjection({
        ...verificationOptions,
        operationType: 'CREATE_VIEW',
        identity: `View ${desired.name}`,
        read: () => listProjectViews(api, projectId),
        classify: (views) => evaluateViewProjection({
          views,
          beforeViews: existing,
          targetViewId: view.id,
          desired,
          apiLayout,
          visibleFieldIds,
          beforeView: undefined,
          created: true,
          visibilityOnly: true,
        }),
      });
      existing = creationProjection.snapshot;
      view = existing.find((candidate) => candidate.id === view.id);
      assert(view, `created view ${desired.name} was unavailable after verified projection`);
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
      const beforeUpdateViews = existing;
      const beforeUpdateView = view;
      await api.request(`
        mutation UpdateRoadmapView($input: UpdateProjectV2ViewInput!) {
          updateProjectV2View(input: $input) { projectV2View { id name layout filter } }
        }
      `, { input: update }, `update view ${desired.name}`);
      updated += 1;
      const updateProjection = await verifyProjectProjection({
        ...verificationOptions,
        operationType: 'UPDATE_VIEW',
        identity: `View ${desired.name}`,
        read: () => listProjectViews(api, projectId),
        classify: (views) => evaluateViewProjection({
          views,
          beforeViews: beforeUpdateViews,
          targetViewId: view.id,
          desired,
          apiLayout,
          visibleFieldIds,
          beforeView: beforeUpdateView,
          created: false,
          visibilityOnly: false,
        }),
      });
      existing = updateProjection.snapshot;
    }
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
                  pageInfo { hasNextPage endCursor }
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

function managedMetadataValue(metadata, fieldName) {
  const metadataKey = MANAGED_FIELD_METADATA_KEYS.get(fieldName);
  assert(metadataKey, `configured project field ${fieldName} has no managed metadata mapping`);
  return metadata[metadataKey];
}

export function planManagedFieldReconciliation({
  configuredFields,
  fieldsByName,
  metadata,
  currentValues,
}) {
  assert(Array.isArray(configuredFields), 'configuredFields must be an array');
  assert(fieldsByName instanceof Map, 'fieldsByName must be a Map');
  assert(isObject(metadata), 'metadata must be an object');
  assert(currentValues instanceof Map, 'currentValues must be a Map');
  return configuredFields.map((configuredField) => {
    const fieldName = configuredField?.name;
    assert(typeof fieldName === 'string' && fieldName.length > 0, 'configured field name is required');
    const field = fieldsByName.get(fieldName);
    assert(field?.id, `project field ${fieldName} is missing`);
    assert(ALLOWED_FIELD_TYPES.has(field.dataType), `unsupported project field type ${field.dataType}`);
    assert(field.dataType === configuredField.dataType,
      `configured project field ${fieldName} type does not match the Project schema`);
    const currentValue = currentValues.get(fieldName);
    if (currentValue !== undefined) {
      assert(currentValue.fieldId === field.id,
        `project field ${fieldName} value belongs to unexpected field identity ${currentValue.fieldId}`);
      assert(currentValue.kind === field.dataType,
        `project field ${fieldName} value has unexpected type ${currentValue.kind}`);
      assert(projectFieldValueIsComplete(currentValue),
        `project field ${fieldName} has an incomplete pre-mutation value projection`);
      if (currentValue.kind === 'SINGLE_SELECT') {
        assert(field.options?.some((option) => option.id === currentValue.optionId),
          `project field ${fieldName} has an unknown option identity`);
      }
    }
    const desiredValue = managedMetadataValue(metadata, fieldName);
    if (desiredValue === undefined) {
      return Object.freeze({
        fieldName,
        fieldId: field.id,
        field,
        action: currentValue === undefined ? MANAGED_FIELD_ACTIONS.NO_OP : MANAGED_FIELD_ACTIONS.CLEAR,
        currentValue: currentValue ?? PROJECT_FIELD_UNSET,
        expectedValue: PROJECT_FIELD_UNSET,
        desiredValue: undefined,
        reason: currentValue === undefined ? 'ALREADY_UNSET' : 'STALE_MANAGED_VALUE',
      });
    }
    const expectedValue = expectedProjectFieldValue(field, desiredValue);
    const exact = projectFieldValuesEqual(currentValue, expectedValue);
    return Object.freeze({
      fieldName,
      fieldId: field.id,
      field,
      action: exact ? MANAGED_FIELD_ACTIONS.NO_OP : MANAGED_FIELD_ACTIONS.SET,
      currentValue: currentValue ?? PROJECT_FIELD_UNSET,
      expectedValue,
      desiredValue,
      reason: exact ? 'ALREADY_EXACT' : 'DESIRED_VALUE_DIFFERS',
    });
  });
}

export function buildExpectedFinalFieldValues({ currentValues, actions }) {
  assert(currentValues instanceof Map, 'currentValues must be a Map');
  assert(Array.isArray(actions), 'managed field actions must be an array');
  const managedNames = new Set(actions.map((action) => action.fieldName));
  assert(managedNames.size === actions.length, 'managed field actions must have unique field names');
  const expected = new Map([...currentValues].filter(([fieldName]) => !managedNames.has(fieldName)));
  for (const action of actions) {
    if (action.expectedValue.kind !== 'UNSET') expected.set(action.fieldName, action.expectedValue);
  }
  return expected;
}

function countManagedFieldActions(actions) {
  const counts = { SET: 0, CLEAR: 0, NO_OP: 0 };
  for (const action of actions) {
    assert(Object.values(MANAGED_FIELD_ACTIONS).includes(action.action),
      `unsupported managed field action ${action.action}`);
    counts[action.action] += 1;
  }
  return counts;
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

export async function clearProjectField(api, projectId, itemId, field) {
  assert(field?.id && field?.name, 'clear Project field identity is required');
  assert(ALLOWED_FIELD_TYPES.has(field.dataType), `unsupported project field type ${field.dataType}`);
  const data = await api.request(`
    mutation ClearRoadmapItemField($input: ClearProjectV2ItemFieldValueInput!) {
      clearProjectV2ItemFieldValue(input: $input) { projectV2Item { id } }
    }
  `, { input: { projectId, itemId, fieldId: field.id } }, `clear ${field.name}`);
  const returnedItemId = data.clearProjectV2ItemFieldValue?.projectV2Item?.id;
  assert(returnedItemId === itemId,
    `clear ${field.name} returned unexpected Project item identity ${returnedItemId ?? 'missing'}`);
}

export async function planRepositoryItems(api, config, project, reconciledAt) {
  const [owner, name] = config.repository.split('/');
  const [issues, pullRequests, existingItems] = await Promise.all([
    listRepositoryIssues(api, owner, name),
    listRepositoryPullRequests(api, owner, name),
    listProjectItems(api, project.id),
  ]);
  const repositoryAliases = configuredRepositoryAliases(config);
  validateProjectItemPreconditions({
    items: existingItems,
    contents: [...issues, ...pullRequests],
    repository: config.repository,
    repositoryAliases,
  });
  const issuesByNumber = new Map(issues.map((issue) => [issue.number, issue]));
  const repositoryItems = existingItems.filter((item) => (
    isConfiguredRepository(item.content?.repository?.nameWithOwner, config.repository, repositoryAliases)
  ));
  const plan = buildItemReconciliationPlan({
    contents: [...issues, ...pullRequests],
    existingItems: repositoryItems,
    retention: config.itemRetention,
    reconciledAt,
  });
  const configuredFieldsByName = new Map(config.fields.map((field) => [field.name, field]));
  const metadataByContentId = new Map();
  for (const content of [...issues, ...pullRequests]) {
    const metadata = deriveMetadata(content, issuesByNumber, config.repository, repositoryAliases);
    validateMetadataAgainstConfig(metadata, configuredFieldsByName);
    metadataByContentId.set(content.id, metadata);
  }
  return {
    issues,
    pullRequests,
    projectItems: existingItems,
    repositoryItems,
    metadataByContentId,
    plan,
  };
}

function rebindItemFromLatestProjection({ item, content, items, repository, repositoryAliases = [] }) {
  if (!item?.id) return item;
  const indexed = itemProjectionIndex(items);
  assert(!indexed.error, indexed.error);
  const latest = indexed.byItemId.get(item.id);
  if (!latest) {
    const replacement = indexed.byContentId.get(content.id);
    const reason = replacement ? 'CONTENT_IDENTITY_DRIFT' : 'ITEM_DELETED';
    throw new Error(`PROJECT_HARD_DRIFT operation=PRE_MUTATION_ITEM_BINDING identity=${content.kind} #${content.number} reason=${reason} expected=Project item ${item.id} observed=${replacement ? `replacement Project item ${replacement.id}` : 'item absent'}`);
  }
  if (latest.content?.id !== content.id
    || latest.content?.__typename !== content.kind
    || latest.content?.number !== content.number
    || !isConfiguredRepository(latest.content?.repository?.nameWithOwner, repository, repositoryAliases)) {
    throw new Error(`PROJECT_HARD_DRIFT operation=PRE_MUTATION_ITEM_BINDING identity=${content.kind} #${content.number} reason=CONTENT_IDENTITY_DRIFT expected=captured repository content observed=Project item ${item.id} identity changed`);
  }
  return latest;
}

export function evaluateFinalItemProjection({
  items,
  config,
  beforeValuesByContentId,
  expectedValuesByContentId,
  fieldActionsByContentId,
  itemIdByContentId,
  plan,
}) {
  const expectedTotal = plan.operations.length;
  const indexed = itemProjectionIndex(items);
  if (indexed.error) {
    return verificationResult(
      PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
      'DUPLICATE_CONTENT',
      `plannedItems=${expectedTotal} exact repository projection`,
      indexed.error,
    );
  }
  const plannedContentIds = new Set(plan.operations.map((operation) => operation.content.id));
  const contentIdByItemId = new Map([...itemIdByContentId].map(([contentId, itemId]) => [itemId, contentId]));
  const operationByContentId = new Map(plan.operations.map((operation) => [operation.content.id, operation]));
  const expectedProjectItemCount = plan.operations.filter((operation) => operation.action !== 'SKIP').length;
  if (items.length > expectedProjectItemCount) {
    return verificationResult(
      PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
      'UNEXPECTED_ITEM',
      `projectItems=${expectedProjectItemCount}`,
      `projectItems=${items.length}`,
    );
  }
  let delayedReason;
  let delayedObserved;
  const markDelayed = (reason, observed) => {
    delayedReason ??= reason;
    delayedObserved ??= observed;
  };
  for (const item of items) {
    if (!item.content?.id || !item.content?.repository?.nameWithOwner) {
      const expectedContentId = contentIdByItemId.get(item.id);
      if (expectedContentId && operationByContentId.get(expectedContentId)?.action === 'ADD') {
        markDelayed('ITEM_NOT_VISIBLE', `new Project item=${item.id} content identity is partial`);
        continue;
      }
      return verificationResult(
        PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
        'CONTENT_IDENTITY_DRIFT',
        `plannedItems=${expectedTotal} complete content identities`,
        'Project contains an incompletely hydrated content identity',
      );
    }
    if (!isConfiguredRepository(item.content.repository.nameWithOwner, config.repository, configuredRepositoryAliases(config))
      || !plannedContentIds.has(item.content.id)) {
      return verificationResult(
        PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
        'UNEXPECTED_ITEM',
        `plannedItems=${expectedTotal} only configured repository content`,
        `unexpected repository item=${item.id}`,
      );
    }
  }

  const verified = { active: 0, archived: 0, absent: 0 };
  for (const operation of plan.operations) {
    const actual = indexed.byContentId.get(operation.content.id);
    if (operation.action === 'SKIP') {
      if (actual) {
        return verificationResult(
          PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
          'UNEXPECTED_ITEM',
          `${operation.content.kind} #${operation.content.number} absent`,
          observedItemSummary(actual),
        );
      }
      verified.absent += 1;
      continue;
    }
    if (!actual && operation.action === 'ADD') {
      markDelayed('ITEM_NOT_VISIBLE', `${operation.content.kind} #${operation.content.number} item=absent`);
      continue;
    }
    if (!actual
      || actual.content.__typename !== operation.content.kind
      || actual.content.number !== operation.content.number
      || !isConfiguredRepository(actual.content.repository.nameWithOwner, config.repository, configuredRepositoryAliases(config))) {
      return verificationResult(
        PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
        actual ? 'CONTENT_IDENTITY_DRIFT' : 'ITEM_DELETED',
        `${operation.content.kind} #${operation.content.number} exact identity`,
        observedItemSummary(actual),
      );
    }
    if (actual.id !== itemIdByContentId.get(operation.content.id)) {
      return verificationResult(
        PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
        'CONTENT_IDENTITY_DRIFT',
        `${operation.content.kind} #${operation.content.number} stable Project item identity`,
        `${operation.content.kind} #${operation.content.number} item identity was replaced`,
      );
    }
    const expectedArchived = operation.action === 'ARCHIVE' || operation.action === 'KEEP_ARCHIVED';
    if (actual.isArchived !== expectedArchived) {
      if (typeof operation.item?.isArchived === 'boolean'
        && actual.isArchived === operation.item.isArchived
        && actual.isArchived !== expectedArchived) {
        markDelayed('ARCHIVE_STATE_NOT_VISIBLE', observedItemSummary(actual));
        continue;
      }
      return verificationResult(
        PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
        'UNEXPECTED_LIFECYCLE_STATE',
        expectedItemSummary(operation.content, expectedArchived),
        observedItemSummary(actual),
      );
    }
    if (expectedArchived) {
      verified.archived += 1;
      continue;
    }
    const inspectedValues = inspectCurrentValueMap(actual.fieldValues);
    if (inspectedValues.error) {
      return verificationResult(
        PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
        'UNEXPECTED_FIELD_VALUE',
        `${operation.content.kind} #${operation.content.number} exact managed fields`,
        inspectedValues.error,
      );
    }
    const expectedValues = expectedValuesByContentId.get(operation.content.id);
    const beforeValues = beforeValuesByContentId.get(operation.content.id);
    const fieldActions = fieldActionsByContentId.get(operation.content.id);
    if (!expectedValues || !beforeValues || !fieldActions) {
      return verificationResult(
        PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
        'UNEXPECTED_FIELD_VALUE',
        `${operation.content.kind} #${operation.content.number} captured field contract`,
        `${operation.content.kind} #${operation.content.number} field contract missing`,
      );
    }
    const actionByFieldName = new Map(fieldActions.map((action) => [action.fieldName, action]));
    for (const [fieldName, actualValue] of inspectedValues.map) {
      if (expectedValues.has(fieldName)) continue;
      const action = actionByFieldName.get(fieldName);
      if (action?.action === MANAGED_FIELD_ACTIONS.CLEAR
        && projectFieldValueStillClearing(actualValue, beforeValues.get(fieldName))) {
        markDelayed('FIELD_NOT_VISIBLE', `${operation.content.kind} #${operation.content.number} field=${fieldName} clear not visible`);
      } else {
        return verificationResult(
          PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
          'UNEXPECTED_FIELD_VALUE',
          `${operation.content.kind} #${operation.content.number} exact supported-field set`,
          `${operation.content.kind} #${operation.content.number} unexpected field=${fieldName}`,
        );
      }
    }
    for (const [fieldName, expectedValue] of expectedValues) {
      const actualValue = inspectedValues.map.get(fieldName);
      if (projectFieldValuesEqual(actualValue, expectedValue)) continue;
      const action = actionByFieldName.get(fieldName);
      if (action?.action === MANAGED_FIELD_ACTIONS.SET
        && projectFieldValueStillProjecting(actualValue, beforeValues.get(fieldName), expectedValue)) {
        markDelayed('FIELD_NOT_VISIBLE', `${operation.content.kind} #${operation.content.number} field=${fieldName} not visible`);
        continue;
      }
      return verificationResult(
        PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
        'UNEXPECTED_FIELD_VALUE',
        `${operation.content.kind} #${operation.content.number} field=${fieldName} configured value`,
        `${operation.content.kind} #${operation.content.number} field=${fieldName} missing or contradictory`,
      );
    }
    verified.active += 1;
  }
  if (delayedReason) {
    return verificationResult(
      PROJECT_VERIFICATION_OUTCOMES.NOT_YET_CONVERGED,
      delayedReason,
      `plannedItems=${expectedTotal} exact repository projection`,
      delayedObserved,
    );
  }
  if (items.length !== expectedProjectItemCount) {
    return verificationResult(
      PROJECT_VERIFICATION_OUTCOMES.HARD_DRIFT,
      'ITEM_DELETED',
      `projectItems=${expectedProjectItemCount}`,
      `projectItems=${items.length}`,
    );
  }
  return verificationResult(
    PROJECT_VERIFICATION_OUTCOMES.CONVERGED,
    'EXACT_STATE_VISIBLE',
    `plannedItems=${expectedTotal} exact repository projection`,
    `active=${verified.active} archived=${verified.archived} absent=${verified.absent}`,
    { verified },
  );
}

export async function reconcileItems(
  api,
  config,
  project,
  fieldsByName,
  itemState,
  verificationOptions = {},
) {
  const {
    issues,
    pullRequests,
    projectItems,
    repositoryItems,
    metadataByContentId,
    plan,
  } = itemState;
  assert(Array.isArray(config.fields), 'configured Project fields are required for item reconciliation');
  const executed = { added: 0, archived: 0, unarchived: 0 };
  let fieldUpdates = 0;
  let fieldClears = 0;
  const plannedFieldActions = { SET: 0, CLEAR: 0, NO_OP: 0 };
  let lastObservedItems = projectItems ?? repositoryItems;
  const knownItemsById = new Map(lastObservedItems.map((item) => [item.id, {
    contentId: item.content.id,
    kind: item.content.__typename,
    number: item.content.number,
    repository: item.content.repository.nameWithOwner,
  }]));
  const projectionLagEligibleItemIds = new Set();
  const itemIdByContentId = new Map(plan.operations
    .filter((operation) => operation.item?.id)
    .map((operation) => [operation.content.id, operation.item.id]));
  const beforeValuesByContentId = new Map();
  const expectedValuesByContentId = new Map();
  const fieldActionsByContentId = new Map();
  for (const operation of plan.operations) {
    const { content } = operation;
    const metadata = metadataByContentId.get(content.id);
    assert(metadata, `prevalidated metadata is missing for ${content.kind} #${content.number}`);
    validateMetadataAgainstConfig(metadata, fieldsByName);
    let { item } = operation;
    item = rebindItemFromLatestProjection({
      item,
      content,
      items: lastObservedItems,
      repository: config.repository,
      repositoryAliases: configuredRepositoryAliases(config),
    });
    if (operation.action === 'ADD') {
      const data = await api.request(`
        mutation AddRoadmapItem($input: AddProjectV2ItemByIdInput!) {
          addProjectV2ItemById(input: $input) { item { id } }
        }
      `, { input: { projectId: project.id, contentId: content.id } }, `add ${content.kind} #${content.number}`);
      const itemId = data.addProjectV2ItemById.item?.id;
      assert(itemId, `add ${content.kind} #${content.number} returned no Project item identity`);
      assert(!knownItemsById.has(itemId), `add ${content.kind} #${content.number} reused Project item identity ${itemId}`);
      knownItemsById.set(itemId, {
        contentId: content.id,
        kind: content.kind,
        number: content.number,
        repository: config.repository,
      });
      projectionLagEligibleItemIds.add(itemId);
      executed.added += 1;
      const projection = await verifyProjectProjection({
        ...verificationOptions,
        operationType: 'ADD_ITEM',
        identity: `${content.kind} #${content.number}`,
        read: () => listProjectItems(api, project.id),
        classify: (items) => evaluateItemLifecycleProjection({
          items,
          targetItemId: itemId,
          content,
          repository: config.repository,
          repositoryAliases: configuredRepositoryAliases(config),
          expectedArchived: false,
          beforeArchived: undefined,
          allowMissing: true,
          knownItemsById,
          projectionLagEligibleItemIds,
        }),
      });
      lastObservedItems = projection.snapshot;
      item = projection.snapshot.find((candidate) => candidate.id === itemId);
      itemIdByContentId.set(content.id, itemId);
    } else if (operation.action === 'UNARCHIVE') {
      const beforeArchived = item.isArchived;
      await api.request(`
        mutation UnarchiveRoadmapItem($input: UnarchiveProjectV2ItemInput!) {
          unarchiveProjectV2Item(input: $input) { item { id } }
        }
      `, { input: { projectId: project.id, itemId: item.id } }, `unarchive ${content.kind} #${content.number}`);
      executed.unarchived += 1;
      const projection = await verifyProjectProjection({
        ...verificationOptions,
        operationType: 'UNARCHIVE_ITEM',
        identity: `${content.kind} #${content.number}`,
        read: () => listProjectItems(api, project.id),
        classify: (items) => evaluateItemLifecycleProjection({
          items,
          targetItemId: item.id,
          content,
          repository: config.repository,
          repositoryAliases: configuredRepositoryAliases(config),
          expectedArchived: false,
          beforeArchived,
          allowMissing: false,
          knownItemsById,
          projectionLagEligibleItemIds,
        }),
      });
      lastObservedItems = projection.snapshot;
      item = projection.snapshot.find((candidate) => candidate.id === item.id);
    } else if (operation.action === 'ARCHIVE') {
      const beforeArchived = item.isArchived;
      await api.request(`
        mutation ArchiveRoadmapItem($input: ArchiveProjectV2ItemInput!) {
          archiveProjectV2Item(input: $input) { item { id } }
        }
      `, { input: { projectId: project.id, itemId: item.id } }, `archive ${content.kind} #${content.number}`);
      executed.archived += 1;
      const projection = await verifyProjectProjection({
        ...verificationOptions,
        operationType: 'ARCHIVE_ITEM',
        identity: `${content.kind} #${content.number}`,
        read: () => listProjectItems(api, project.id),
        classify: (items) => evaluateItemLifecycleProjection({
          items,
          targetItemId: item.id,
          content,
          repository: config.repository,
          repositoryAliases: configuredRepositoryAliases(config),
          expectedArchived: true,
          beforeArchived,
          allowMissing: false,
          knownItemsById,
          projectionLagEligibleItemIds,
        }),
      });
      lastObservedItems = projection.snapshot;
      continue;
    } else if (operation.action === 'KEEP_ARCHIVED' || operation.action === 'SKIP') {
      continue;
    }

    assert(item?.id, `${operation.action} ${content.kind} #${content.number} did not produce an active Project item`);
    let current = currentValueMap(item.fieldValues);
    const capturedValues = new Map(current);
    const fieldActions = planManagedFieldReconciliation({
      configuredFields: config.fields,
      fieldsByName,
      metadata,
      currentValues: capturedValues,
    });
    const actionCounts = countManagedFieldActions(fieldActions);
    for (const action of Object.keys(plannedFieldActions)) plannedFieldActions[action] += actionCounts[action];
    beforeValuesByContentId.set(content.id, capturedValues);
    fieldActionsByContentId.set(content.id, fieldActions);
    expectedValuesByContentId.set(content.id, buildExpectedFinalFieldValues({
      currentValues: capturedValues,
      actions: fieldActions,
    }));

    for (const action of fieldActions) {
      if (action.action === MANAGED_FIELD_ACTIONS.NO_OP) continue;
      const currentValue = current.get(action.fieldName);
      if (action.currentValue.kind === 'UNSET') {
        assert(currentValue === undefined,
          `${content.kind} #${content.number} field ${action.fieldName} changed before its planned mutation`);
      } else {
        assert(projectFieldValuesEqual(currentValue, action.currentValue),
          `${content.kind} #${content.number} field ${action.fieldName} changed before its planned mutation`);
      }

      if (action.action === MANAGED_FIELD_ACTIONS.SET) {
        const fieldWasMutated = await setProjectField(
          api,
          project.id,
          item.id,
          action.field,
          action.desiredValue,
          currentValue,
        );
        if (!fieldWasMutated) {
          assert(currentValue?.kind === 'SINGLE_SELECT'
            && action.expectedValue.kind === 'SINGLE_SELECT'
            && currentValue.fieldId === action.expectedValue.fieldId
            && currentValue.optionId === action.expectedValue.optionId,
          `${content.kind} #${content.number} field ${action.fieldName} has contradictory non-mutated value`);
        } else {
          fieldUpdates += 1;
        }
        const expectedValues = new Map(current);
        expectedValues.set(action.fieldName, action.expectedValue);
        const projection = await verifyProjectProjection({
          ...verificationOptions,
          operationType: fieldWasMutated ? 'UPDATE_ITEM_FIELD' : 'VERIFY_ITEM_FIELD',
          identity: `${content.kind} #${content.number} field=${action.fieldName}`,
          read: () => listProjectItems(api, project.id),
          classify: (items) => evaluateItemFieldProjection({
            items,
            targetItemId: item.id,
            content,
            repository: config.repository,
            repositoryAliases: configuredRepositoryAliases(config),
            beforeValues: current,
            expectedValues,
            allowMissing: operation.action === 'ADD',
            knownItemsById,
            projectionLagEligibleItemIds,
          }),
        });
        lastObservedItems = projection.snapshot;
        item = projection.snapshot.find((candidate) => candidate.id === item.id);
        current = currentValueMap(item.fieldValues);
        continue;
      }

      assert(action.action === MANAGED_FIELD_ACTIONS.CLEAR,
        `unsupported managed field action ${action.action}`);
      await clearProjectField(api, project.id, item.id, action.field);
      fieldClears += 1;
      const projection = await verifyProjectProjection({
        ...verificationOptions,
        operationType: 'CLEAR_ITEM_FIELD',
        identity: `${content.kind} #${content.number} field=${action.fieldName}`,
        read: () => listProjectItems(api, project.id),
        classify: (items) => evaluateItemFieldClearProjection({
          items,
          targetItemId: item.id,
          content,
          repository: config.repository,
          repositoryAliases: configuredRepositoryAliases(config),
          beforeValues: current,
          field: action.field,
          knownItemsById,
          projectionLagEligibleItemIds,
        }),
      });
      lastObservedItems = projection.snapshot;
      item = projection.snapshot.find((candidate) => candidate.id === item.id);
      current = currentValueMap(item.fieldValues);
    }
  }
  let useCapturedSnapshot = true;
  const finalProjection = await verifyProjectProjection({
    ...verificationOptions,
    operationType: 'FINAL_ITEM_AUDIT',
    identity: `Project #${project.number}`,
    read: async () => {
      if (useCapturedSnapshot) {
        useCapturedSnapshot = false;
        return lastObservedItems;
      }
      return listProjectItems(api, project.id);
    },
    classify: (items) => evaluateFinalItemProjection({
      items,
      config,
      beforeValuesByContentId,
      expectedValuesByContentId,
      fieldActionsByContentId,
      itemIdByContentId,
      plan,
    }),
  });
  const finalVerification = finalProjection.result;
  const verified = finalVerification.details.verified;
  return {
    observed: {
      issues: issues.length,
      pullRequests: pullRequests.length,
      existingRepositoryItems: repositoryItems.length,
    },
    planned: plan.counts,
    reasonCounts: plan.reasonCounts,
    baselines: plan.baselines,
    managedFieldActions: {
      planned: plannedFieldActions,
      verified: { ...plannedFieldActions },
    },
    executed: { ...executed, fieldUpdates, fieldClears },
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
      repositoryAliases: configuredRepositoryAliases(config),
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
